"""전체 제어 경로 검사. 사용자 메뉴 전달은 모의이며 실제 UI 검증이 아니다."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from build_loop.director import create_project, guide, STAGES
from build_loop.state import StateStore, RecoveryExhausted
from build_loop.workflow import Workflow
from build_loop.delivery import Delivery
from build_loop.policy import decision, control_command
from build_loop.harness import handle


class ManagedFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="build-loop-flow-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = Path(__file__).resolve().parents[1]
        self.project = create_project(self.root, "managed", "integration fixture", self.source / "templates", enabled=True)
        self.store = StateStore(self.project, enabled=True)
        self.session = self.root / "host-home/sessions/simulated-session.jsonl"
        self.session.parent.mkdir(parents=True)
        self.rows = [{"type": "session_meta", "payload": {"id": "s1"}}]
        self.calls = 0

    def revision(self):
        return self.store.snapshot()["revision"]

    def select(self, question, answer=None):
        self.calls += 1
        call = f"call-{self.calls}"
        arguments = {"questions": [dict(question, isOther=True, isSecret=False)]}
        payload = {"cwd": str(self.project), "session_id": "s1", "transcript_path": str(self.session),
                   "tool_use_id": call, "tool_name": "request_user_input", "tool_input": arguments}
        self.assertEqual(handle(self.project, dict(payload, hook_event_name="PreToolUse")), {})
        self.rows += [{"type": "turn_context", "payload": {"turn_id": f"turn-{self.calls}"}},
                      {"type": "response_item", "payload": {"type": "function_call", "call_id": call,
                       "name": "request_user_input", "arguments": json.dumps(arguments)}},
                      {"type": "response_item", "payload": {"type": "function_call_output", "call_id": call,
                       "output": json.dumps({"answers": {question["id"]: {"answers": [answer or question["options"][0]["label"]]}}})}}]
        self.session.write_text("\n".join(json.dumps(row) for row in self.rows), encoding="utf-8")
        handle(self.project, dict(payload, hook_event_name="PostToolUse"))

    def control(self, data, expected=0):
        data.setdefault("revision", self.revision())
        path = self.project / ".build-loop/requests/current.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        command = control_command(self.project, path.name)
        payload = {"hook_event_name": "PreToolUse", "cwd": str(self.project), "tool_name": "Bash", "tool_input": {"command": command}}
        self.assertEqual(decision(self.project, payload), {})
        result = subprocess.run(["powershell.exe", "-NoProfile", "-Command", command], capture_output=True, timeout=15, cwd=self.project,
                                env=dict(os.environ, CODEX_HOME=str(self.root / "host-home")))
        message = result.stdout.decode(errors="replace") + result.stderr.decode(errors="replace")
        if expected == 0:
            self.assertEqual(result.returncode, 0, message)
        else:
            # PowerShell converts an external program's exit 2 to shell exit 1.
            self.assertNotEqual(result.returncode, 0, message)
        response = json.loads(result.stdout)
        if expected != 0:
            self.assertTrue(response["blocked"])
        return response

    def documents(self):
        texts = {"00-brief.md": "# 목적과 합격 조건", "01-prd.md": "### REQ-001 실행\n- [ ] 실행 성공\n- [ ] 출력 확인\nPOL-001 SCR-001 TC-001",
                 "02-policy.md": "### POL-001 규칙\n규칙 적용 시점 위반 예외 의사코드 TC-001", "03-ia.md": "SCR-001",
                 "05-qa-tc.md": "### TC-001 실행\nREQ-001 POL-001 SCR-001"}
        for stage, filename, _, _ in STAGES:
            self.assertEqual(guide(self.project)["stage"], stage)
            target = self.project / "docs" / filename
            self.assertEqual(decision(self.project, {"tool_name": "Write", "tool_input": {"file_path": str(target)}}), {})
            target.write_text(texts[filename], encoding="utf-8")
            if stage == "design":
                screens = self.project / "docs/04-screens"
                screens.mkdir()
                (screens / "SCR-001-main.md").write_text("REQ-001 POL-001 TC-001 [1] 기본 로딩 빈 에러", encoding="utf-8")
                (screens / "SCR-001-main.html").write_text("[1]", encoding="utf-8")
                (screens / "index.html").write_text("SCR-001-main.html", encoding="utf-8")
            request = self.control({"action": "stage-review", "stage": stage, "thread": "s1"})
            self.select(request["question"])
            self.control({"action": "stage-selection", "stage": stage})
        self.control({"action": "spec-review", "version": "v1.0", "thread": "s1"})
        request = Workflow(self.store).status()["review"]
        self.select(request["question"])
        self.control({"action": "spec-selection"})

    def test_interview_to_completion_through_real_windows_controller(self):
        for index in range(10):
            question = {"id": f"Q-{index}", "question": f"선택 {index}", "options": [
                {"label": "첫 항목 (추천)", "description": "작은 범위로 진행"}, {"label": "다른 항목", "description": "범위를 변경"}]}
            self.control({"action": "question", "question": question})
            self.assertEqual(guide(self.project)["question"], question)
            self.select(question)
            self.control({"action": "selection", "question": question["id"]})
        self.documents()
        self.assertTrue(Workflow(self.store).status()["development_allowed"])
        task = {"id": "TASK-001", "req": "REQ-001", "tc": "TC-001", "title": "실행", "acceptance": "출력 검증",
                "environment": "Windows-local-test", "check": [sys.executable, "check.py"]}
        self.control({"action": "plan", "tasks": [task]})
        path = self.project / "code/check.py"
        self.assertEqual(decision(self.project, {"tool_name": "Write", "tool_input": {"file_path": str(path)}}), {})
        path.write_text("assert 1 + 1 == 2\nprint('verified')", encoding="utf-8")
        self.control({"action": "check", "task": "TASK-001"})
        rejected_demo = self.control({"action": "demo", "thread": "s1"})
        self.select(rejected_demo["question"], "수정 필요")
        self.control({"action": "demo-selection"})
        self.assertFalse(guide(self.project)["complete"])
        self.assertEqual(handle(self.project, {"hook_event_name": "Stop", "cwd": str(self.project)})["decision"], "block")
        self.control({"action": "check", "task": "TASK-001"})
        for kind in ["demo", "completion"]:
            request = self.control({"action": kind, "thread": "s1"})
            self.select(request["question"])
            self.control({"action": kind + "-selection"})
        self.assertTrue(guide(self.project)["complete"])
        payload = {"hook_event_name": "Stop", "cwd": str(self.project)}
        self.assertEqual(handle(self.project, payload), {})
        handle(self.project, {"hook_event_name": "Interrupt", "cwd": str(self.project)})
        request = guide(self.project)["question"]
        self.select(request)
        self.control({"action": "resume-selection"})
        self.assertTrue(guide(self.project)["complete"])
        cr = self.project / "docs/changes/open/CR-001.md"
        cr.parent.mkdir(parents=True)
        cr.write_text("# CR-001\n표현 정정, 요구 범위는 동일", encoding="utf-8")
        prd = self.project / "docs/01-prd.md"
        request = self.control({"action": "change-review", "cr": "docs/changes/open/CR-001.md",
                                "files": {"docs/01-prd.md": prd.read_text(encoding="utf-8") + "\n표현 정정\n"}, "version": "v1.1", "thread": "s1"})
        self.select(request["question"])
        self.control({"action": "change-apply"})
        self.assertTrue(Workflow(self.store).status()["development_allowed"])
        self.assertFalse(guide(self.project)["complete"])
        self.assertTrue((self.project / "docs/changes/merged/CR-001.md").is_file())

    def test_recovery_limit_survives_new_store_and_success_opens_new_cycle(self):
        identity = {"project_id": str(self.project), "task_id": "t", "check_id": "c", "failure_category": "validation_failure", "environment_id": "local"}
        for index in range(3):
            self.store.begin_recovery(identity, self.revision())
        with self.assertRaises(RecoveryExhausted):
            StateStore(self.project, enabled=True).begin_recovery(identity, self.revision())
        proof = {"token": "pass-proof", "result": "passed", "task": {"id": "t", "environment": "local"}, "code": {"code/a": "hash"}}
        with self.store._write(self.revision()) as (db, state):
            self.store._event(db, state, "task_check_finished", proof)
        self.store.resolve_recovery(identity, "pass-proof", self.revision())
        self.store.begin_recovery(identity, self.revision())
        self.assertEqual(len(self.store.snapshot()["attempts"]), 4)

    def test_revision_feedback_is_preserved_and_old_menu_does_not_approve_revised_file(self):
        brief = self.project / "docs/00-brief.md"
        brief.write_text("# 초안", encoding="utf-8")
        request = self.control({"action": "stage-review", "stage": "brief", "thread": "s1"})
        self.select(request["question"], "수정 필요")
        self.control({"action": "stage-selection", "stage": "brief"})
        self.assertEqual(guide(self.project)["action_kind"], "revise")
        self.assertEqual(guide(self.project)["feedback"]["text"], "수정 필요")
        brief.write_text("# 수정안", encoding="utf-8")
        new_request = self.control({"action": "stage-review", "stage": "brief", "thread": "s1"})
        self.control({"action": "stage-selection", "stage": "brief"}, expected=2)
        self.assertEqual(guide(self.project)["stage"], "brief")
        self.select(new_request["question"])
        self.control({"action": "stage-selection", "stage": "brief"})
        self.assertEqual(guide(self.project)["stage"], "prd")
