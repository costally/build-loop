"""로컬 실증 시험·의존성·소스 해시를 남긴다. 제품 TC 통과와 구분한다."""

import argparse
import ast
import hashlib
import io
import json
import platform
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    sources = sorted(root.rglob("*.py"))
    unexpected = []
    for path in sources:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            modules = []
            if isinstance(node, ast.Import):
                modules = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                modules = [node.module.split(".")[0]]
            unexpected.extend(f"{path.relative_to(root)}:{node.lineno}:{m}" for m in modules
                              if m not in sys.stdlib_module_names and m != "build_loop")
    suite = unittest.defaultTestLoader.discover(str(root / "tests"))
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    report = {
        "scope": "local unit/SQLite/process tests; not full native-host product QA",
        "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
        "python": platform.python_version(), "platform": platform.system(),
        "tests_run": result.testsRun, "failures": len(result.failures),
        "errors": len(result.errors), "skipped": len(result.skipped),
        "external_imports": unexpected, "log": stream.getvalue(),
        "source_sha256": {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources},
        "product_tc_pass_claims": [],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ["tests_run", "failures", "errors", "external_imports"]}))
    return 0 if result.wasSuccessful() and not unexpected else 1


if __name__ == "__main__":
    raise SystemExit(main())
