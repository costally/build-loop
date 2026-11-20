"""프로젝트에 네이티브 수명주기 훅을 연결한다."""

import ast
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from .policy import decision, write_paths, control_command
from .menu_input import menu_tool_name
from .state import StateStore, StateError


def attach(project):
    project = Path(project).resolve(strict=True)
    StateStore(project).snapshot()
    config = project / ".codex/hooks.json"
    if config.exists():
        raise StateError("기존 훅을 보존했습니다. 자동 덮어쓰기는 하지 않습니다.")
    config.parent.mkdir(exist_ok=True)
    entry = Path(__file__).resolve().parents[1] / "hook_entry.py"
    command = subprocess.list2cmdline([sys.executable, str(entry), "--project", str(project)])
    hooks = {event: [{"hooks": [{"type": "command", "command": command, "commandWindows": command,
                                "timeout": 3 if event == "Interrupt" else 10}]}]
             for event in ["SessionStart", "PreToolUse", "PostToolUse", "Stop", "Interrupt"]}
    config.write_text(json.dumps({"hooks": hooks}, ensure_ascii=False, indent=2), encoding="utf-8")
    requests = project / ".build-loop/requests"
    requests.mkdir(exist_ok=True)
    (requests / "guide.json").write_text('{"action":"guide"}', encoding="utf-8")
    return {"configured": True, "trust": "normal host review required", "command": control_command(project, "guide.json")}


