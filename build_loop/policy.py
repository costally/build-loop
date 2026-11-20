"""저장된 단계·승인에 따른 네이티브 도구 허용 정책."""

import re
import json
import sqlite3
import sys
from pathlib import Path

from .state import StateStore, StateError
from .workflow import Workflow
from .menu_input import menu_tool_name, menu_arguments, question_matches


def deny(reason):
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                   "permissionDecisionReason": reason}}


def control_command(project, request):
    return f'& "{sys.executable}" "{Path(__file__).resolve().parents[1] / "control.py"}" --project "{project}" --request "{request}"'


def is_control_command(project, text):
    if not isinstance(text, str):
        return False
    match = re.search(r' --request "([A-Za-z0-9_-]+\.json)"$', text)
    return bool(match and text == control_command(project, match[1]))


def write_paths(payload):
    data = payload.get("tool_input", {})
    if isinstance(data, str):
        patch = data
    elif isinstance(data, dict):
        path = data.get("file_path", data.get("path"))
        if isinstance(path, str):
            return [path]
        edits = data.get("edits")
        if isinstance(edits, list):
            return [edit.get("file_path", "") for edit in edits if isinstance(edit, dict)]
        # Codex의 네이티브 훅은 apply_patch 원문을 command 필드로 전달한다.
        # patch/input은 직접 도구 호출과 기존 통합 경로의 호환 형식이다.
        patch = data.get("command", data.get("patch", data.get("input", "")))
    else:
        return []
    return re.findall(r"^\*\*\* (?:Add File|Update File|Delete File|Move to): (.+)$", patch, re.M) if isinstance(patch, str) else []


def decision(project, payload=None):
    root = Path(project).resolve(strict=True)
    if not payload:
        return deny("현재 호출을 확인할 수 없습니다.")
    tool = menu_tool_name(payload.get("tool_name", ""))
    data = payload.get("tool_input", {})
    if tool == "request_user_input":
        data = menu_arguments(data)
    if tool in {"Read", "Glob", "Grep"}:
        return {}
    if tool in {"Bash", "exec_command"}:
        command = data.get("command", data.get("cmd")) if isinstance(data, dict) else None
        return {} if is_control_command(root, command) else deny("임의 셸 실행은 차단했습니다. control 요청으로 조회·검증을 수행하세요.")
    try:
        store = StateStore(root)
        flow = Workflow(store).status()
        if flow["paused"]:
            # 재개 응답을 제어기에 전달할 요청 파일은 중단 상태에서도 작성해야 한다.
            # 상태 변경 권한은 제어기의 실제 사용자 응답 검증이 담당한다.
            paths = write_paths(payload) if tool in {"Write", "Edit", "MultiEdit", "apply_patch"} else []
            if paths and all((root / name).resolve().is_relative_to(root)
                             and re.fullmatch(r"\.build-loop/requests/[A-Za-z0-9_-]+\.json",
                                              (root / name).resolve().relative_to(root).as_posix())
                             for name in paths):
                return {}
            if tool == "request_user_input":
                from .director import guide
                question = guide(root).get("question")
                questions = data.get("questions", [])
                if question and isinstance(questions, list) and len(questions) == 1 and question_matches(question, questions[0]):
                    return {}
                return deny("재개 메뉴 입력이 저장된 재개 질문과 일치하지 않습니다. "
                            f"tool={payload.get('tool_name')}; input_keys={sorted(data)}; "
                            f"question_keys={sorted(questions[0]) if isinstance(questions, list) and questions and isinstance(questions[0], dict) else []}. "
                            "guide의 question을 수정 없이 questions:[question]으로 전달하세요.")
            return deny("중단 상태에서 허용되지 않은 도구입니다. "
                        f"tool={payload.get('tool_name')}; "
                        "guide 조회와 재개 메뉴, 제어 요청 파일 작성만 허용됩니다.")
        if tool == "request_user_input":
            pending = flow["pending_question"]
            if not pending:
                from .director import guide
                current = guide(root)
                if current.get("action_kind") in {"revise", "change_required"}:
                    return deny("사용자의 수정 의견을 먼저 반영한 뒤 새 확인 질문을 등록하세요.")
                pending = current.get("question") or (current.get("review") or {}).get("question")
                if not pending and current.get("stage") == "spec_review" and not flow["approval"]:
                    pending = (flow["review"] or {}).get("question")
                if not pending and current.get("stage") == "delivery":
                    requests = [json.loads(e["payload"]) for e in store.snapshot()["events"]
                                if e["kind"] == "delivery_confirmation_requested"]
                    if requests:
                        from .delivery import Delivery
                        last = requests[-1]
                        if last["code"] == Delivery(store).code_version():
                            pending = last.get("question")
            questions = data.get("questions", []) if isinstance(data, dict) else []
            if not pending or not isinstance(questions, list) or len(questions) != 1 or not question_matches(pending, questions[0]):
                return deny("저장된 현재 질문·선택지와 일치하는 동기 메뉴만 표시할 수 있습니다.")
            return {}
        if tool not in {"Write", "Edit", "MultiEdit", "apply_patch"}:
            return deny("이 단계에서 허용되지 않은 도구입니다. 현재 guide의 작업으로 돌아가세요.")
        paths = write_paths(payload)
        if not paths or any(not isinstance(name, str) or not name for name in paths):
            return deny("쓰기 대상 경로를 확인할 수 없습니다.")
        from .director import guide, stage_records
        view = guide(root)
        _, confirmed = stage_records(store.snapshot())
        protected = set(flow["review"]["files"] if flow["review"] else {})
        protected.update(name for stage in confirmed.values() for name in stage["files"])
        for name in paths:
            path = (root / name).resolve()
            if not path.is_relative_to(root):
                return deny("선택한 프로젝트 밖의 쓰기를 차단했습니다.")
            relative = path.relative_to(root).as_posix()
            if re.fullmatch(r"\.build-loop/requests/[A-Za-z0-9_-]+\.json", relative):
                continue
            if relative in protected:
                return deny("확인·승인된 스펙입니다. CR을 작성하고 사용자 변경 승인을 먼저 연결하세요.")
            if relative.startswith("docs/"):
                stage_files = {"brief": {"docs/00-brief.md"}, "prd": {"docs/01-prd.md"},
                               "policy": {"docs/02-policy.md"}, "design": {"docs/03-ia.md"}, "qa": {"docs/05-qa-tc.md"}}
                supporting = relative in {"docs/CHANGELOG.md", "docs/NEXT-STEP.md"} or relative.startswith(("docs/minutes/", "docs/changes/open/"))
                design = view["stage"] == "design" and relative.startswith(("docs/04-screens/", "docs/04-interactions/"))
                if supporting or design or relative in stage_files.get(view["stage"], set()):
                    continue
            if relative.startswith("code/") and flow["development_allowed"]:
                from .delivery import Delivery
                delivery = Delivery(store).status()
                if delivery.get("current_task") and delivery["current_task"]["result"] != "unknown":
                    active = Delivery(StateStore(root, enabled=True))
                    active.prepare_recovery(delivery["current_task"], delivery["revision"])
                    continue
            return deny("현재 단계·승인·개발 작업과 맞지 않는 쓰기입니다. guide의 다음 작업으로 돌아가세요.")
        return {}
    except (OSError, ValueError, StateError, sqlite3.DatabaseError, KeyError, TypeError):
        return deny("상태 또는 파일을 확인할 수 없습니다. 원본을 보존하고 진행을 차단했습니다.")
