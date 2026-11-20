import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from build_loop.guard import decision
from build_loop.spec_check import inspect_spec
from build_loop.state import StateStore


class SpecGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.docs = self.root / "docs"
        self.docs.mkdir()

    def write(self, name, text):
        path = self.docs / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def fixture(self):
        self.write("00-brief.md", "브리프")
        self.write("01-prd.md", "### REQ-001 기능\n- [ ] 첫 조건\n- [ ] 두번째 조건\nPOL-001 SCR-001 TC-001")
        self.write("02-policy.md", "### POL-001 규칙\n규칙 적용 시점 위반 예외 의사코드 TC-001")
        self.write("03-ia.md", "SCR-001")
        self.write("05-qa-tc.md", "### TC-001 시험\nREQ-001 POL-001 SCR-001")
        self.write("04-screens/SCR-001-main.md", "[1] 기본 로딩 빈 에러")
        self.write("04-screens/SCR-001-main.html", "<p>[1]</p>")
        self.write("04-screens/index.html", "SCR-001-main.html")

    def test_structural_pass_is_not_semantic_approval(self):
        self.fixture()
        result = inspect_spec(self.root)
        self.assertTrue(result["structural_passed"])
        self.assertEqual(result["semantic_review"], "required")
        self.assertEqual(len(result["files"]), 8)

    def test_missing_documents_fail(self):
        result = inspect_spec(self.root)
        self.assertFalse(result["structural_passed"])
        self.assertIn("missing:01-prd.md", result["issues"])

    def test_broken_reference_and_screen_mismatch(self):
        self.fixture()
        self.write("03-ia.md", "REQ-999")
        self.write("04-screens/SCR-001-main.html", "[2]")
        issues = inspect_spec(self.root)["issues"]
        self.assertIn("undefined:REQ-999", issues)
        self.assertIn("component_mismatch:SCR-001", issues)

    def test_policy_missing_element_blocks(self):
        self.fixture()
        self.write("02-policy.md", "### POL-001 규칙\nTC-001")
        self.assertIn("missing_policy_element:POL-001:예외", inspect_spec(self.root)["issues"])

    def test_guard_denies_missing_and_corrupt_db(self):
        self.assertEqual(decision(self.root)["hookSpecificOutput"]["permissionDecision"], "deny")
        state = StateStore(self.root, enabled=True)
        state.initialize()
        state.path.write_bytes(b"corrupt")
        self.assertEqual(decision(self.root)["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_guard_protocol_uses_exit_zero_json_deny(self):
        command = [sys.executable, "-m", "build_loop.guard", "--project", str(self.root)]
        result = subprocess.run(command, input=json.dumps({"hook_event_name": "PreToolUse",
                                "cwd": str(self.root), "tool_name": "Bash"}).encode(),
                                capture_output=True, check=True, timeout=10,
                                cwd=Path(__file__).resolve().parents[1])
        self.assertEqual(json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_guard_malformed_input_is_structured_denial(self):
        result = subprocess.run([sys.executable, "-m", "build_loop.guard", "--project", str(self.root)],
                                input=b"bad", capture_output=True, check=True, timeout=10,
                                cwd=Path(__file__).resolve().parents[1])
        self.assertEqual(json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"], "deny")
