"""승인한 로컬 저장 규모에서 상태 조회·답 저장·재개 안내를 측정한다."""

import argparse
import hashlib
import json
import platform
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from build_loop.state import StateStore
from build_loop.workflow import Workflow
from build_loop.director import guide


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    samples = {"snapshot": [], "answer": [], "guide": []}
    with tempfile.TemporaryDirectory(prefix="build-loop-benchmark-") as directory:
        stores = []
        for index in range(100):
            project = Path(directory) / str(index)
            (project / "docs").mkdir(parents=True)
            (project / "code").mkdir()
            store = StateStore(project, enabled=True)
            store.initialize()
            with store._write(0) as (db, state):
                for number in range(1, 1001):
                    payload = {"name": str(index), "idea": "benchmark fixture"} if number == 1 else {}
                    kind = "project_started" if number == 1 else "benchmark_fixture"
                    state["revision"] = store._event(db, state, kind, payload)
            stores.append(store)
        for store in stores:
            start = time.perf_counter()
            revision = store.snapshot()["revision"]
            samples["snapshot"].append(time.perf_counter() - start)
            flow = Workflow(store)
            flow.ask({"id": "Q-1", "question": "측정", "options": [{"label": "예 (추천)", "description": "측정"},
                       {"label": "아니오", "description": "중단"}]}, revision)
            receipt = {"kind": "user_reply_observation", "transport": "codex_app_server_stdio", "thread_id": "fixture",
                       "turn_id": "fixture", "item_id": "fixture", "text_sha256": hashlib.sha256("예".encode()).hexdigest()}
            start = time.perf_counter()
            flow.answer("Q-1", "예", receipt, revision + 1)
            samples["answer"].append(time.perf_counter() - start)
            start = time.perf_counter()
            guide(store.project)
            samples["guide"].append(time.perf_counter() - start)
        report = {"scope": "local state operations; excludes model/network/UI and lock contention",
                  "projects": 100, "initial_events_each": 1000, "samples_each": 100,
                  "os": platform.platform(), "python": platform.python_version(), "sqlite": sqlite3.sqlite_version,
                  "storage_drive": Path(directory).anchor, "processor": platform.processor(),
                  "p95_seconds": {name: sorted(values)[94] for name, values in samples.items()}}
        report["passed"] = all(value <= 2 for value in report["p95_seconds"].values())
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
