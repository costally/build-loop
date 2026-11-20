"""명시적으로 선택한 로컬 세션의 동기 메뉴 호출·응답 연결. 승인 권한 아님."""

import hashlib
import json
import os
from pathlib import Path

from .menu_input import menu_arguments, menu_tool_name, question_matches
from .state import StateError


def session_records(path: Path, session_id: str):
    home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
    sessions = home / "sessions"
    actual = path.resolve(strict=True)
    if actual.suffix != ".jsonl" or not actual.is_relative_to(sessions.resolve()):
        raise StateError("응답은 현재 호스트의 Codex sessions 기록에서만 읽을 수 있습니다.")
    with actual.open(encoding="utf-8") as stream:
        records = [json.loads(line) for line in stream if line.strip()]
    metadata = [r.get("payload", {}) for r in records if r.get("type") == "session_meta"]
    if len(metadata) != 1 or metadata[0].get("id") != session_id:
        raise StateError("selection session mismatch")
    return records


def find_selection(project, expected, thread=None):
    """Resolve a menu completed in this project using only host hook and transcript evidence."""
    log = (project / ".build-loop/hook-events.jsonl").resolve()
    if not log.is_relative_to(project) or not log.is_file():
        raise StateError("현재 프로젝트에서 선택 메뉴 실행 기록을 찾지 못했습니다.")
    with log.open(encoding="utf-8") as stream:
        records = [json.loads(line) for line in stream if line.strip()]
    sessions = {}
    completed = {}
    completed_sessions = set()
    for record in records:
        key = record["session_sha256"]
        if "selection_log" in record:
            source = record["selection_log"]
            if not thread or source["session"] == thread:
                sessions[key] = source
        if (record["event"] == "PostToolUse" and record["decision"] == "observe"
                and menu_tool_name(record["tool"]) == "request_user_input"):
            completed[key, record["tool_call_sha256"]] = record["at"]
            completed_sessions.add(key)

    matches = []
    for key, source in sessions.items():
        if key not in completed_sessions:
            continue
        path = Path(source["path"])
        session = source["session"]
        if not path.is_file():
            continue
        for row in session_records(path, session):
            call = row.get("payload", {})
            if (row.get("type") != "response_item" or call.get("type") != "function_call"
                    or menu_tool_name(call.get("name")) != "request_user_input"):
                continue
            call_id = call.get("call_id")
            if not isinstance(call_id, str) or not call_id:
                continue
            time = completed.get((key, hashlib.sha256(call_id.encode()).hexdigest()))
            if not time:
                continue
            questions = menu_arguments(call.get("arguments")).get("questions")
            if isinstance(questions, list) and len(questions) == 1 and question_matches(expected, questions[0]):
                matches.append((time, path, session, call_id))
    if not matches:
        raise StateError("현재 질문의 완료된 메뉴 기록이 없습니다. 실제 메뉴의 응답을 확인하세요.")
    _, path, session, call_id = max(matches)
    return path, session, call_id


def read_selection(path: Path, session_id: str, call_id: str, question_id: str):
    records = session_records(path, session_id)
    calls, outputs = [], []
    turn_id = None
    for record in records:
        payload = record.get("payload", {})
        if record.get("type") == "turn_context":
            turn_id = payload.get("turn_id")
        if record.get("type") != "response_item" or payload.get("call_id") != call_id:
            continue
        if payload.get("type") == "function_call":
            calls.append((payload, turn_id))
        elif payload.get("type") == "function_call_output":
            outputs.append(payload)
    if len(calls) != 1 or len(outputs) != 1:
        raise StateError("selection call/output is missing or ambiguous")
    call, turn = calls[0]
    if menu_tool_name(call.get("name")) != "request_user_input" or not turn:
        raise StateError("not a synchronous selection call with a known turn")
    arguments = menu_arguments(call["arguments"])
    questions = arguments.get("questions", [])
    if len(questions) != 1 or questions[0].get("id") != question_id:
        raise StateError("selection question mismatch")
    try:
        output = json.loads(outputs[0]["output"])
    except (TypeError, ValueError) as error:
        raise StateError("선택 메뉴에 유효한 사용자 응답이 없습니다.") from error
    answers = output.get("answers", {})
    if set(answers) != {question_id}:
        raise StateError("selection answer missing or unrelated")
    values = answers[question_id].get("answers", [])
    if not isinstance(values, list) or not values or any(not isinstance(value, str) for value in values):
        raise StateError("selection answer empty or ambiguous")
    if len(values) == 1 and values[0].strip() and values[0] not in {"None of the above", "Other"}:
        text = values[0]
    elif len(values) == 2 and values[1].startswith("user_note:") and values[1].split(":", 1)[1].strip():
        note = values[1].split(":", 1)[1].strip()
        text = note if values[0] in {"None of the above", "Other"} else values[0] + "\n의견: " + note
    else:
        raise StateError("selection answer empty or ambiguous")
    question = questions[0]
    digest = hashlib.sha256(json.dumps(question, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    receipt = {"kind": "selection_observation", "authority": "observation_only",
            "session_id": session_id, "turn_id": turn, "call_id": call_id,
            "question_id": question_id, "question_sha256": digest,
            "answer_sha256": hashlib.sha256(text.encode()).hexdigest()}
    return receipt, question, text


def observe_selection(path: Path, session_id: str, call_id: str, question_id: str):
    return read_selection(path, session_id, call_id, question_id)[0]
