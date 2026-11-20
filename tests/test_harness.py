import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from build_loop.director import create_project
from build_loop.harness import handle
from build_loop.policy import decision, control_command
from build_loop.workflow import Workflow
from build_loop.state import StateStore


class HarnessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="build-loop-harness-")
        self.addCleanup(self.temp.cleanup)
        self.source = Path(__file__).resolve().parents[1]
        self.project = create_project(Path(self.temp.name), "managed", "minimal", self.source / "templates", enabled=True)

    def payload(self, event="PreToolUse", tool="Write", data=None):
        return {"hook_event_name": event, "cwd": str(self.project), "tool_name": tool,
                "tool_input": data or {}, "session_id": "test-session"}

    def test_actual_configured_handler_runs_outside_source_directory(self):
        config = json.loads((self.project / ".codex/hooks.json").read_text(encoding="utf-8"))
        self.assertEqual(set(config["hooks"]), {"SessionStart", "PreToolUse", "PostToolUse", "Stop", "Interrupt"})
        command = config["hooks"]["PreToolUse"][0]["hooks"][0]["commandWindows"]
        process = subprocess.run(command, input=json.dumps(self.payload(data={"file_path": "code/main.py"})).encode(),
                                 capture_output=True, cwd=self.project, timeout=10)
        self.assertEqual(process.returncode, 0)
        self.assertEqual(json.loads(process.stdout)["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertFalse((self.project / "code/main.py").exists())

    def test_current_document_allowed_later_document_and_code_blocked(self):
        self.assertEqual(decision(self.project, self.payload(data={"file_path": "docs/00-brief.md"})), {})
        for path in ["docs/01-prd.md", "code/main.py", "../escape", "AGENTS.md", ".codex/hooks.json", ".build-loop/state.sqlite"]:
            self.assertEqual(decision(self.project, self.payload(data={"file_path": path}))["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_patch_cannot_hide_write_outside_current_stage(self):
        patch = "*** Begin Patch\n*** Add File: docs/00-brief.md\n+brief\n*** Add File: code/hidden.py\n+pass\n*** End Patch"
        result = decision(self.project, self.payload(tool="apply_patch", data={"input": patch}))
        self.assertEqual(result["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_native_command_patch_respects_stage_and_checks_python_after_write(self):
        patch = "*** Begin Patch\n*** Add File: docs/00-brief.md\n+brief\n*** End Patch"
        self.assertEqual(decision(self.project, self.payload(tool="apply_patch", data={"command": patch})), {})
        hidden = patch.replace("*** End Patch", "*** Add File: code/hidden.py\n+pass\n*** End Patch")
        self.assertEqual(decision(self.project, self.payload(tool="apply_patch", data={"command": hidden}))[
            "hookSpecificOutput"]["permissionDecision"], "deny")
        (self.project / "code/broken.py").write_text("def broken(:", encoding="utf-8")
        result = handle(self.project, self.payload(event="PostToolUse", tool="apply_patch", data={
            "command": "*** Begin Patch\n*** Add File: code/broken.py\n+def broken(:\n*** End Patch"}))
        self.assertEqual(result["decision"], "block")

    def test_control_command_allowed_but_appended_shell_blocked(self):
        command = control_command(self.project, "guide.json")
        self.assertEqual(decision(self.project, self.payload(tool="Bash", data={"command": command})), {})
        result = decision(self.project, self.payload(tool="Bash", data={"command": command + '; Write-Output bypass'}))
        self.assertEqual(result["hookSpecificOutput"]["permissionDecision"], "deny")
        process = subprocess.run(["powershell.exe", "-NoProfile", "-Command", command], capture_output=True, cwd=self.project, timeout=10)
        self.assertEqual(process.returncode, 0)
        self.assertEqual(json.loads(process.stdout)["stage"], "brief")

    def test_only_saved_menu_is_allowed(self):
        q = {"id": "q1", "question": "사례 선택", "options": [{"label": "새 사례 (추천)", "description": "새 사례로 확인"},
              {"label": "기존 사례", "description": "기존 사례로 확인"}]}
        store = StateStore(self.project, enabled=True)
        Workflow(store).ask(q, 1)
        self.assertEqual(decision(self.project, self.payload(tool="request_user_input", data={"questions": [q]})), {})
        q["id"] = "different"
        self.assertEqual(decision(self.project, self.payload(tool="request_user_input", data={"questions": [q]}))["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_stop_continues_unfinished_work_but_interrupt_pauses(self):
        self.assertEqual(handle(self.project, self.payload(event="Stop"))["decision"], "block")
        handle(self.project, self.payload(event="Interrupt"))
        self.assertEqual(handle(self.project, self.payload(event="Stop")), {})
        self.assertTrue(StateStore(self.project).snapshot()["paused"])

    def test_post_tool_rejects_python_syntax_error(self):
        (self.project / "code/broken.py").write_text("def broken(", encoding="utf-8")
        result = handle(self.project, self.payload(event="PostToolUse", data={"file_path": "code/broken.py"}))
        self.assertEqual(result["decision"], "block")

    def test_waiting_for_document_review_does_not_force_continue(self):
        (self.project / "docs/00-brief.md").write_text("# review", encoding="utf-8")
        from build_loop.director import request_stage_review
        request_stage_review(StateStore(self.project, enabled=True), "brief", "thread", 1)
        self.assertEqual(handle(self.project, self.payload(event="Stop")), {})

    def test_stop_progress_is_not_mistaken_for_stall(self):
        first = self.payload(event="Stop")
        self.assertEqual(handle(self.project, first)["decision"], "block")
        first["stop_hook_active"] = True
        (self.project / "docs/00-brief.md").write_text("# drafted", encoding="utf-8")
        self.assertEqual(handle(self.project, first)["decision"], "block")
        self.assertFalse(StateStore(self.project).snapshot()["paused"])

    def test_busy_writer_stops_after_configured_five_seconds(self):
        import sqlite3
        import time
        state = StateStore(self.project, enabled=True)
        connection = sqlite3.connect(state.path, isolation_level=None)
        try:
            connection.execute("BEGIN IMMEDIATE")
            start = time.perf_counter()
            with self.assertRaises(sqlite3.OperationalError):
                state.pause(1)
            elapsed = time.perf_counter() - start
            self.assertGreaterEqual(elapsed, 4.5)
            self.assertLess(elapsed, 7)
        finally:
            connection.rollback()
            connection.close()
        self.assertEqual(state.snapshot()["revision"], 1)
