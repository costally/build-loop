"""다운로드 폴더의 세션과 사용자 프로젝트를 연결한다."""

import json
import re
from pathlib import Path

from .director import create_project, guide
from .state import StateError


def session_key(session):
    if not isinstance(session, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", session):
        raise StateError("실제 Codex session_id가 필요합니다.")
    return session


def binding_path(root, session):
    return Path(root) / ".build-loop/sessions" / (session_key(session) + ".json")


def bound_project(root, session):
    root = Path(root).resolve(strict=True)
    binding = binding_path(root, session)
    if not binding.exists():
        return None
    relative = json.loads(binding.read_text(encoding="utf-8"))["project"]
    project = (root / relative).resolve(strict=True)
    if not project.is_relative_to(root / "projects") or not (project / ".build-loop/state.sqlite").is_file():
        raise StateError("세션 프로젝트 경계를 확인하세요.")
    return project


def begin(root, session, *, enabled=False):
    if not enabled:
        raise StateError("experimental writes are disabled")
    root = Path(root).resolve(strict=True)
    session_key(session)
    project = bound_project(root, session)
    if project is None:
        request = root / ".build-loop" / ("start-" + session + ".json")
        data = json.loads(request.read_text(encoding="utf-8-sig"))
        name = data["name"]
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", name):
            raise StateError("프로젝트 이름을 확인하세요.")
        candidate = root / "projects" / name
        if candidate.exists():
            # 기존 자료는 명시적인 재개 요청일 때만 연결한다.
            if data.get("resume") is not True:
                raise StateError("같은 이름의 프로젝트가 있습니다. 재개 여부를 확인하세요.")
            project = candidate.resolve(strict=True)
            if not project.is_relative_to(root / "projects"):
                raise StateError("프로젝트 경계를 확인하세요.")
            guide(project)
        else:
            if data.get("resume") is True:
                raise StateError("재개할 프로젝트가 없습니다.")
            project = create_project(root, name, data["idea"], root / "templates", enabled=True)
        binding = binding_path(root, session)
        binding.parent.mkdir(parents=True, exist_ok=True)
        # 같은 세션의 동시 시작이 기존 연결을 덮어쓰지 않는다.
        with binding.open("x", encoding="utf-8") as stream:
            json.dump({"project": project.relative_to(root).as_posix()}, stream)
    return {"workspace": str(root), "session": session, "same_session": True,
            "instructions": str(project / "AGENTS.md"),
            "skill": str(root / "skills/build-loop/SKILL.md"), "guide": guide(project)}


def begin_command(session):
    return '.\\build-loop.cmd begin --session "' + session_key(session) + '"'


def workspace_handle(root, payload):
    """루트 훅을 현재 세션의 프로젝트 정책으로 연결한다."""
    from .harness import handle
    from .policy import deny, write_paths

    root = Path(root).resolve(strict=True)
    cwd = Path(payload.get("cwd", "")).resolve(strict=True)
    if not cwd.is_relative_to(root):
        raise StateError("workspace hook cwd mismatch")
    session = session_key(payload.get("session_id"))
    event = payload.get("hook_event_name")
    tool = str(payload.get("tool_name", "")).split(".")[-1]
    data = payload.get("tool_input", {})
    command = data.get("cmd", data.get("command")) if isinstance(data, dict) else None
    project = bound_project(root, session)
    context = (f"build-loop 다운로드 작업공간. session_id={session}\n"
               f"현재 폴더와 세션을 유지하세요. .build-loop/start-{session}.json에 "
               '{"name":"영문-이름","idea":"사용자 아이디어"}를 작성하고 '
               f"{begin_command(session)} 실행. 기존 프로젝트를 재개할 때만 resume:true 포함. "
               "출력의 instructions와 skill을 읽고 guide의 첫 미완료 작업을 수행하세요. "
               "도구 본체 소스·specs를 조사하거나 사용자에게 폴더 이동을 요구하지 마세요.")
    if event == "SessionStart":
        if project:
            context += f"\n연결된 프로젝트: {project}. begin을 실행해 저장 상태를 복원하세요."
        return {"hookSpecificOutput": {"hookEventName": event, "additionalContext": context}}
    if event == "PreToolUse" and command == begin_command(session) and cwd == root:
        return {}
    if project is None:
        if event == "PreToolUse":
            paths = write_paths(payload)
            allowed = root / ".build-loop" / ("start-" + session + ".json")
            if tool in {"Write", "Edit", "apply_patch"} and paths and all(
                    (cwd / name).resolve() == allowed for name in paths):
                return {}
            if tool in {"Read", "Glob", "Grep"}:
                return {}
            return deny(f"시작 단계에서 허용되지 않은 호출: {tool}. "
                        "요청 파일은 apply_patch로 작성하고 begin을 별도 호출하세요.\n" + context)
        # 아이디어를 아직 받지 않은 세션을 자동 계속시키지 않는다.
        return {}
    # 루트 작업 디렉터리에서 상대 경로를 쓰더라도 프로젝트 경계는 동일하게 검사한다.
    mapped = dict(payload, cwd=str(project))
    if tool in {"Write", "Edit", "MultiEdit", "apply_patch"}:
        paths = write_paths(payload)
        mapped["tool_name"] = "MultiEdit"
        mapped["tool_input"] = {"edits": [{"file_path": str((cwd / name).resolve())} for name in paths]}
    if event == "PreToolUse" and command:
        # Read 도구가 없는 호스트에서도 지침 두 파일만 셸로 읽을 수 있다.
        for path in [project / "AGENTS.md", root / "skills/build-loop/SKILL.md"]:
            expected = f'Get-Content -LiteralPath "{path}" -Encoding UTF8'
            if command == expected:
                return {}
    return handle(project, mapped)
