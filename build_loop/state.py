"""실증용 로컬 상태·사건의 원자적 저장. 제품 전체 워크플로우는 아직 구현 전."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import unicodedata
import secrets
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

    def pause(self, expected_revision: int, reason="user"):
        with self._write(expected_revision) as (db, state):
            db.execute("UPDATE state SET paused=1 WHERE id=1")
            from .confirmation import question
            return self._event(db, state, "paused", {"reason": reason, "question": question("중단된 작업 재개", "저장 상태", secrets.token_hex(12))})

    def resume_selection(self, path, session, call, expected_revision):
        from .confirmation import selected
        pauses = [json.loads(e["payload"]) for e in self.snapshot()["events"] if e["kind"] == "paused"]
        if not pauses:
            raise StateError("중단 기록이 없습니다.")
        receipt = selected(pauses[-1], path, session, call)
        with self._write(expected_revision) as (db, state):
            if not state["paused"]:
                raise StateError("이미 재개된 상태입니다.")
            db.execute("UPDATE state SET paused=0 WHERE id=1")
            return self._event(db, state, "resumed", {"source": receipt})

    def begin_recovery(self, identity: dict, expected_revision: int):
        keys = {"project_id", "task_id", "check_id", "failure_category", "environment_id"}
        if set(identity) != keys or any(not isinstance(v, str) or not v.strip() for v in identity.values()):
            raise StateError("complete error identity is required")
        identity = {k: unicodedata.normalize("NFC", v) for k, v in identity.items()}
        fingerprint = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        with self._write(expected_revision) as (db, state):
            if state["paused"]:
                raise StateError("user stopped; no automatic recovery")
            base = fingerprint
            cycle = sum(json.loads(row[0]).get("base") == base for row in db.execute("SELECT payload FROM events WHERE kind='recovery_resolved'"))
            cycle += sum(json.loads(row[0]).get("base") == base for row in db.execute("SELECT payload FROM events WHERE kind='recovery_retry_authorized'"))
            if cycle:
                fingerprint = hashlib.sha256(f"{base}:{cycle}".encode()).hexdigest()
            count = db.execute("SELECT count(*) FROM attempts WHERE fingerprint=?", (fingerprint,)).fetchone()[0]
            if count >= 3:
                raise RecoveryExhausted("same unresolved error reached three recovery attempts")
            db.execute("INSERT INTO attempts(fingerprint,ordinal,result) VALUES(?,?,'unknown')", (fingerprint, count + 1))
            return self._event(db, state, "recovery_started", {"fingerprint": fingerprint, "base": base,
                                                               "identity": identity, "attempt": count + 1})

    def resolve_recovery(self, identity, token, expected_revision):
        base = hashlib.sha256(json.dumps({k: unicodedata.normalize("NFC", v) for k, v in identity.items()}, sort_keys=True).encode()).hexdigest()
        with self._write(expected_revision) as (db, state):
            checks = [json.loads(row[0]) for row in db.execute("SELECT payload FROM events WHERE kind='task_check_finished'")]
            proof = next((p for p in reversed(checks) if p.get("token") == token), None)
            if not proof or proof["result"] not in {"passed", "failed"} or proof["task"]["id"] != identity["task_id"] or proof["task"]["environment"] != identity["environment_id"]:
                raise StateError("해당 오류의 실제 검증 결과 증거가 필요합니다.")
            starts = [json.loads(row[0]) for row in db.execute("SELECT payload FROM events WHERE kind='recovery_started'")]
            start = next((p for p in reversed(starts) if p.get("base") == base), None)
            resolved = [json.loads(row[0]) for row in db.execute("SELECT payload FROM events WHERE kind='recovery_resolved'")]
            if not start or any(p.get("token") == token or p.get("fingerprint") == start["fingerprint"] for p in resolved):
                return state["revision"]
            db.execute("UPDATE attempts SET result=? WHERE fingerprint=? AND ordinal=?", (proof["result"], start["fingerprint"], start["attempt"]))
            return self._event(db, state, "recovery_resolved" if proof["result"] == "passed" else "recovery_checked", {
                "base": base, "fingerprint": start["fingerprint"], "token": token, "proof_code": proof["code"]})


def require_native_capabilities(evidence: dict):
    required = {"selection_ui", "verified_user_source", "guard_enforcement", "resume"}
    missing = sorted(k for k in required if evidence.get(k) != "passed")
    if missing:
        raise StateError("native integration remains unverified: " + ", ".join(missing))

