"""네이티브 PreToolUse 정책의 독립 실행 진입점."""

import argparse
import json
import sys
from pathlib import Path

from .policy import decision


def main():
    parser = argparse.ArgumentParser(description="build-loop PreToolUse guard")
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
