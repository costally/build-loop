"""실험용 개발 진입 검사. 준비되지 않은 호스트는 항상 거절한다."""

import argparse
import json
import sys
import sqlite3
from pathlib import Path

from .state import StateStore, StateError
from .workflow import Workflow


def decision(project, payload=None):
    # Only intrinsically read-only native tools bypass the development gate.
    # Shells and arbitrary MCP tools can write, even when their names sound read-only.
    if payload:
        tool = payload.get("tool_name")
        if tool in {"Read", "Glob", "Grep", "request_user_input"}:
            return {}
    try:
        view = Workflow(StateStore(project)).status()
        reason = view["next_action"] + ": " + view["blocked_reason"]
    except (OSError, ValueError, StateError, sqlite3.DatabaseError) as exc:
        reason = "프로젝트 상태를 확인할 수 없습니다: " + type(exc).__name__
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse",
            "permissionDecision": "deny", "permissionDecisionReason": reason}}


def main():
    parser = argparse.ArgumentParser(description="experimental development-only guard")
    parser.add_argument("--project", type=Path, required=True)
    args = parser.parse_args()
    try:
        payload = json.load(sys.stdin)
        if (not isinstance(payload, dict) or payload.get("hook_event_name") != "PreToolUse"
                or not Path(payload.get("cwd", "")).resolve().is_relative_to(args.project.resolve(strict=True))):
            raise ValueError("invalid guard context")
        result = decision(args.project, payload)
    except (OSError, ValueError, TypeError):
        result = {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                         "permissionDecisionReason": "개발 가드 입력을 확인할 수 없습니다."}}
    print(json.dumps(result, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
