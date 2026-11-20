"""허용 목록으로 신규 독립 배포 폴더를 만든다. 사용자 자료를 포함하지 않는다."""

import argparse
import shutil
from pathlib import Path


def export(destination, specs):
    source = Path(__file__).resolve().parents[1]
    destination = Path(destination).resolve()
    if destination.exists():
        raise FileExistsError("existing release directory preserved")
    destination.mkdir(parents=True)
    entries = ["build_loop", "tests", "tools", "templates", "skills", ".github", "launch.py", "build-loop.cmd",
               "install.ps1", "LICENSE", "THIRD_PARTY.md"]
    for name in entries:
        origin = source / name
        target = destination / name
        if origin.is_dir():
            shutil.copytree(origin, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        else:
            shutil.copyfile(origin, target)
    shutil.copyfile(source / ("RELEASE.md" if (source / "RELEASE.md").is_file() else "README.md"), destination / "README.md")
    (destination / ".gitignore").write_text("__pycache__/\n*.pyc\n.build-loop/\nchecks.json\nprojects/\n", encoding="utf-8")
    (destination / "AGENTS.md").write_text(
        "# build-loop 개발\n" +
        "specs/의 승인 요구사항을 기준으로 본체를 개발한다. 사용자 프로젝트와 원본 로그는 이 저장소에 넣지 않는다. "
        "수정 후 tools/run_checks.py를 실행한다. 개별 진단을 목표로 늘리지 말고 아이디어부터 결과물까지 흐름을 유지한다.\n",
        encoding="utf-8")
    target_specs = destination / "specs"
    target_specs.mkdir()
    for name in ["00-brief.md", "01-prd.md", "02-policy.md", "03-ia.md", "05-qa-tc.md"]:
        shutil.copyfile(Path(specs) / name, target_specs / name)
    shutil.copytree(Path(specs) / "04-screens", target_specs / "04-screens")
    return destination


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--specs", type=Path, required=True)
    args = parser.parse_args()
    print(export(args.destination, args.specs))
