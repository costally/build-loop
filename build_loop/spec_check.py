"""승인 전 스펙 세트의 구조 검사. 내용 검토나 사용자 승인을 대신하지 않는다."""

import re
from pathlib import Path

from .workflow import manifest


def inspect_spec(project, docs="docs"):
    root = Path(project).resolve(strict=True)
    directory = (root / docs).resolve(strict=True)
    if not directory.is_relative_to(root) or not directory.is_dir():
        raise ValueError("spec directory escapes project")
    issues, files, texts = [], [], {}
    required = ["00-brief.md", "01-prd.md", "02-policy.md", "03-ia.md", "05-qa-tc.md"]
    def read(path):
        if not path.resolve().is_relative_to(root):
            issues.append(f"outside_project:{path.name}")
            return ""
        if not path.is_file():
            issues.append(f"missing:{path.name}")
            return ""
        text = path.read_text(encoding="utf-8")
        files.append(path.relative_to(root).as_posix())
        if not text.strip():
            issues.append(f"empty:{path.name}")
        return text
    for name in required:
        texts[name] = read(directory / name)
    defined = {}
    for prefix, name in [("REQ", "01-prd.md"), ("NFR", "01-prd.md"),
                         ("POL", "02-policy.md"), ("TC", "05-qa-tc.md")]:
        matches = re.findall(r"^###\s+(" + prefix + r"-\d+)\b", texts[name], re.M)
        if prefix == "NFR":
            matches = re.findall(r"^\|\s*(NFR-\d+)\s*\|", texts[name], re.M)
        defined[prefix] = set(matches)
        if prefix != "NFR" and not matches:
            issues.append(f"no_definitions:{prefix}")
        if len(matches) != len(set(matches)):
            issues.append(f"duplicate_definition:{prefix}")
    screens = sorted((directory / "04-screens").glob("SCR-*.md"))
    interactions = sorted((directory / "04-interactions").glob("REQ-*.md"))
    defined["SCR"] = set()
    if not screens and not interactions:
        issues.append("screen_or_nonvisual_contract_required")
    for path in interactions:
        text = read(path)
        texts[path.name] = text
        compact = text.replace(" ", "")
        for marker in ["입력", "출력", "실행", "기본", "실행중", "입력없음", "실패", "오류", "POL-", "TC-"]:
            if marker not in compact:
                issues.append(f"missing_interaction_element:{path.name}:{marker}")
        req = re.match(r"REQ-\d+", path.name)[0]
        if req not in text or path.name not in texts["03-ia.md"]:
            issues.append(f"unlinked_interaction:{path.name}")
    index = read(directory / "04-screens" / "index.html") if screens else ""
    for path in screens:
        screen = path.name.split("-", 2)
        sid = "-".join(screen[:2])
        if sid in defined["SCR"]:
            issues.append(f"duplicate_definition:{sid}")
        defined["SCR"].add(sid)
        md = read(path)
        html = read(path.with_suffix(".html"))
        texts[path.name] = md
        if path.with_suffix(".html").name not in index:
            issues.append(f"not_indexed:{sid}")
        for state in ["기본", "로딩", "빈", "에러"]:
            if state not in md:
                issues.append(f"missing_state:{sid}:{state}")
        html_components = set(re.findall(r"\[(\d+)\]", html)) | set(re.findall(r'data-component=["\x27](\d+)["\x27]', html))
        if set(re.findall(r"\[(\d+)\]", md)) != html_components:
            issues.append(f"component_mismatch:{sid}")
    all_text = "\n".join(texts.values())
    for prefix, number in set(re.findall(r"\b(REQ|NFR|POL|SCR|TC)-(\d+)\b", all_text)):
        if f"{prefix}-{number}" not in defined[prefix]:
            issues.append(f"undefined:{prefix}-{number}")
    sections = re.split(r"(?=^###\s+REQ-\d+)", texts["01-prd.md"], flags=re.M)[1:]
    for section in sections:
        sid = re.match(r"###\s+(REQ-\d+)", section)[1]
        if len(re.findall(r"^\s*- \[[ xX]\]", section, re.M)) < 2:
            issues.append(f"insufficient_acceptance:{sid}")
    policies = re.split(r"(?=^###\s+POL-\d+)", texts["02-policy.md"], flags=re.M)[1:]
    for section in policies:
        sid = re.match(r"###\s+(POL-\d+)", section)[1]
        for marker in ["규칙", "적용 시점", "예외", "의사코드", "TC-"]:
            if marker not in section:
                issues.append(f"missing_policy_element:{sid}:{marker}")
        if not re.search("위반|실패 시 처리", section):
            issues.append(f"missing_policy_element:{sid}:violation")
    return {"scope": "structural_only", "issues": sorted(set(issues)),
            "structural_passed": not issues, "semantic_review": "required",
            "files": manifest(root, files) if files else {}}
