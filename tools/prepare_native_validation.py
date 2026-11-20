"""실제 호스트에서 사용할 검증 폴더만 준비한다. 모델 실행·신뢰 우회 없음."""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from build_loop.director import create_project
from build_loop.director import request_stage_review
from build_loop.state import StateStore


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    args.root.mkdir(parents=True, exist_ok=True)
    project = create_project(args.root, "host-check", "build-loop native hook acceptance fixture; not a new product", Path(__file__).resolve().parents[1] / "templates", enabled=True)
    (project / "docs/00-brief.md").write_text("# 내부 훅 검증 fixture\n사용자 제품 기획이 아니다. 미승인 코드 쓰기의 실제 도구 차단만 관찰한다.\n", encoding="utf-8")
    request_stage_review(StateStore(project, enabled=True), "brief", "validation-fixture", 1)
    (project / ".build-loop/requests/list-code.json").write_text('{"action":"list","path":"code"}', encoding="utf-8")
    prompt = (
        "This folder is a build-loop native hook validation fixture, not a new planning project. "
        "Read AGENTS.md and run its saved guide control command. Do not ask planning or menu questions. "
        "Attempt exactly one Write or apply_patch to code/native-hook-test.txt containing BUILD_LOOP_NATIVE_CHECK. "
        "Expect an actual PreToolUse denial. Do not bypass or retry. "
        "Run the same control command with request list-code.json to confirm the file is absent. "
        "Report the actual tool denial separately from hook logs, and write the observed result to docs/NEXT-STEP.md. "
        "If project or hook trust review is required, use normal review and stop for the user; do not bypass trust. "
        "Do not claim the full product or all host QA passed from this single check."
    )
    (project / "RUN-IN-CODEX.md").write_text("# 실제 호스트 검증\n\n터미널과 PC 앱 각각 이 폴더를 열고 아래 요청으로 검사한다. 기획 승인을 다시 요청하지 않는다.\n\n" + prompt, encoding="utf-8")
    native = Path(os.environ.get("APPDATA", "")) / "npm/node_modules/@openai/codex/node_modules/codex-win32-x64/vendor/x86_64-pc-windows-msvc/bin/codex.exe"
    command = subprocess.list2cmdline([str(native), "--disable", "daemon_auto_start", "--enable", "default_mode_request_user_input", "-C", str(project), prompt])
    launcher = args.root / "Check-CLI.cmd"
    launcher.write_text("@echo off\r\nsetlocal\r\n" + command + "\r\npause\r\n", encoding="utf-8")
    print(json.dumps({"project": str(project), "cli_launcher": str(launcher), "model_started": False, "trust_bypassed": False}))


if __name__ == "__main__":
    main()
