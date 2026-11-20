"""모델 턴 없이 두 실제 App Server가 생성된 훅을 읽는지 확인한다."""

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from build_loop.director import create_project
from build_loop.rpc import ReadOnlyCodex


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cli", type=Path, required=True)
    parser.add_argument("--pc", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    report = {"scope": "actual native hook discovery; not interactive UI or native tool-denial validation",
              "new_model_turns": 0, "hosts": {}}
    with tempfile.TemporaryDirectory(prefix="build-loop-hosts-") as directory:
        project = create_project(Path(directory), "managed", "integration fixture", root / "templates", enabled=True)
        for name, executable in [("cli", args.cli), ("pc", args.pc)]:
            version = subprocess.run([str(executable), "--version"], capture_output=True, timeout=10, check=True).stdout.decode().strip()
            with ReadOnlyCodex(executable) as client:
                hooks = client.call("hooks/list", {"cwds": [str(project)]})
            def redact(value):
                if isinstance(value, dict):
                    return {k: redact(v) for k, v in value.items() if k not in {"command", "path", "cwd", "sourcePath"}}
                if isinstance(value, list):
                    return [redact(v) for v in value]
                if isinstance(value, str) and (":\\" in value or "python.exe" in value):
                    return "local path redacted"
                return value
            loaded = sum(len(entry.get("hooks", [])) for entry in hooks.get("data", []))
            report["hosts"][name] = {"version": version, "hooks": redact(hooks), "loaded_count": loaded,
                                     "status": "loaded_review_required" if loaded else "not_loaded_project_trust_required"}
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report))
    return 0 if all(host["loaded_count"] for host in report["hosts"].values()) else 2


if __name__ == "__main__":
    raise SystemExit(main())
