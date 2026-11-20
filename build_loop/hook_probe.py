"""격리 프로젝트의 실제 훅 로드·차단을 확인하는 실증용 핸들러."""

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

MARKER = "BUILD_LOOP_GUARD_SMOKE"


def denial_reason(payload):
    if payload.get("hook_event_name") != "PreToolUse":
        return None
    if str(payload.get("tool_name", "")).rsplit(".", 1)[-1] == "request_user_input_async":
        return "async_selection_unverified"
    if MARKER in json.dumps(payload.get("tool_input", {})):
        return "guard_marker"
    return None


def handle(payload, project: Path):
    root = project.resolve(strict=True)
    if not isinstance(payload, dict) or not isinstance(payload.get("hook_event_name"), str):
        raise ValueError("malformed hook input")
    cwd = Path(payload.get("cwd", "")).resolve()
    if not cwd.is_relative_to(root):
        raise ValueError("hook project mismatch")
    event = payload["hook_event_name"]
    reason = denial_reason(payload)
    denied = reason is not None
    raw_tool = str(payload.get("tool_name", ""))
    tool = raw_tool if re.fullmatch(r"[A-Za-z0-9_./:-]{0,128}", raw_tool) else "unknown"
    observation = {
        "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
        "event": event, "tool": tool, "decision": "deny" if denied else "observe",
        "marker_present": reason == "guard_marker", "reason_code": reason,
        "session_sha256": hashlib.sha256(str(payload.get("session_id", "")).encode()).hexdigest(),
    }
    destination = root / ".build-loop" / "host-smoke-events.jsonl"
    if not destination.resolve().is_relative_to(root):
        raise ValueError("evidence path escapes project")
    destination.parent.mkdir(exist_ok=True)
    with destination.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(observation, ensure_ascii=False) + "\n")
    return denied


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, required=True)
    args = parser.parse_args()
    try:
        payload = json.load(sys.stdin)
        denied = handle(payload, args.project)
    except (ValueError, OSError, TypeError):
        print("build-loop smoke: invalid input or project; original data preserved", file=sys.stderr)
        return 2
    if denied:
        message = (
            "build-loop smoke: async selection is unverified in this CLI; use request_user_input if available, otherwise preserve the question and report unavailable"
            if denial_reason(payload) == "async_selection_unverified" else
            "build-loop smoke: requested probe command blocked; do not bypass"
        )
        # Windows PowerShell -Command can translate native exit 2 into 1.
        # Return the documented structured decision with a successful exit.
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse", "permissionDecision": "deny",
            "permissionDecisionReason": message,
        }}))
        return 0
    print("{}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
