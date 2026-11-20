"""소스 체크아웃 또는 설치 디렉터리에서 동일 CLI를 실행한다."""

from build_loop.__main__ import main
from build_loop.state import StateError
from build_loop.rpc import RpcError
import json
import subprocess


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (StateError, RpcError, OSError, ValueError, subprocess.SubprocessError) as error:
        print(json.dumps({"blocked": True, "reason": str(error)}, ensure_ascii=True))
        raise SystemExit(2)
