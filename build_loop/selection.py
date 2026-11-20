"""명시적으로 선택한 로컬 세션의 동기 메뉴 호출·응답 연결. 승인 권한 아님."""

import hashlib
import json
from pathlib import Path

from .state import StateError


def read_selection(path: Path, session_id: str, call_id: str, question_id: str):
    with path.open(encoding="utf-8") as stream:
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
    if not isinstance(values, list) or len(values) != 1 or not isinstance(values[0], str) or not values[0].strip():
        raise StateError("selection answer empty or ambiguous")
    question = questions[0]
    digest = hashlib.sha256(json.dumps(question, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    receipt = {"kind": "selection_observation", "authority": "observation_only",
            "session_id": session_id, "turn_id": turn, "call_id": call_id,
            "question_id": question_id, "question_sha256": digest,
            "answer_sha256": hashlib.sha256(values[0].encode()).hexdigest()}
    return receipt, question, values[0]


def observe_selection(path: Path, session_id: str, call_id: str, question_id: str):
    return read_selection(path, session_id, call_id, question_id)[0]
