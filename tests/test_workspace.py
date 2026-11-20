import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from build_loop.workspace import begin, begin_command, bound_project, workspace_handle
from build_loop.policy import control_command
from build_loop.state import StateError


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="build-loop-download-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        source = Path(__file__).resolve().parents[1]
        shutil.copytree(source / "templates", self.root / "templates")
        (self.root / ".build-loop").mkdir()

    def start(self, session="session-1", name="web-notepad", **extra):
        (self.root / f".build-loop/start-{session}.json").write_text(
            json.dumps(dict(name=name, idea="웹페이지 메모장 만들자", **extra)), encoding="utf-8")
        return begin(self.root, session, enabled=True)

    def payload(self, event="PreToolUse", **kwargs):
        return dict(cwd=str(self.root), session_id="session-1", hook_event_name=event, **kwargs)

    def denied(self, result):
        return result.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"

    def test_same_session_begin_preserves_state_and_routes_control(self):
        first = self.start()
        self.assertTrue(first["same_session"])
        project = self.root / "projects/web-notepad"
        self.assertEqual(Path(first["guide"]["project"]), project)
        self.assertEqual(first, begin(self.root, "session-1", enabled=True))
        self.assertEqual(bound_project(self.root, "session-1"), project)
        allowed = workspace_handle(self.root, self.payload(tool_name="exec_command", tool_input={
            "cmd": control_command(project, "guide.json")}))
        self.assertEqual(allowed, {})

    def test_sessions_are_isolated_and_resume_is_explicit(self):
        first = self.start()
        with self.assertRaises(StateError):
            self.start("session-2")
        second = self.start("session-2", resume=True)
        self.assertEqual(first["guide"], second["guide"])
        third = self.start("session-3", "another")
        self.assertNotEqual(third["guide"]["project"], first["guide"]["project"])

    def test_bootstrap_only_allows_own_request_and_exact_command(self):
        context = workspace_handle(self.root, self.payload("SessionStart"))
        self.assertIn("session-1", context["hookSpecificOutput"]["additionalContext"])
        write = self.payload(tool_name="apply_patch", tool_input={"input":
            "*** Begin Patch\n*** Add File: .build-loop/start-session-1.json\n+{}\n*** End Patch"})
        self.assertEqual(workspace_handle(self.root, write), {})
        write["tool_input"]["input"] = write["tool_input"]["input"].replace("session-1", "session-2")
        self.assertTrue(self.denied(workspace_handle(self.root, write)))
        for suffix in ["", "; whoami"]:
            result = workspace_handle(self.root, self.payload(tool_name="exec_command", tool_input={
                "cmd": begin_command("session-1") + suffix}))
            self.assertEqual(self.denied(result), bool(suffix))

    def test_root_writes_cannot_escape_project_or_skip_approval(self):
        self.start()
        for path, allowed in [("projects/web-notepad/docs/00-brief.md", True),
                              ("projects/web-notepad/code/index.html", False),
                              ("README.md", False), ("projects/other/docs/00-brief.md", False)]:
            result = workspace_handle(self.root, self.payload(tool_name="apply_patch", tool_input={
                "input": f"*** Begin Patch\n*** Add File: {path}\n+test\n*** End Patch"}))
            self.assertEqual(not self.denied(result), allowed, path)

    def test_read_instructions_and_real_question_menu_in_same_session(self):
        self.start()
        project = bound_project(self.root, "session-1")
        read = f'Get-Content -LiteralPath "{project / "AGENTS.md"}" -Encoding UTF8'
        self.assertEqual(workspace_handle(self.root, self.payload(tool_name="exec_command",
                                                                tool_input={"cmd": read})), {})
        from build_loop.workflow import Workflow
        from build_loop.state import StateStore
        question = {"id": "usage", "question": "어떤 용도인가요?",
                    "options": [{"label": "Work (추천)", "description": "Work notes"},
                                {"label": "Study", "description": "Study notes"}]}
        Workflow(StateStore(project, enabled=True)).ask(question, 1)
        self.assertEqual(workspace_handle(self.root, self.payload(tool_name="request_user_input",
                          tool_input={"questions": [question]})), {})
        bad = dict(question, question="Different question")
        self.assertTrue(self.denied(workspace_handle(self.root, self.payload(tool_name="request_user_input",
                          tool_input={"questions": [bad]}))))

    def test_hook_processes_read_utf8_menu_with_non_utf8_pipe_locale(self):
        from build_loop.workflow import Workflow
        from build_loop.state import StateStore

        self.start()
        project = bound_project(self.root, "session-1")
        question = {
            "id": "usage", "header": "사용 상황", "question": "어떤 용도인가요?",
            "options": [{"label": "한 장 자동 저장 (Recommended)", "description": "브라우저에 저장합니다."},
                        {"label": "임시 메모", "description": "현재 화면에서만 씁니다."}],
        }
        Workflow(StateStore(project, enabled=True)).ask(question, 1)
        source = Path(__file__).resolve().parents[1]
        shutil.copy2(source / "workspace_hook.py", self.root / "workspace_hook.py")
        entrypoints = [
            ([str(self.root / "workspace_hook.py")], self.root),
            ([str(source / "hook_entry.py"), "--project", str(project)], project),
        ]
        for args, cwd in entrypoints:
            with self.subTest(entrypoint=args[0]):
                payload = self.payload(tool_name="request_user_input", tool_input={"questions": [question]})
                payload["cwd"] = str(cwd)
                result = subprocess.run([sys.executable, *args], cwd=cwd,
                                        input=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                                        capture_output=True, timeout=10,
                                        env=dict(os.environ, PYTHONIOENCODING="cp949", PYTHONPATH=str(source)))
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout), {})

    def test_checkout_hook_command_executes_from_download_root(self):
        source = Path(__file__).resolve().parents[1]
        for name in ["workspace_hook.py", "hook_entry.py", "control.py", "launch.py", "build-loop.cmd"]:
            shutil.copyfile(source / name, self.root / name)
        shutil.copytree(source / "build_loop", self.root / "build_loop", ignore=shutil.ignore_patterns("__pycache__"))
        # 네이티브 Codex는 patch/input이 아니라 tool_input.command로 패치를 보낸다.
        request = self.root / ".build-loop/start-session-1.json"
        patch = f'*** Begin Patch\n*** Add File: {request}\n+{{"name":"web-notepad","idea":"메모장"}}\n*** End Patch'
        before = subprocess.run([sys.executable, "workspace_hook.py"], cwd=self.root,
                                input=json.dumps(self.payload(tool_name="apply_patch", tool_input={"command": patch})),
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(json.loads(before.stdout), {})
        # 배포한 폴더의 실제 런처로 생성하고 그 폴더의 훅/제어기를 실행한다.
        (self.root / ".build-loop/start-session-1.json").write_text(
            json.dumps({"name": "web-notepad", "idea": "웹페이지 메모장"}), encoding="utf-8")
        started = subprocess.run([sys.executable, "launch.py", "begin", "--session", "session-1"],
                                 cwd=self.root, env=dict(os.environ, BUILD_LOOP_EXPERIMENT_ENABLED="1"),
                                 capture_output=True, text=True, encoding="utf-8", timeout=15)
        self.assertEqual(started.returncode, 0, started.stderr)
        self.assertTrue(json.loads(started.stdout)["same_session"])
        config = json.loads((source / ".codex/hooks.json").read_text(encoding="utf-8"))
        command = config["hooks"]["SessionStart"][0]["hooks"][0]["commandWindows"]
        self.assertEqual(command, "python workspace_hook.py")
        run = subprocess.run([sys.executable, "workspace_hook.py"], cwd=self.root,
                             input=json.dumps(self.payload("SessionStart")), capture_output=True, text=True, timeout=10)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn("session-1", json.loads(run.stdout)["hookSpecificOutput"]["additionalContext"])
        project = self.root / "projects/web-notepad"
        cmd = f'& "{sys.executable}" "{self.root / "control.py"}" --project "{project}" --request "guide.json"'
        pre = subprocess.run([sys.executable, "workspace_hook.py"], cwd=self.root,
                             input=json.dumps(self.payload(tool_name="exec_command", tool_input={"cmd": cmd})),
                             capture_output=True, text=True, timeout=10)
        self.assertEqual(json.loads(pre.stdout), {})
        checked = subprocess.run([sys.executable, "control.py", "--project", str(project),
                                  "--request", "guide.json"], cwd=self.root, capture_output=True, text=True, encoding="utf-8", timeout=10)
        self.assertEqual(checked.returncode, 0, checked.stderr)
        self.assertEqual(json.loads(checked.stdout)["stage"], "brief")

    def test_invalid_session_and_disabled_begin_write_nothing(self):
        for session in ["../escape", "", "x;whoami"]:
            with self.assertRaises(StateError):
                begin(self.root, session, enabled=True)
        with self.assertRaises(StateError):
            begin(self.root, "session-1")
        self.assertFalse((self.root / "projects").exists())

    def test_native_command_patch_bootstrap_and_project_writes(self):
        request = self.root / ".build-loop/start-session-1.json"
        patch = f'*** Begin Patch\n*** Add File: {request}\n+{{"name":"web-notepad","idea":"메모장"}}\n*** End Patch'
        payload = self.payload(tool_name="apply_patch", tool_input={"command": patch})
        self.assertEqual(workspace_handle(self.root, payload), {})
        other = patch.replace("start-session-1.json", "start-other-session.json")
        self.assertTrue(self.denied(workspace_handle(self.root, self.payload(tool_name="apply_patch", tool_input={"command": other}))))
        self.start()
        project = bound_project(self.root, "session-1")
        for path, allowed in [(project / "docs/00-brief.md", True), (project / "code/index.html", False),
                              (self.root / "README.md", False)]:
            patch = f'*** Begin Patch\n*** Add File: {path}\n+content\n*** End Patch'
            result = workspace_handle(self.root, self.payload(tool_name="apply_patch", tool_input={"command": patch}))
            self.assertEqual(not self.denied(result), allowed)
