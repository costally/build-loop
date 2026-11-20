"""선택한 기존 스레드만 읽는 통합 검사. 모델 턴 생성·승인 추정 없음."""

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from build_loop.probe import observe_user_reply
from build_loop.rpc import ReadOnlyCodex
from build_loop.state import StateStore
from build_loop.workflow import Workflow


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--codex", type=Path, required=True)
    parser.add_argument("--thread", required=True)
    parser.add_argument("--reply", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with ReadOnlyCodex(args.codex) as client:
        receipt = observe_user_reply(client, args.thread, args.reply)
    if not receipt:
        raise RuntimeError("selected user reply not found")
    with tempfile.TemporaryDirectory(prefix="build-loop-flow-") as directory:
        root = Path(directory)
        store = StateStore(root, enabled=True)
        store.initialize()
        flow = Workflow(store)
        flow.ask({"id": "Q-NATIVE-001", "question": "기존 사용자 응답의 저장 연결 검사",
                  "options": [{"label": "진행 (추천)", "description": "저장 연결 검증"},
                              {"label": "중단", "description": "진행 보류"}]}, 0)
        flow.answer("Q-NATIVE-001", args.reply, receipt, 1)
        spec = root / "spec.md"
        spec.write_text("integration-fixture-v1", encoding="utf-8")
        flow.review("fixture-spec", "v1.0", ["spec.md"], 2)
        command = [sys.executable, "-m", "build_loop", "workflow", "--project", str(root), "status"]
        process = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", check=True,
                                 timeout=10, cwd=Path(__file__).resolve().parents[1])
        restored = json.loads(process.stdout)
        assert restored["answers"]["Q-NATIVE-001"]["source"] == receipt
        assert restored["manifest_matches"] is True
        assert restored["development_allowed"] is False
        spec.write_text("changed", encoding="utf-8")
        assert flow.status()["manifest_matches"] is False
        report = {"scope": "real reply read + temporary workflow + independent CLI process",
                  "reply_source": receipt, "new_model_turns_started": 0,
                  "native_selection_ui_test": "not_run", "approval_binding": "not_verified",
                  "checks": {"reply_saved": "passed", "independent_process_resume": "passed",
                             "manifest_change_detection": "passed", "unapproved_development_denied": "passed"},
                  "product_ready": False}
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["checks"]))


if __name__ == "__main__":
    main()