def handle(project, payload):
    from .director import guide
    root = Path(project).resolve(strict=True)
    event = payload.get("hook_event_name")
    if not isinstance(payload.get("cwd"), str) or not Path(payload["cwd"]).resolve().is_relative_to(root):
        raise StateError("hook project mismatch")
    store = StateStore(root, enabled=True, timeout=1)
    stop_signature, stop_repeats = None, 0
    if event == "PreToolUse":
        result = decision(root, payload)
    elif event == "Interrupt":
        snapshot = store.snapshot()
        if not snapshot["paused"]:
            store.pause(snapshot["revision"], "user_interrupt")
        result = {}
    else:
        view = guide(root)
        context = f"build-loop: {view.get('stage')} / {view.get('action')}\n상태 조회: {control_command(root, 'guide.json')}"
        if event == "SessionStart":
            result = {"hookSpecificOutput": {"hookEventName": event, "additionalContext": context}}
        elif event == "PostToolUse":
            errors = []
            for name in write_paths(payload):
                path = (root / name).resolve()
                if path.is_relative_to(root) and path.suffix == ".py" and path.is_file():
                    try:
                        ast.parse(path.read_text(encoding="utf-8-sig"))
                    except (SyntaxError, UnicodeError):
                        errors.append(path.relative_to(root).as_posix())
            if errors or view.get("action_kind") == "change_required":
                result = {"decision": "block", "reason": "문법 또는 승인 스펙 불일치: " + ", ".join(errors),
                          "hookSpecificOutput": {"hookEventName": event, "additionalContext": context}}
            else:
                result = {"hookSpecificOutput": {"hookEventName": event, "additionalContext": context}}
        elif event == "Stop":
            waiting = view.get("stage") in {"paused", "interview", "change_review", "recovery_review", "complete"} or view.get("action_kind") == "change_required"
            waiting = waiting or (view.get("action_kind") == "review" and bool(view.get("review")))
            if view["stage"] == "spec_review":
                from .workflow import Workflow
                flow = Workflow(store).status()
                waiting = waiting or bool(flow["review"]) or flow["manifest_matches"] is False
            delivery_action = view.get("delivery", {}).get("next_action", "")
            waiting = waiting or "미확인" in delivery_action or "3회 실패" in delivery_action
            if view["stage"] == "delivery":
                from .delivery import Delivery
                events = store.snapshot()["events"]
                requests = [json.loads(entry["payload"]) for entry in events if entry["kind"] == "delivery_confirmation_requested"]
                if requests:
                    latest = requests[-1]
                    answered = any(entry["kind"] == latest["kind"] + "_user_confirmed" and json.loads(entry["payload"]).get("reply") == latest["reply"] for entry in events)
                    answered = answered or any(entry["kind"] == "review_feedback" and json.loads(entry["payload"])["question_id"] == latest["question"]["id"] for entry in events)
                    expected_action = "합의한 환경에서 실제 결과물을 시연하고 사용자 확인" if latest["kind"] == "demo" else "사용자 최종 완료 승인 대기"
                    waiting = waiting or (not answered and latest["code"] == Delivery(store).code_version()
                                          and latest["plan_sha256"] == view["delivery"].get("plan_sha256")
                                          and delivery_action == expected_action)
            artifact = root / view["artifact"] if view.get("artifact") else None
            progress = {"revision": view["revision"], "stage": view["stage"], "action": view.get("action_kind"),
                        "artifact": hashlib.sha256(artifact.read_bytes()).hexdigest() if artifact and artifact.is_file() else None}
            if view["stage"] == "delivery":
                from .delivery import Delivery
                progress["code"] = Delivery(store).code_version()
            stop_signature = hashlib.sha256(json.dumps(progress, sort_keys=True).encode()).hexdigest()
            log = root / ".build-loop/hook-events.jsonl"
            previous = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line.strip()] if log.is_file() else []
            last = next((entry for entry in reversed(previous) if entry["event"] == "Stop"), {})
            stop_repeats = last.get("stop_repeats", 0) + 1 if payload.get("stop_hook_active") and last.get("stop_signature") == stop_signature else 1
            if waiting:
                result = {}
            elif stop_repeats >= 3:
                snapshot = store.snapshot()
                if not snapshot["paused"]:
                    store.pause(snapshot["revision"], "guard_stalled")
                result = {"systemMessage": "동일 단계에서 진행이 정체되어 상태를 보존했습니다. 필요한 조치를 사용자에게 알리세요."}
            else:
                result = {"decision": "block", "reason": context + "\n사용자 결정 대기가 아니라면 현재 작업을 수행하고 다시 검사하세요. 완료를 추정하지 마세요."}
        else:
            raise StateError("unsupported hook event")
    destination = root / ".build-loop/hook-events.jsonl"
    if not destination.resolve().is_relative_to(root):
        raise StateError("evidence path escapes project")
    record = {"at": datetime.now(timezone.utc).isoformat(), "event": event,
              "tool": str(payload.get("tool_name", ""))[:128],
              "tool_input_sha256": hashlib.sha256(json.dumps(payload.get("tool_input", {}), sort_keys=True).encode()).hexdigest(),
              "tool_call_sha256": hashlib.sha256(str(payload.get("tool_use_id", payload.get("tool_call_id", ""))).encode()).hexdigest(),
              "session_sha256": hashlib.sha256(str(payload.get("session_id", "")).encode()).hexdigest(),
              "decision": result.get("hookSpecificOutput", {}).get("permissionDecision", result.get("decision", "observe"))}
    if event == "Stop":
        record.update(stop_signature=stop_signature, stop_repeats=stop_repeats)
    tool = menu_tool_name(record["tool"])
    if record["decision"] == "observe" and (
        (event == "PostToolUse" and tool == "request_user_input")
        or (event == "PreToolUse" and tool in {"Bash", "exec_command"})
    ):
        session, transcript = payload.get("session_id"), payload.get("transcript_path")
        if isinstance(session, str) and session and isinstance(transcript, str) and transcript:
            record["selection_log"] = {"session": session, "path": transcript}
            if event == "PostToolUse":
                result["hookSpecificOutput"]["additionalContext"] += (
                    "\n메뉴가 응답을 반환했다면 control-requests.md의 해당 *-selection/selection 요청으로 연결하세요. "
                    "제어기가 호스트 기록에서 호출과 응답을 찾습니다."
                )
    with destination.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record) + "\n")
    return result
