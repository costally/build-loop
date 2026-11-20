import hashlib
import tempfile
import unittest
import subprocess
import sys
from pathlib import Path

from build_loop.state import StateStore, StateError, RevisionConflict
from build_loop.workflow import Workflow, manifest


def entry(kind="userMessage", text=""):
    return {"turnId": "turn-1", "item": {"id": "item-1", "type": kind,
            "content": [{"type": "text", "text": text}]}}


class FakeReadClient:
    def __init__(self, entries):
        self.entries = entries

    def call(self, method, params):
        if method == "thread/read":
            return {"thread": {"id": "thread-1"}}
        if method == "thread/turns/list":
            return {"data": [{"id": "turn-1"}]}
        if method == "thread/items/list":
            return {"data": self.entries}
        raise AssertionError(method)


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = StateStore(self.root, enabled=True)
        self.store.initialize()
        self.flow = Workflow(self.store)
        self.question = {"id": "Q-001", "question": "어떤 사례인가요?", "options": [
            {"label": "신규 (추천)", "description": "새 사례부터 확인"},
            {"label": "기존", "description": "기존 사례 연결"}]}

    def receipt(self, text="신규", item="i1"):
        return {"kind": "user_reply_observation", "transport": "codex_app_server_stdio",
                "thread_id": "t1", "turn_id": "u1", "item_id": item,
                "text_sha256": hashlib.sha256(text.encode()).hexdigest()}

    def answered(self):
        self.flow.ask(self.question, 0)
        self.flow.answer("Q-001", "신규", self.receipt(), 1)

    def test_reopen_restores_pending_and_answer(self):
        self.flow.ask(self.question, 0)
        self.assertEqual(Workflow(StateStore(self.root)).status()["pending_question"]["id"], "Q-001")
        self.flow.answer("Q-001", "신규", self.receipt(), 1)
        status = Workflow(StateStore(self.root)).status()
        self.assertIsNone(status["pending_question"])
        self.assertEqual(status["answers"]["Q-001"]["text"], "신규")
        self.assertFalse(status["development_allowed"])

    def test_missing_reply_and_wrong_hash_do_not_advance(self):
        self.flow.ask(self.question, 0)
        for receipt in (None, self.receipt("기존")):
            with self.assertRaises(StateError):
                self.flow.answer("Q-001", "신규", receipt, 1)
        self.assertEqual(self.flow.status()["revision"], 1)

    def test_pending_and_duplicate_question_blocked(self):
        self.flow.ask(self.question, 0)
        with self.assertRaises(StateError):
            self.flow.ask(self.question, 1)
        self.flow.answer("Q-001", "신규", self.receipt(), 1)
        with self.assertRaises(StateError):
            self.flow.ask(self.question, 2)

    def test_stale_session_cannot_answer(self):
        self.flow.ask(self.question, 0)
        with self.assertRaises(RevisionConflict):
            self.flow.answer("Q-001", "신규", self.receipt(), 0)

    def test_changed_and_deleted_spec_block(self):
        self.answered()
        spec = self.root / "spec.md"
        spec.write_text("v1", encoding="utf-8")
        self.flow.review("fixture-spec", "v1.0", ["spec.md"], 2)
        self.assertTrue(self.flow.status()["manifest_matches"])
        self.assertFalse(self.flow.status()["development_allowed"])
        spec.write_text("v2", encoding="utf-8")
        self.assertFalse(self.flow.status()["manifest_matches"])
        spec.unlink()
        self.assertFalse(self.flow.status()["manifest_matches"])

    def test_pause_blocks_write(self):
        self.store.pause(0)
        with self.assertRaises(StateError):
            self.flow.ask(self.question, 1)

    def test_manifest_cannot_escape_project(self):
        with self.assertRaises(StateError):
            manifest(self.root, [str(Path(__file__).resolve())])

    def test_answer_cannot_be_replayed_for_next_question(self):
        self.answered()
        self.question["id"] = "Q-002"
        self.flow.ask(self.question, 2)
        with self.assertRaises(StateError):
            self.flow.answer("Q-002", "신규", self.receipt(), 3)

    def prepare_review(self):
        self.answered()
        (self.root / "spec.md").write_text("spec-v1", encoding="utf-8")
        self.flow.review("fixture-spec", "v1.0", ["spec.md"], 2)
        return self.flow.status()["review"]["approval_text"]

    def test_approval_is_bound_but_not_authorized_before_host_validation(self):
        text = self.prepare_review()
        self.flow.observe_approval(FakeReadClient([entry(text=text)]), "thread-1", 3)
        view = Workflow(StateStore(self.root)).status()
        self.assertEqual(view["approval"]["version"], "v1.0")
        self.assertFalse(view["development_allowed"])
        self.assertEqual(view["next_action"], "호스트 승인 바인딩 실증")

    def test_assistant_and_generic_approval_cannot_bind(self):
        text = self.prepare_review()
        for candidate in [entry("agentMessage", text=text), entry(text="승인")]:
            with self.assertRaises(StateError):
                self.flow.observe_approval(FakeReadClient([candidate]), "thread-1", 3)
        self.assertEqual(self.flow.status()["revision"], 3)

    def test_old_challenge_cannot_approve_new_review(self):
        old = self.prepare_review()
        self.flow.review("fixture-spec", "v1.1", ["spec.md"], 3)
        with self.assertRaises(StateError):
            self.flow.observe_approval(FakeReadClient([entry(text=old)]), "thread-1", 4)

    def test_file_change_during_source_read_rolls_back(self):
        text = self.prepare_review()
        outer = self
        class ChangingClient(FakeReadClient):
            def call(self, method, params):
                if method == "thread/items/list":
                    (outer.root / "spec.md").write_text("changed", encoding="utf-8")
                return super().call(method, params)
        with self.assertRaises(StateError):
            self.flow.observe_approval(ChangingClient([entry(text=text)]), "thread-1", 3)
        self.assertIsNone(self.flow.status()["approval"])

    def test_independent_cli_outputs_utf8_and_restores_korean(self):
        self.answered()
        result = subprocess.run([sys.executable, "-m", "build_loop", "workflow", "--project",
                                 str(self.root), "status"], capture_output=True, check=True,
                                timeout=10, cwd=Path(__file__).resolve().parents[1])
        import json
        restored = json.loads(result.stdout.decode("utf-8"))
        self.assertEqual(restored["answers"]["Q-001"]["text"], "신규")
