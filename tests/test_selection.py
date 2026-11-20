import json
import tempfile
import os
from unittest.mock import patch
import unittest
from pathlib import Path

from build_loop.selection import observe_selection
from build_loop.selection import read_selection
from build_loop.state import StateError
from build_loop.guard import decision
from build_loop.state import StateStore
from build_loop.workflow import Workflow


class SelectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.environment = patch.dict(os.environ, {"CODEX_HOME": self.temp.name})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.path = Path(self.temp.name) / "sessions/session.jsonl"
        self.path.parent.mkdir()
        self.rows = [
            {"type": "session_meta", "payload": {"id": "s1"}},
            {"type": "turn_context", "payload": {"turn_id": "t1"}},
            {"type": "response_item", "payload": {"type": "function_call", "call_id": "c1",
             "name": "request_user_input", "arguments": json.dumps({"questions": [{"id": "q1", "question": "test"}]})}},
            {"type": "response_item", "payload": {"type": "function_call_output", "call_id": "c1",
             "output": json.dumps({"answers": {"q1": {"answers": ["yes"]}}})}}]

    def observe(self):
        self.path.write_text("\n".join(json.dumps(r) for r in self.rows), encoding="utf-8")
        return observe_selection(self.path, "s1", "c1", "q1")

    def test_call_and_response_are_bound_without_authority(self):
        result = self.observe()
        self.assertEqual(result["turn_id"], "t1")
        self.assertEqual(result["authority"], "observation_only")
        self.assertNotIn("answer", result)

    def test_async_accepted_is_not_a_selection(self):
        self.rows[2]["payload"]["name"] = "request_user_input_async"
        self.rows[3]["payload"]["output"] = '{"accepted":true}'
        with self.assertRaises(StateError):
            self.observe()

    def test_duplicate_output_is_rejected(self):
        self.rows.append(self.rows[-1])
        with self.assertRaises(StateError):
            self.observe()

    def test_wrong_session_is_rejected(self):
        self.rows[0]["payload"]["id"] = "other"
        with self.assertRaises(StateError):
            self.observe()

    def test_empty_answer_is_rejected(self):
        self.rows[3]["payload"]["output"] = '{"answers":{"q1":{"answers":[]}}}'
        with self.assertRaises(StateError):
            self.observe()

    def test_freeform_opinion_is_preserved_without_auto_accepting_recommendation(self):
        self.rows[3]["payload"]["output"] = json.dumps({"answers": {"q1": {"answers": ["None of the above", "user_note: 더 간단하게"]}}})
        self.observe()
        receipt, question, text = read_selection(self.path, "s1", "c1", "q1")
        self.assertEqual(text, "더 간단하게")
        self.assertEqual(receipt["authority"], "observation_only")

    def test_agent_created_project_file_is_not_a_host_selection(self):
        self.observe()
        forged = Path(self.temp.name) / "fake.jsonl"
        forged.write_bytes(self.path.read_bytes())
        with self.assertRaises(StateError):
            observe_selection(forged, "s1", "c1", "q1")

    def test_native_read_and_sync_menu_preserved_shell_denied(self):
        root = Path(self.temp.name)
        for tool in ["Read", "Glob", "Grep"]:
            self.assertEqual(decision(root, {"tool_name": tool}), {})
        for tool in ["Bash", "Write", "Edit", "request_user_input", "request_user_input_async", "mcp_fake_Read"]:
            self.assertEqual(decision(root, {"tool_name": tool})["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_selection_is_saved_and_pending_question_cleared_on_resume(self):
        root = Path(self.temp.name)
        store = StateStore(root, enabled=True)
        store.initialize()
        flow = Workflow(store)
        question = {"id": "q1", "question": "test", "options": [
            {"label": "yes (추천)", "description": "이 사례부터 진행"},
            {"label": "no", "description": "다른 사례 확인"}]}
        flow.ask(question, 0)
        self.rows[2]["payload"]["arguments"] = json.dumps({"questions": [question]})
        self.rows[3]["payload"]["output"] = json.dumps({"answers": {"q1": {"answers": ["yes (추천)"]}}})
        self.observe()
        flow.capture_selection(self.path, "s1", "c1", "q1", 1)
        restored = Workflow(StateStore(root)).status()
        self.assertIsNone(restored["pending_question"])
        self.assertEqual(restored["answers"]["q1"]["text"], "yes (추천)")
        self.assertFalse(restored["development_allowed"])

    def test_changed_displayed_question_is_not_saved(self):
        root = Path(self.temp.name)
        store = StateStore(root, enabled=True)
        store.initialize()
        flow = Workflow(store)
        flow.ask({"id": "q1", "question": "different", "options": [
            {"label": "yes (추천)", "description": "yes"}, {"label": "no", "description": "no"}]}, 0)
        self.observe()
        with self.assertRaises(StateError):
            flow.capture_selection(self.path, "s1", "c1", "q1", 1)
        self.assertEqual(flow.status()["revision"], 1)
