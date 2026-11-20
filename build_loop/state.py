"""실증용 로컬 상태·사건의 원자적 저장. 제품 전체 워크플로우는 아직 구현 전."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import unicodedata
from contextlib import closing, contextmanager
from pathlib import Path


class StateError(RuntimeError):
    pass


class RevisionConflict(StateError):
    pass


class RecoveryExhausted(StateError):
    pass


class StateStore:
    APPLICATION_ID = 0x424C4F50

    def __init__(self, project: Path, *, enabled: bool = False, timeout: float = 5):
        self.project = project.resolve(strict=True)
        if not self.project.is_dir():
            raise StateError("project must be a directory")
        self.enabled = enabled
        self.timeout = timeout
        directory = self.project / ".build-loop"
        if not directory.resolve().is_relative_to(self.project):
            raise StateError("state path escapes the selected project")
        self.path = directory / "state.sqlite"
        if not self.path.resolve().is_relative_to(self.project):
            raise StateError("database path escapes the selected project")

    def initialize(self):
        self._require_enabled()
        if self.path.exists():
            with closing(self._connect()) as db:
                self._validate(db)
            return
        self.path.parent.mkdir(exist_ok=True)
        with closing(self._connect()) as db:
            app = db.execute("PRAGMA application_id").fetchone()[0]
            tables = db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            if app or tables:
                raise StateError("existing database is not an empty build-loop database")
            db.executescript(f"""
                BEGIN IMMEDIATE;
                PRAGMA application_id={self.APPLICATION_ID};
                PRAGMA user_version=1;
                CREATE TABLE state(id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL,
                                   paused INTEGER NOT NULL CHECK(paused IN (0,1)));
                INSERT INTO state VALUES(1,0,0);
                CREATE TABLE events(revision INTEGER PRIMARY KEY, kind TEXT NOT NULL, payload TEXT NOT NULL);
                INSERT INTO events VALUES(0,'initialized','{{}}');
                CREATE TABLE attempts(id INTEGER PRIMARY KEY, fingerprint TEXT NOT NULL,
                                      ordinal INTEGER NOT NULL, result TEXT NOT NULL,
                                      UNIQUE(fingerprint,ordinal));
                COMMIT;
            """)

    def _require_enabled(self):
        if not self.enabled:
            raise StateError("experimental writes are disabled")

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=self.timeout, isolation_level=None)
        try:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA synchronous=FULL")
        except BaseException:
            db.close()
            raise
        return db

    def _validate(self, db):
        if db.execute("PRAGMA application_id").fetchone()[0] != self.APPLICATION_ID:
            raise StateError("unrecognized database; original data preserved")
        if db.execute("PRAGMA user_version").fetchone()[0] != 1:
            raise StateError("unsupported database version")
        if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise StateError("database integrity check failed")

    @contextmanager
    def _write(self, expected_revision):
        self._require_enabled()
        if not self.path.is_file():
            raise StateError("state database has not been initialized")
        db = self._connect()
        try:
            self._validate(db)
            db.execute("BEGIN IMMEDIATE")
            state = db.execute("SELECT * FROM state WHERE id=1").fetchone()
            if state["revision"] != expected_revision:
                raise RevisionConflict("stale revision; reload current state")
            yield db, dict(state)
            db.execute("COMMIT")
        except BaseException:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise
        finally:
            db.close()

    @staticmethod
    def _event(db, state, kind, payload):
        revision = state["revision"] + 1
        db.execute("UPDATE state SET revision=? WHERE id=1", (revision,))
        db.execute("INSERT INTO events VALUES(?,?,?)", (
            revision, kind, json.dumps(payload, ensure_ascii=False, sort_keys=True),
        ))
        return revision

    def snapshot(self):
        if not self.path.is_file():
            raise StateError("state database has not been initialized")
        db = self._connect()
        try:
            self._validate(db)
            db.execute("BEGIN")
            state = dict(db.execute("SELECT * FROM state WHERE id=1").fetchone())
            state["events"] = [dict(row) for row in db.execute("SELECT * FROM events ORDER BY revision")]
            state["attempts"] = [dict(row) for row in db.execute("SELECT * FROM attempts ORDER BY id")]
            db.execute("COMMIT")
            return state
        finally:
            db.close()

    def record_observation(self, payload: dict, expected_revision: int):
        # This is evidence of a read, never an approval or an accepted decision.
        allowed = {"thread_id", "turn_id", "item_id", "text_sha256", "transport", "kind"}
        if set(payload) - allowed or payload.get("kind") != "user_reply_observation":
            raise StateError("only sanitized user-reply observations may be recorded")
        with self._write(expected_revision) as (db, state):
            return self._event(db, state, "user_reply_observed", payload)

    def pause(self, expected_revision: int):
        with self._write(expected_revision) as (db, state):
            db.execute("UPDATE state SET paused=1 WHERE id=1")
            return self._event(db, state, "paused", {})

    def begin_recovery(self, identity: dict, expected_revision: int):
        keys = {"project_id", "task_id", "check_id", "failure_category", "environment_id"}
        if set(identity) != keys or any(not isinstance(v, str) or not v.strip() for v in identity.values()):
            raise StateError("complete error identity is required")
        identity = {k: unicodedata.normalize("NFC", v) for k, v in identity.items()}
        fingerprint = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        with self._write(expected_revision) as (db, state):
            if state["paused"]:
                raise StateError("user stopped; no automatic recovery")
            count = db.execute("SELECT count(*) FROM attempts WHERE fingerprint=?", (fingerprint,)).fetchone()[0]
            if count >= 3:
                raise RecoveryExhausted("same unresolved error reached three recovery attempts")
            db.execute("INSERT INTO attempts(fingerprint,ordinal,result) VALUES(?,?,'unknown')", (fingerprint, count + 1))
            return self._event(db, state, "recovery_started", {"fingerprint": fingerprint, "attempt": count + 1})


def require_native_capabilities(evidence: dict):
    required = {"selection_ui", "verified_user_source", "guard_enforcement", "resume"}
    missing = sorted(k for k in required if evidence.get(k) != "passed")
    if missing:
        raise StateError("native integration remains unverified: " + ", ".join(missing))

