import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from contextlib import closing

from build_loop.state import (
    RecoveryExhausted, RevisionConflict, StateError, StateStore, require_native_capabilities,
)


IDENTITY = dict(project_id="p", task_id="t", check_id="check-1",
                failure_category="test_failed", environment_id="windows-cli")


class StateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="build-loop-")
        self.project = Path(self.temp.name)
        self.store = StateStore(self.project, enabled=True)
        self.store.initialize()

    def tearDown(self):
        self.temp.cleanup()

    def test_disabled_writes_create_nothing(self):
        other = self.project / "other"
        other.mkdir()
        store = StateStore(other)
        with self.assertRaises(StateError):
            store.initialize()
        self.assertFalse(store.path.exists())

    def test_reinitialize_preserves_existing_events(self):
        self.store.record_observation({"kind": "user_reply_observation"}, 0)
        self.store.initialize()
        self.assertEqual(self.store.snapshot()["revision"], 1)

    def test_revision_conflict_does_not_change_state(self):
        self.store.record_observation({"kind": "user_reply_observation"}, 0)
        before = self.store.snapshot()
        with self.assertRaises(RevisionConflict):
            self.store.pause(0)
        self.assertEqual(before, self.store.snapshot())

    def test_raw_transcript_is_not_accepted_as_observation(self):
        with self.assertRaises(StateError):
            self.store.record_observation({"kind": "user_reply_observation", "text": "not allowed"}, 0)
        self.assertEqual(self.store.snapshot()["revision"], 0)

    def test_observation_does_not_create_approval(self):
        self.store.record_observation({"kind": "user_reply_observation", "item_id": "i"}, 0)
        self.assertEqual([e["kind"] for e in self.store.snapshot()["events"]],
                         ["initialized", "user_reply_observed"])

    def test_recovery_limit_and_unknown_result_survive_new_store(self):
        for revision in range(3):
            self.store.begin_recovery(IDENTITY, revision)
        reopened = StateStore(self.project, enabled=True)
        self.assertEqual([a["result"] for a in reopened.snapshot()["attempts"]], ["unknown"] * 3)
        with self.assertRaises(RecoveryExhausted):
            reopened.begin_recovery(IDENTITY, 3)
        self.assertEqual(reopened.snapshot()["revision"], 3)

    def test_error_identity_rejects_message_in_place_of_stable_key(self):
        changed = dict(IDENTITY, message="reworded")
        with self.assertRaises(StateError):
            self.store.begin_recovery(changed, 0)
        self.assertEqual(len(self.store.snapshot()["attempts"]), 0)

    def test_pause_blocks_recovery_without_consuming_attempt(self):
        self.store.pause(0)
        with self.assertRaises(StateError):
            self.store.begin_recovery(IDENTITY, 1)
        self.assertEqual(self.store.snapshot()["attempts"], [])

    def test_event_failure_rolls_back_state_and_attempt(self):
        class BrokenStore(StateStore):
            @staticmethod
            def _event(db, state, kind, payload):
                db.execute("UPDATE state SET revision=999 WHERE id=1")
                raise RuntimeError("injected failure before commit")
        failing = BrokenStore(self.project, enabled=True)
        with self.assertRaises(RuntimeError):
            failing.begin_recovery(IDENTITY, 0)
        state = self.store.snapshot()
        self.assertEqual(state["revision"], 0)
        self.assertEqual(len(state["events"]), 1)
        self.assertEqual(state["attempts"], [])

    def test_foreign_database_is_not_reinitialized(self):
        other = self.project / "foreign"
        (other / ".build-loop").mkdir(parents=True)
        target = other / ".build-loop/state.sqlite"
        with closing(sqlite3.connect(target)) as db:
            db.execute("CREATE TABLE important(value)")
            db.execute("INSERT INTO important VALUES('keep')")
            db.commit()
        with self.assertRaises(StateError):
            StateStore(other, enabled=True).initialize()
        with closing(sqlite3.connect(target)) as db:
            self.assertEqual(db.execute("SELECT value FROM important").fetchone()[0], "keep")

    def test_lock_contention_fails_without_mutation(self):
        lock = sqlite3.connect(self.store.path, isolation_level=None)
        try:
            lock.execute("BEGIN IMMEDIATE")
            competing = StateStore(self.project, enabled=True, timeout=0.03)
            with self.assertRaises(sqlite3.OperationalError):
                competing.pause(0)
        finally:
            lock.execute("ROLLBACK")
            lock.close()
        self.assertEqual(self.store.snapshot()["revision"], 0)

    def test_force_terminated_uncommitted_write_is_rolled_back(self):
        program = (
            "import sqlite3,sys,time; "
            "db=sqlite3.connect(sys.argv[1],isolation_level=None); "
            "db.execute('BEGIN IMMEDIATE'); "
            "db.execute('UPDATE state SET revision=77 WHERE id=1'); "
            "print('uncommitted',flush=True); time.sleep(30)"
        )
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        proc = subprocess.Popen([sys.executable, "-u", "-c", program, str(self.store.path)],
                                stdout=subprocess.PIPE, text=True, creationflags=flags)
        try:
            self.assertEqual(proc.stdout.readline().strip(), "uncommitted")
            proc.terminate()
            proc.wait(timeout=5)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=5)
            proc.stdout.close()
        state = self.store.snapshot()
        self.assertEqual(state["revision"], 0)
        self.assertEqual(len(state["events"]), 1)

    def test_committed_write_is_readable_from_new_process(self):
        self.store.pause(0)
        program = "import sqlite3,sys; db=sqlite3.connect(sys.argv[1]); print(db.execute('SELECT revision,paused FROM state').fetchone()); db.close()"
        result = subprocess.run([sys.executable, "-c", program, str(self.store.path)],
                                capture_output=True, text=True, timeout=5, check=True)
        self.assertEqual(result.stdout.strip(), "(1, 1)")

    def test_missing_native_evidence_blocks_readiness(self):
        with self.assertRaises(StateError):
            require_native_capabilities({"selection_ui": "configured", "verified_user_source": "passed"})


if __name__ == "__main__":
    unittest.main()
