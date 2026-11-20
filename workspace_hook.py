"""다운로드한 폴더에서 시작하는 세션용 훅 진입점."""
import json
import sys
from pathlib import Path

from build_loop.policy import deny
from build_loop.workspace import workspace_handle

payload = {}
try:
    payload = json.load(sys.stdin)
    result = workspace_handle(Path(__file__).resolve().parent, payload)
except Exception:
    event = payload.get("hook_event_name") if isinstance(payload, dict) else None
    result = deny("build-loop 작업공간 연결 실패. 세션 ID와 저장 상태를 확인하세요.") if event in {None, "PreToolUse"} else {
        "continue": False, "stopReason": "build-loop 작업공간 연결 실패. 저장 자료를 보존했습니다."}
print(json.dumps(result, ensure_ascii=True))
