import tempfile
import unittest
from pathlib import Path

from build_loop.director import create_project, guide, STAGES, request_stage_review, confirm_stage
from build_loop.state import StateStore, StateError


class DirectorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.templates = self.root / "templates"
        self.templates.mkdir()
        for filename in ["00-project-brief.md", "01-prd.md", "02-policy.md", "03-ia.md",
                         "04-wireframe-spec.md", "04-wireframe.html", "05-qa-tc.md", "change-request.md"]:
            (self.templates / filename).write_text("# original template", encoding="utf-8")

    def create(self):
        return create_project(self.root, "my-app", "반복 업무를 줄이는 도구", self.templates, enabled=True)

    def test_idea_to_first_artifact_and_resume(self):
        project = self.create()
        view = guide(project)
        self.assertEqual(view["stage"], "brief")
        self.assertIn("반복 업무", view["idea"])
        self.assertTrue((project / ".git").is_dir())
        self.assertTrue((project / "AGENTS.md").is_file())
        self.assertEqual(view, guide(project))
        self.assertFalse(view["development_allowed"])

    def test_existing_project_is_preserved(self):
        project = self.create()
        original = (project / "AGENTS.md").read_bytes()
        with self.assertRaises(FileExistsError):
            self.create()
        self.assertEqual((project / "AGENTS.md").read_bytes(), original)

    def test_missing_artifact_selected_in_order_no_false_completion(self):
        project = self.create()
        for stage, filename, _, _ in STAGES:
            (project / "docs" / filename).write_text("# Draft", encoding="utf-8")
        view = guide(project)
        self.assertEqual(view["stage"], "brief")
        self.assertEqual(view["action_kind"], "review")
        self.assertFalse(view["complete"])
        self.assertFalse(view["development_allowed"])

    def test_invalid_name_and_disabled_start_write_nothing(self):
        for name in ["../escape", "CON", "COM1", "a/b"]:
            with self.assertRaises(StateError):
                create_project(self.root, name, "idea", self.templates, enabled=True)
        with self.assertRaises(StateError):
            create_project(self.root, "disabled", "idea", self.templates)
        self.assertFalse((self.root / "projects").exists())

    def test_pause_is_respected(self):
        project = self.create()
        StateStore(project, enabled=True).pause(1)
        self.assertEqual(guide(project)["stage"], "paused")

    def client(self, text):
        class Client:
            def call(self, method, params):
                if method == "thread/read":
                    return {"thread": {"id": "thread-1"}}
                if method == "thread/turns/list":
                    return {"data": [{"id": "turn-1"}]}
                return {"data": [{"turnId": "turn-1", "item": {"type": "userMessage", "id": "item-1",
                                  "content": [{"type": "text", "text": text}]}}]}
        return Client()

    def test_full_planning_order_with_explicit_stage_confirmations(self):
        project = self.create()
        store = StateStore(project, enabled=True)
        for stage, filename, _, _ in STAGES:
            self.assertEqual(guide(project)["stage"], stage)
            (project / "docs" / filename).write_text("# Draft", encoding="utf-8")
            revision = store.snapshot()["revision"]
            review = request_stage_review(store, stage, "thread-1", revision)
            confirm_stage(store, self.client(review["reply"]), stage, revision + 1)
        self.assertEqual(guide(project)["stage"], "spec_review")
        self.assertFalse(guide(project)["development_allowed"])

    def test_generic_confirmation_does_not_skip_stage(self):
        project = self.create()
        store = StateStore(project, enabled=True)
        (project / "docs/00-brief.md").write_text("# Draft", encoding="utf-8")
        request_stage_review(store, "brief", "thread-1", 1)
        with self.assertRaises(StateError):
            confirm_stage(store, self.client("확인"), "brief", 2)
        self.assertEqual(guide(project)["stage"], "brief")

    def test_changed_document_blocks_confirmation_and_resume(self):
        project = self.create()
        store = StateStore(project, enabled=True)
        path = project / "docs/00-brief.md"
        path.write_text("# Draft", encoding="utf-8")
        review = request_stage_review(store, "brief", "thread-1", 1)
        path.write_text("# Changed", encoding="utf-8")
        with self.assertRaises(StateError):
            confirm_stage(store, self.client(review["reply"]), "brief", 2)
        review = request_stage_review(store, "brief", "thread-1", 2)
        confirm_stage(store, self.client(review["reply"]), "brief", 3)
        path.write_text("# Changed again", encoding="utf-8")
        self.assertEqual(guide(project)["action_kind"], "change_required")
