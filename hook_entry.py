"""Codex 훅은 작업 디렉터리와 무관하게 설치된 본체를 호출한다."""
import argparse
import json
import sys

from build_loop.harness import handle
from build_loop.policy import deny

parser = argparse.ArgumentParser()
parser.add_argument("--project", required=True)
args = parser.parse_args()
payload = {}
try:
    payload = json.load(sys.stdin)
    result = handle(args.project, payload)
except Exception:
    event = payload.get("hook_event_name") if isinstance(payload, dict) else None
    result = deny("build-loop 상태 검사 실패. 실행을 차단하고 원본을 보존했습니다.") if event in {None, "PreToolUse"} else {
        "continue": False, "stopReason": "build-loop 상태 검사 실패. 원본을 보존하고 필요한 조치를 확인하세요."}
print(json.dumps(result, ensure_ascii=True))
