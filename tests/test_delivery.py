import tempfile
import unittest
import sys
from pathlib import Path
from unittest.mock import patch

from build_loop.delivery import Delivery
from build_loop.state import StateStore, StateError, RecoveryExhausted
from build_loop.workflow import manifest
from build_loop.workflow import Workflow
from build_loop.director import guide, STAGES, request_stage_review, confirm_stage
from build_loop.spec_check import inspect_spec
from build_loop.probe import observe_user_reply


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "docs").mkdir()
        (self.root / "code").mkdir()
        (self.root / "docs/01-prd.md").write_text("### REQ-001 Example", encoding="utf-8")
        (self.root / "docs/05-qa-tc.md").write_text("### TC-001 Example", encoding="utf-8")
        self.app = self.root / "code/app.py"
        self.app.write_text("print('example result')", encoding="utf-8")
        self.store = StateStore(self.root, enabled=True)
        self.store.initialize()
        self.delivery = Delivery(self.store)
        self.spec = manifest(self.root, ["docs/01-prd.md", "docs/05-qa-tc.md"])
        self.task = {"id": "task-001", "req": "REQ-001", "tc": "TC-001", "title": "예제 구현",
                     "acceptance": "예제 실행 성공", "check": [sys.executable, "app.py"], "environment": "local-test"}

    def fake_flow(self):
        # Simulates an approved host solely to test the delivery engine.
        return {"development_allowed": True, "paused": False, "manifest_matches": True,
                "review": {"files": self.spec, "version": "v1.0"}}

    def plan(self):
        self.delivery.plan([self.task], self.store.snapshot()["revision"])

    def client(self, text):
        class Client:
            def call(self, method, params):
                if method == "thread/read":
                    return {"thread": {"id": "t1"}}
                if method == "thread/turns/list":
                    return {"data": [{"id": "u1"}]}
                return {"data": [{"turnId": "u1", "item": {"id": "i1", "type": "userMessage",
                                  "content": [{"type": "text", "text": text}]}}]}
        return Client()

    def test_unapproved_execution_is_rejected(self):
        with self.assertRaises(StateError):
            self.delivery.run_check("task-001", 0)
        self.assertEqual(self.store.snapshot()["revision"], 0)

    @patch("build_loop.delivery.Workflow.status")
    def test_actual_check_then_demo_then_final_confirmation(self, status):
        status.side_effect = self.fake_flow
        self.plan()
        self.assertFalse(self.delivery.status()["complete"])
        self.delivery.run_check("task-001", 1)
        self.assertIn("시연", self.delivery.status()["next_action"])
        demo = self.delivery.request_user_confirmation("demo", "t1", 3)
        self.delivery.confirm_user(self.client(demo["reply"]), "demo", 4)
        self.assertEqual(self.delivery.status()["next_action"], "사용자 최종 완료 승인 대기")
        done = self.delivery.request_user_confirmation("completion", "t1", 5)
        self.delivery.confirm_user(self.client(done["reply"]), "completion", 6)
        self.assertTrue(self.delivery.status()["complete"])
        self.app.write_text("print('changed')", encoding="utf-8")
        self.assertFalse(self.delivery.status()["complete"])
        self.assertEqual(self.delivery.status()["current_task"]["result"], "unrun")

    @patch("build_loop.delivery.Workflow.status")
    def test_failed_check_blocks_demo_and_points_to_same_task(self, status):
        status.side_effect = self.fake_flow
        self.app.write_text("raise SystemExit(1)", encoding="utf-8")
        self.plan()
        self.delivery.run_check("task-001", 1)
        self.assertEqual(self.delivery.status()["current_task"]["result"], "failed")
        with self.assertRaises(StateError):
            self.delivery.request_user_confirmation("demo", "t1", 3)

    @patch("build_loop.delivery.Workflow.status")
    def test_code_change_during_check_is_stale(self, status):
        status.side_effect = self.fake_flow
        self.app.write_text("from pathlib import Path\nPath('new.txt').write_text('changed')", encoding="utf-8")
        self.plan()
        self.delivery.run_check("task-001", 1)
        self.assertNotEqual(self.delivery.status()["tasks"][0]["result"], "passed")

    @patch("build_loop.delivery.Workflow.status")
    def test_unknown_req_and_missing_coverage_rejected(self, status):
        status.side_effect = self.fake_flow
        self.task["req"] = "REQ-999"
        with self.assertRaises(StateError):
            self.plan()

    @patch("build_loop.delivery.Workflow.status")
    def test_generic_final_response_is_not_completion(self, status):
        status.side_effect = self.fake_flow
        self.plan()
        self.delivery.run_check("task-001", 1)
        self.delivery.request_user_confirmation("demo", "t1", 3)
        with self.assertRaises(StateError):
            self.delivery.confirm_user(self.client("좋네"), "demo", 4)
        self.assertFalse(self.delivery.status()["complete"])

    def test_planning_to_real_process_and_completion_with_simulated_user_transport(self):
        self.delivery._append(0, "project_started", {"name": "example", "idea": "실행 결과 출력"})
        (self.root / "docs/00-brief.md").write_text("# 합의한 예제", encoding="utf-8")
        (self.root / "docs/01-prd.md").write_text("### REQ-001 출력\n- [ ] 실행\n- [ ] 출력\nPOL-001 SCR-001 TC-001", encoding="utf-8")
        (self.root / "docs/02-policy.md").write_text("### POL-001 정책\n규칙 적용 시점 위반 예외 의사코드 TC-001", encoding="utf-8")
        (self.root / "docs/03-ia.md").write_text("SCR-001", encoding="utf-8")
        screens = self.root / "docs/04-screens"
        screens.mkdir()
        (screens / "SCR-001-main.md").write_text("[1] 기본 로딩 빈 에러", encoding="utf-8")
        (screens / "SCR-001-main.html").write_text("[1]", encoding="utf-8")
        (screens / "index.html").write_text("SCR-001-main.html", encoding="utf-8")
        flow = Workflow(self.store)
        flow.ask({"id": "Q-001", "question": "예제 용도", "options": [
            {"label": "출력 (추천)", "description": "출력 확인"}, {"label": "다른 용도", "description": "추가 구체화"}]}, 1)
        receipt = observe_user_reply(self.client("출력"), "t1", "출력")
        flow.answer("Q-001", "출력", receipt, 2)
        for stage, _, _, _ in STAGES:
            revision = self.store.snapshot()["revision"]
            review = request_stage_review(self.store, stage, "t1", revision)
            confirm_stage(self.store, self.client(review["reply"]), stage, revision + 1)
        check = inspect_spec(self.root)
        revision = self.store.snapshot()["revision"]
        flow.review("spec-set", "v1.0", list(check["files"]), revision, "t1")
        self.assertFalse(flow.status()["development_allowed"])
        text = flow.status()["review"]["approval_text"]
        flow.observe_approval(self.client(text), "t1", revision + 1)
        self.assertTrue(flow.status()["development_allowed"])
        self.spec = check["files"]
        self.plan()
        self.delivery.run_check("task-001", self.store.snapshot()["revision"])
        for kind in ["demo", "completion"]:
            revision = self.store.snapshot()["revision"]
            request = self.delivery.request_user_confirmation(kind, "t1", revision)
            self.delivery.confirm_user(self.client(request["reply"]), kind, revision + 1)
        self.assertTrue(guide(self.root)["complete"])
        self.assertEqual(guide(self.root)["stage"], "complete")

    @patch("build_loop.delivery.Workflow.status")
    def test_unfinished_check_is_not_automatically_retried_after_resume(self, status):
        status.side_effect = self.fake_flow
        self.plan()
        self.delivery._append(1, "task_check_started", {"task": self.task, "token": "interrupted",
                              "code": self.delivery.code_version(), "spec": self.spec})
        reopened = Delivery(StateStore(self.root, enabled=True))
        self.assertEqual(reopened.status()["current_task"]["result"], "unknown")
        with self.assertRaises(StateError):
            reopened.run_check("task-001", 2)

    @patch("build_loop.delivery.Workflow.status")
    def test_actual_failed_checks_consume_three_recoveries_and_no_more(self, status):
        status.side_effect = self.fake_flow
        self.app.write_text("raise SystemExit(1)", encoding="utf-8")
        self.plan()
        self.delivery.run_check("task-001", self.store.snapshot()["revision"])
        for attempt in range(3):
            self.delivery.run_check("task-001", self.store.snapshot()["revision"])
        snapshot = self.store.snapshot()
        self.assertEqual(len(snapshot["attempts"]), 3)
        self.assertTrue(all(attempt["result"] == "failed" for attempt in snapshot["attempts"]))
        with self.assertRaises(RecoveryExhausted):
            Delivery(StateStore(self.root, enabled=True)).run_check("task-001", snapshot["revision"])
