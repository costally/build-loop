"""명시적으로 선택한 로컬 세션의 동기 메뉴 호출·응답 연결. 승인 권한 아님."""

import hashlib
import json
import os
from pathlib import Path

from .state import StateError


def read_selection(path: Path, session_id: str, call_id: str, question_id: str):
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
    if call.get("name", "").split(".")[-1] != "request_user_input" or not turn:
        raise StateError("not a synchronous selection call with a known turn")
    arguments = json.loads(call["arguments"])
    questions = arguments.get("questions", [])
    if len(questions) != 1 or questions[0].get("id") != question_id:
        raise StateError("selection question mismatch")
    output = json.loads(outputs[0]["output"])
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
