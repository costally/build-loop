"""메뉴 호출의 호스트 표현과 승인 대상의 의미를 분리한다."""
import json


def menu_tool_name(name):
    name = str(name).split(".")[-1]
    # 일부 네임스페이스 경로는 구분자 없이 flat_tool_name으로 직렬화된다.
    return "request_user_input" if name in {
        "request_user_input", "functionsrequest_user_input", "functions__request_user_input"
    } else name


def menu_arguments(data):
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except ValueError:
            return {}
    if not isinstance(data, dict):
        return {}
    if "questions" in data:
        return data
    # 호스트 어댑터가 인수를 command/arguments로 감싼 경우만 해제한다.
    for key in ("arguments", "command"):
        if isinstance(data.get(key), str):
            try:
                value = json.loads(data[key])
            except ValueError:
                continue
            if isinstance(value, dict) and "questions" in value:
                return value
    return data


def question_matches(expected, actual):
    if not isinstance(expected, dict) or not isinstance(actual, dict):
        return False
    # Codex는 자유 입력 옵션(isOther)을 true로 정규화한다. 이는 승인 응답이 아니다.
    # 질문·선택지 내용은 그대로 대조하며 비밀 입력 모드 전환은 허용하지 않는다.
    if not isinstance(actual.get("isOther", False), bool) or actual.get("isSecret", False) is not False:
        return False
    allowed = set(expected) | {"header", "isOther", "isSecret"}
    return (not set(actual) - allowed and
            all(actual.get(key) == value for key, value in expected.items()))
