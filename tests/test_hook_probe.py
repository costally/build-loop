import json
import os
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

from build_loop.hook_probe import MARKER, handle


class HookProbeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="build-loop-hook-")
        self.project = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def payload(self, command):
        return {"hook_event_name": "PreToolUse", "cwd": str(self.project),
                "tool_name": "exec_command", "tool_input": {"cmd": command}, "session_id": "test-only"}

    def test_marker_command_is_denied(self):
        self.assertTrue(handle(self.payload("Write-Output '" + MARKER + "'"), self.project))

    def test_ordinary_command_is_not_denied(self):
        self.assertFalse(handle(self.payload("Write-Output 'ordinary'"), self.project))

    def test_async_selection_is_blocked_before_false_menu_claim(self):
        payload = self.payload("not relevant")
        payload["tool_name"] = "request_user_input_async"
        self.assertTrue(handle(payload, self.project))
        event = json.loads((self.project / ".build-loop/host-smoke-events.jsonl").read_text())
        self.assertEqual(event["reason_code"], "async_selection_unverified")
        self.assertFalse(event["marker_present"])

    def test_synchronous_selection_is_not_blocked(self):
        payload = self.payload("not relevant")
        payload["tool_name"] = "request_user_input"
        self.assertFalse(handle(payload, self.project))

    def test_log_does_not_contain_command_or_raw_session(self):
        handle(self.payload("do-not-log-this-text"), self.project)
        log = (self.project / ".build-loop/host-smoke-events.jsonl").read_text()
        self.assertNotIn("do-not-log-this-text", log)
        self.assertNotIn("test-only", log)
        self.assertEqual(json.loads(log)["decision"], "observe")

    def test_other_project_is_rejected_without_evidence_write(self):
        payload = self.payload("ordinary")
        payload["cwd"] = str(self.project.parent)
        with self.assertRaises(ValueError):
            handle(payload, self.project)
        self.assertFalse((self.project / ".build-loop").exists())

    def test_malformed_payload_is_rejected(self):
        with self.assertRaises(ValueError):
            handle([], self.project)

    def test_handler_process_returns_structured_deny_with_zero_exit(self):
        script = Path(__file__).resolve().parents[1] / "build_loop/hook_probe.py"
        result = subprocess.run(
            [sys.executable, str(script), "--project", str(self.project)],
            input=json.dumps(self.payload(MARKER)), text=True, encoding="utf-8",
            capture_output=True, timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        self.assertEqual(result.returncode, 0)
        decision = json.loads(result.stdout)["hookSpecificOutput"]
        self.assertEqual(decision["hookEventName"], "PreToolUse")
        self.assertEqual(decision["permissionDecision"], "deny")
        self.assertIn("requested probe command blocked", decision["permissionDecisionReason"])
        self.assertEqual(result.stderr, "")

    @unittest.skipUnless(os.name == "nt", "Windows shell integration")
    def test_windows_powershell_preserves_structured_deny(self):
        script = Path(__file__).resolve().parents[1] / "build_loop/hook_probe.py"
        command = subprocess.list2cmdline([sys.executable, str(script), "--project", str(self.project)])
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
            input=json.dumps(self.payload(MARKER)), text=True, encoding="utf-8",
            capture_output=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        decision = json.loads(result.stdout)["hookSpecificOutput"]
        self.assertEqual(decision["permissionDecision"], "deny")
        self.assertEqual(decision["hookEventName"], "PreToolUse")

    def test_smoke_setup_preserves_existing_project(self):
        marker = self.project / "user-file.txt"
        marker.write_text("keep", encoding="utf-8")
        script = Path(__file__).resolve().parents[1] / "tools/setup_host_smoke.py"
        result = subprocess.run(
            [sys.executable, str(script), "--project", str(self.project)],
            text=True, capture_output=True, timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(marker.read_text(encoding="utf-8"), "keep")
        self.assertFalse((self.project / ".codex").exists())

    def test_new_smoke_project_pins_default_mode_selection(self):
        target = self.project / "new-smoke"
        script = Path(__file__).resolve().parents[1] / "tools/setup_host_smoke.py"
        result = subprocess.run(
            [sys.executable, str(script), "--project", str(target)],
            text=True, capture_output=True, timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        settings = tomllib.loads((target / ".codex/config.toml").read_text())
        self.assertTrue(settings["features"]["default_mode_request_user_input"])
        guidance = (target / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("request_user_input", guidance)
        self.assertNotIn("@@SELECTION_STEP@@", guidance)
