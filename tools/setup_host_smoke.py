"""빈 격리 폴더에만 네이티브 호스트 실증 지침·훅을 준비한다."""

import argparse
import json
import subprocess
import sys
from pathlib import Path

SYNC_SELECTION_STEP = "2. 이 질문은 권한·승인 요청이 아닌 선택 UI의 선택적 진단이다. 제공된 도구 목록에 request_user_input이 있으면 반드시 그 동기 도구를 실제 호출한다. questions에는 header=메뉴 확인, id=selection_ui_visible, question=현재 환경에서 선택 메뉴가 보이나요?, options에는 label과 description이 있는 두 객체(보입니다 (추천) / 문제가 있습니다)를 넣는다. request_user_input_async는 이 CLI 실증에서 사용하지 않는다. 동기 도구가 없거나 호출 오류·취소·빈 응답이면 메뉴가 표시됐다고 말하지 말고 미확인 상태와 원인을 기록한 뒤 차단 시험을 진행하지 않는다. 텍스트로 선택지를 대신 출력하거나 도구의 accepted:true를 사용자 답변으로 간주하지 않는다."


def prepare(project: Path):
    project = project.resolve()
    if project.exists():
        raise FileExistsError("existing project is preserved; choose a new empty path")
    source = Path(__file__).resolve().parents[1]
    handler = source / "build_loop/hook_probe.py"
    if not handler.is_file():
        raise FileNotFoundError("hook probe source is missing")
    command = subprocess.list2cmdline([sys.executable, str(handler), "--project", str(project)])
    configuration = {"description": "build-loop isolated host smoke probe only", "hooks": {}}
    for event in ["SessionStart", "PreToolUse", "Stop", "Interrupt"]:
        configuration["hooks"][event] = [{"hooks": [{
            "type": "command", "command": command, "commandWindows": command, "timeout": 5,
        }]}]
    (project / ".codex").mkdir(parents=True)
    (project / "docs").mkdir()
    (project / ".build-loop").mkdir()
    (project / ".codex/hooks.json").write_text(json.dumps(configuration, ensure_ascii=False, indent=2), encoding="utf-8")
    (project / ".codex/config.toml").write_text(
        "[features]\ndefault_mode_request_user_input = true\n", encoding="utf-8",
    )
    (project / ".gitignore").write_text(".build-loop/\n", encoding="utf-8")
    instructions = '''# build-loop 네이티브 호스트 실증

이 폴더는 실제 서비스 개발이 아니라 격리된 실증 프로젝트다. 다른 프로젝트·전역 설정·계정·파일을 바꾸지 않는다. 기존 Codex Flow 명령으로 전환하지 않는다.

사용자가 '시작'하면 다음을 한 단계씩 진행한다.

1. .codex/hooks.json을 확인한다. 훅 검토·신뢰가 필요하면 현재 호스트의 정식 검토 UI를 안내한다. 신뢰 우회 플래그·자동 승인·전역 설정 편집을 하지 않는다.
@@SELECTION_STEP@@
3. 사용자 답변 후 셸에서 Write-Output 'BUILD_LOOP_GUARD_SMOKE'를 정확히 한 번 시도한다. 이 무해한 명령은 시험 훅의 차단 대상이다. 차단을 우회하거나 다른 도구로 재실행하지 않는다. 차단되지 않으면 실패를 기록한다.
4. .build-loop/host-smoke-events.jsonl의 해당 PreToolUse deny 기록과 실제 도구 실패를 대조한다. 핸들러를 수동 실행한 결과는 네이티브 훅 통과 증거가 아니다.
5. docs/host-observation.md에 실제 호스트·선택 UI 결과·사용자 응답·차단 결과·근거를 기록한다. 토큰·원문 대화·전체 명령 입력은 복사하지 않는다. 아직 미확인인 부분을 통과로 채우지 않는다.
6. 중단/재개 시험은 사용자가 실제로 중단·재개한 뒤 수행한다. 먼저 통과했다고 선언하거나 새 AI 작업을 자동 생성하지 않는다.

이 단계는 제품 완성·설치 완료가 아니다. 사용자 응답 관찰은 실제 스펙 승인 바인딩을 대신하지 않는다. 이후 원래 build-loop 개발 작업에 결과를 전달한다.
'''
    (project / "AGENTS.md").write_text(instructions.replace("@@SELECTION_STEP@@", SYNC_SELECTION_STEP), encoding="utf-8")
    return {"project": str(project), "source": str(source), "hook_events": list(configuration["hooks"]),
            "native_validation": "pending actual host load and trust review", "global_configuration_changed": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, required=True)
    print(json.dumps(prepare(parser.parse_args().project), ensure_ascii=False, indent=2))
