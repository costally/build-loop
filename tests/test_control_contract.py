"""실제 제어 프로세스에서 메뉴 질문과 중단/재개의 계약을 검증한다."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from build_loop.director import create_project, guide
from build_loop.harness import handle
from build_loop.policy import control_command, decision
from build_loop.state import StateStore
from build_loop.workflow import Workflow


class ControlContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = Path(__file__).resolve().parents[1]
        self.project = create_project(self.root, "web-notepad", "초간단 웹 메모장", self.source / "templates", enabled=True)
        self.store = StateStore(self.project, enabled=True)
        self.home = self.root / "host"
        self.session = self.home / "sessions/session.jsonl"
        self.session.parent.mkdir(parents=True)
        self.question = {"id": "primary_use_case", "header": "사용 상황", "question": "어떤 상황에서 쓰면 될까요?",
                         "options": [{"label": "개인 메모 한 장 (추천)", "description": "가장 작은 흐름"},
                                     {"label": "여러 메모 관리", "description": "목록에서 선택"}]}

    def control(self, data, expected=0):
        path = self.project / ".build-loop/requests/test-contract.json"
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        run = subprocess.run([sys.executable, str(self.source / "control.py"), "--project", str(self.project),
                              "--request", path.name], capture_output=True, text=True, encoding="utf-8", timeout=10,
                             env=dict(os.environ, CODEX_HOME=str(self.home)))
        self.assertEqual(run.returncode, expected, run.stdout + run.stderr)
        return json.loads(run.stdout)

    def record_selection(self, question, call):
        question = dict(question, isOther=True, isSecret=False)
        rows = [{"type": "session_meta", "payload": {"id": "session-1"}},
                {"type": "turn_context", "payload": {"turn_id": "turn-1"}},
                {"type": "response_item", "payload": {"type": "function_call", "call_id": call,
                    "name": "functions.request_user_input", "arguments": json.dumps({"questions": [question]})}},
                {"type": "response_item", "payload": {"type": "function_call_output", "call_id": call,
                    "output": json.dumps({"answers": {question["id"]: {"answers": [question["options"][0]["label"]]}}})}}]
        self.session.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
        return dict(session_file=str(self.session), session="session-1", call=call)

    def test_header_question_registers_menu_and_captures_exact_reply(self):
        view = self.control(dict(action="question", revision=1, question=self.question))
        self.assertEqual(view["stage"], "interview")
        self.assertEqual(view["question"], self.question)
        payload = {"tool_name": "request_user_input", "tool_input": {"questions": [view["question"]]}}
        self.assertEqual(decision(self.project, payload), {})
        receipt = self.record_selection(view["question"], "question-call")
        answer = self.control(dict(action="selection", revision=view["revision"], question=self.question["id"], **receipt))
        self.assertEqual(answer["stage"], "brief")
        self.assertEqual(answer["accepted_answers"][self.question["id"]]["text"], self.question["options"][0]["label"])

    def test_saved_wrapped_question_is_normalized(self):
        view = self.control(dict(action="question", revision=1, question={"questions": [self.question]}))
        self.assertEqual(view["question"], self.question)

    def test_previous_hook_record_recovers_real_selection_after_pause(self):
        view = self.control(dict(action="question", revision=1, question=self.question))
        self.record_selection(self.question, "first-call")
        first_rows = self.session.read_text(encoding="utf-8").splitlines()
        request = dict(action="selection", revision=view["revision"], question=self.question["id"])
        self.control(request, expected=2)
        self.assertEqual(self.store.snapshot()["revision"], view["revision"])

        host = dict(cwd=str(self.project), session_id="session-1")
        menu = dict(host, tool_name="request_user_input", tool_input={"questions": [self.question]})
        handle(self.project, dict(menu, hook_event_name="PostToolUse", tool_use_id="first-call"))
        paused = self.control(dict(action="external-block", revision=view["revision"], reason="older menu transport"))
        self.control(request | {"revision": paused["revision"]}, expected=2)
        self.assertTrue(self.store.snapshot()["paused"])

        self.record_selection(paused["question"], "resume-call")
        resume_rows = self.session.read_text(encoding="utf-8").splitlines()[1:]
        self.session.write_text("\n".join(first_rows + resume_rows), encoding="utf-8")
        resume = dict(host, tool_name="request_user_input", tool_input={"questions": [paused["question"]]},
                      tool_use_id="resume-call", transcript_path=str(self.session))
        self.assertEqual(handle(self.project, dict(resume, hook_event_name="PreToolUse")), {})
        handle(self.project, dict(resume, hook_event_name="PostToolUse"))
        resumed = self.control(dict(action="resume-selection", revision=paused["revision"]))
        self.assertEqual(resumed["question"], self.question)
        answer = self.control(request | {"revision": resumed["revision"]})
        self.assertEqual(answer["stage"], "brief")
        self.assertEqual(answer["accepted_answers"][self.question["id"]]["source"]["call_id"], "first-call")
        self.control(request | {"revision": answer["revision"]}, expected=2)

    def test_allowed_control_command_locates_legacy_menu_without_model_metadata(self):
        view = self.control(dict(action="question", revision=1, question=self.question))
        self.record_selection(self.question, "older-call")
        host = dict(cwd=str(self.project), session_id="session-1")
        handle(self.project, dict(host, hook_event_name="PostToolUse", tool_use_id="older-call",
                                  tool_name="request_user_input", tool_input={"questions": [self.question]}))
        payload = dict(host, hook_event_name="PreToolUse", tool_name="Bash",
                       tool_input={"command": control_command(self.project, "test-contract.json")},
                       transcript_path=str(self.session))
        self.assertEqual(handle(self.project, payload), {})
        confirmed = self.control(dict(action="selection", revision=view["revision"], question=self.question["id"]))
        self.assertEqual(confirmed["accepted_answers"][self.question["id"]]["text"], self.question["options"][0]["label"])

    def test_bad_shape_reports_contract_without_changing_revision(self):
        result = self.control(dict(action="question", revision=1, question=dict(self.question, text="wrong")), expected=2)
        self.assertIn("text", result["error"])
        self.assertTrue(Path(result["request_help"]).is_file())
        self.assertIn("question_example", result)
        self.assertEqual(self.store.snapshot()["revision"], 1)
        self.assertIsNone(Workflow(self.store).status()["pending_question"])

    def test_pause_allows_only_request_writes_then_verified_resume_and_question(self):
        self.store.pause(1, "external_block: question schema")
        view = guide(self.project)
        for name, allowed in [(".build-loop/requests/resume.json", True), ("docs/00-brief.md", False),
                              ("code/index.html", False), (".build-loop/state.sqlite", False)]:
            result = decision(self.project, {"tool_name": "apply_patch", "tool_input": {"command":
                f"*** Begin Patch\n*** Add File: {name}\n+{{}}\n*** End Patch"}})
            self.assertEqual(result == {}, allowed)
        self.assertEqual(decision(self.project, {"tool_name": "request_user_input", "tool_input": {
            "questions": [view["question"]]}}), {})
        receipt = self.record_selection(view["question"], "resume-call")
        resumed = self.control(dict(action="resume-selection", revision=view["revision"], **receipt))
        self.assertEqual(resumed["stage"], "brief")
        fresh = self.control(dict(action="question", revision=resumed["revision"], question=self.question))
        self.assertEqual(fresh["stage"], "interview")

    def test_native_resume_menu_metadata_is_not_a_question_change(self):
        self.store.pause(1, "external_block: schema")
        question = guide(self.project)["question"]
        native = dict(question, isOther=True, isSecret=False)
        for tool, arguments in [("request_user_input", {"questions": [native]}),
                                ("functionsrequest_user_input", {"questions": [native]}),
                                ("request_user_input", {"command": json.dumps({"questions": [native]})})]:
            self.assertEqual(decision(self.project, {"tool_name": tool, "tool_input": arguments}), {})
        for changed in [dict(native, id="other"), dict(native, question="different target"),
                        dict(native, isSecret=True), dict(native, options=list(reversed(native["options"])) )]:
            result = decision(self.project, {"tool_name": "request_user_input", "tool_input": {"questions": [changed]}})
            self.assertEqual(result["hookSpecificOutput"]["permissionDecision"], "deny")
