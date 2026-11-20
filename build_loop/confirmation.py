"""확인할 대상과 버전을 실제 동기 선택 질문에 묶는다."""

from .selection import read_selection
from .state import StateError
from .menu_input import question_matches


def question(target, version, nonce):
    return {"header": "내용 확인", "id": "confirm_" + nonce,
            "question": f"{target} ({version}) 내용을 확인하고 다음 단계로 진행할까요?",
            "options": [{"label": "내용 확인 (추천)", "description": "표시된 대상·버전으로 다음 단계를 진행합니다."},
                        {"label": "수정 필요", "description": "수정할 내용을 반영한 뒤 다시 확인합니다."}]}


def selected(request, path, session, call):
    expected = request.get("question")
    if not expected:
        raise StateError("이전 버전의 확인 요청입니다. 현재 파일로 새 확인 질문을 등록하세요.")
    receipt, actual, answer = read_selection(path, session, call, expected["id"])
    if request.get("thread_id") and session != request["thread_id"]:
        raise StateError("확인을 요청한 스레드와 응답 세션이 다릅니다.")
    if not question_matches(expected, actual) or answer != expected["options"][0]["label"]:
        raise StateError("해당 대상·버전을 확인한 응답이 없습니다. 수정 의견 또는 미응답 상태를 보존합니다.")
    return receipt
