"""프로젝트 시작과 재개에서 사용하는 단일 안내 경로."""

import json
import re
import shutil
import subprocess
import secrets
from pathlib import Path

from .state import StateError, StateStore
from .workflow import Workflow, manifest


def stage_records(snapshot):
    reviews, confirmations = {}, {}
    for event in snapshot["events"]:
        payload = json.loads(event["payload"])
        if event["kind"] == "stage_review_requested":
            reviews[payload["stage"]] = payload
        elif event["kind"] == "stage_user_confirmed":
            confirmations[payload["stage"]] = payload
    return reviews, confirmations


def request_stage_review(store, stage, thread_id, revision):
    current = guide(store.project)
    if current["stage"] != stage or current.get("action_kind") != "review":
        raise StateError("현재 작성된 단계만 검토할 수 있습니다.")
    if not isinstance(thread_id, str) or not thread_id.strip():
        raise StateError("검토할 사용자 스레드가 필요합니다.")
    _, filename, _, _ = next(row for row in STAGES if row[0] == stage)
    files = [f"docs/{filename}"]
    if stage == "design":
        files.extend(p.relative_to(store.project).as_posix()
                     for folder in ["04-screens", "04-interactions"]
                     for p in sorted((store.project / "docs" / folder).glob("*")) if p.is_file())
    payload = {"stage": stage, "thread_id": thread_id, "files": manifest(store.project, files),
               "reply": f"{stage} 확인 {secrets.token_hex(12)}"}
    from .confirmation import question
    text = (store.project / "docs" / filename).read_text(encoding="utf-8")
    version_match = re.search(r"\|\s*문서 버전\s*\|\s*([^|]+)\|", text)
    display_version = version_match[1].strip() if version_match else "sha256:" + next(iter(payload["files"].values()))[:12]
    payload["question"] = question(filename, display_version, secrets.token_hex(12))
    with store._write(revision) as (db, state):
        if state["paused"]:
            raise StateError("사용자가 중단했습니다.")
        store._event(db, state, "stage_review_requested", payload)
    return payload


def confirm_stage(store, client, stage, revision, selection=None):
    from .probe import observe_user_reply
    snapshot = store.snapshot()
    reviews, confirmations = stage_records(snapshot)
    review = reviews.get(stage)
    if snapshot["revision"] != revision or not review or stage in confirmations:
        raise StateError("현재 단계 검토 기록과 revision을 확인해 주세요.")
    if guide(store.project).get("stage") != stage:
        raise StateError("이전 미완료 단계부터 확인해야 합니다.")
    if selection:
        from .confirmation import selected
        receipt = selected(review, *selection)
    else:
        receipt = observe_user_reply(client, review["thread_id"], review["reply"])
    if not receipt:
        raise StateError("이 검토 대상에 대한 사용자 확인 응답이 없습니다.")
    with store._write(revision) as (db, state):
        if state["paused"] or manifest(store.project, review["files"]) != review["files"]:
            raise StateError("중단되었거나 검토 대상 문서가 바뀌었습니다.")
        return store._event(db, state, "stage_user_confirmed", {
            "stage": stage, "files": review["files"], "source": receipt,
            "scope": "planning_stage_only", "reply": review["reply"]})


STAGES = [
    ("brief", "00-brief.md", "plan-init", "실제 사례 하나를 바탕으로 목적·사용자·핵심 흐름·제외 범위·결과물 합격 조건을 정리한다."),
    ("prd", "01-prd.md", "prd", "브리프를 요구사항 ID와 검사 가능한 인수 조건으로 구체화한다."),
    ("policy", "02-policy.md", "policy", "업무 규칙·적용 시점·오류 문구·예외·의사코드·TC를 연결한다."),
    ("design", "03-ia.md", "wireframe", "화면 또는 비화면 동작을 구분해 사용 흐름·상태·입출력을 명세한다."),
    ("qa", "05-qa-tc.md", "qa-tc", "각 P0 요구와 정책 위반을 실제 실행할 테스트에 연결한다."),
]


def create_project(root, name, idea, template_root, *, enabled=False):
    if not enabled:
        raise StateError("experimental writes are disabled")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", name) or name.upper().split(".")[0] in {
        "CON", "PRN", "AUX", "NUL", *{f"COM{i}" for i in range(1, 10)}, *{f"LPT{i}" for i in range(1, 10)}
    }:
        raise StateError("프로젝트 이름은 영문·숫자·하이픈·밑줄 1~64자로 입력해 주세요.")
    if not isinstance(idea, str) or not idea.strip():
        raise StateError("아이디어를 입력해 주세요.")
    root = Path(root).resolve(strict=True)
    templates = Path(template_root).resolve(strict=True)
    expected = ["00-project-brief.md", "01-prd.md", "02-policy.md", "03-ia.md",
                "04-wireframe-spec.md", "04-wireframe.html", "05-qa-tc.md", "change-request.md"]
    if any(not (templates / name).is_file() for name in expected):
        raise StateError("원본 기획 템플릿 세트가 필요합니다.")
    projects = root / "projects"
    if not projects.resolve().is_relative_to(root):
        raise StateError("프로젝트 경계와 충돌합니다.")
    projects.mkdir(exist_ok=True)
    destination = projects / name
    # mkdir is exclusive: preserve any existing project, including partial setup.
    destination.mkdir()
    (destination / "docs").mkdir()
    (destination / "code").mkdir()
    (destination / ".codex").mkdir()
    (destination / ".codex/config.toml").write_text(
        "[features]\ndefault_mode_request_user_input = true\n", encoding="utf-8")
    store = StateStore(destination, enabled=True)
    store.initialize()
    local_templates = destination / ".build-loop" / "templates"
    local_templates.mkdir()
    for filename in expected:
        shutil.copyfile(templates / filename, local_templates / filename)
    with store._write(0) as (db, state):
        store._event(db, state, "project_started", {"name": name, "idea": idea.strip()})
    (destination / ".gitignore").write_text(".build-loop/\n__pycache__/\n*.pyc\n", encoding="utf-8")
    result = subprocess.run(["git", "init", "--quiet", str(destination)], capture_output=True, timeout=20)
    if result.returncode:
        raise StateError("독립 Git 초기화 실패. 생성 자료를 보존했습니다.")
    code_root = Path(__file__).resolve().parents[1]
    from .policy import control_command
    instructions = f"""# build-loop 관리 프로젝트

사용자가 시작·다음·계속·재개를 요청하면 먼저 아래 명령을 실행하고 그 결과의 현재 작업만 수행한다.

```powershell
{control_command(destination, 'guide.json')}
```

1. docs/는 기획, code/는 실제 결과물이다. 목표는 사용자가 합의한 결과물 완성이다. 도구 자체 진단·기능 추가로 가지치지 않는다.
2. 기존 확정 답은 재질문하지 않는다. 필요한 질문 한 개만 실제 동기 request_user_input으로 추천·이유와 함께 묻는다. 메뉴가 없거나 빈 응답이면 같은 질문을 보존한다. 텍스트 목록을 실제 메뉴라고 말하지 않는다.
3. 기획은 .build-loop/templates 원본을 복사해 작성한다. 미정 내용을 추측하지 않는다. 승인 문서 변경은 CR로 처리한다.
4. 단계 산출물 작성 후 사용자 확인이 필요한 곳에서만 대기한다. 문서의 Approved 표기나 AI의 완료 선언은 사용자 승인 증거가 아니다.
5. 스펙 세트 승인 전 code/에 구현하지 않는다. 스펙 안의 버그는 수정·검증하며 동일 미해결 오류 복구는 최대 3회다. 외부 차단·사용자 중단은 재시도하지 않는다.
6. 구현·검증·실제 사용 시연·명시적 완료 승인을 구분한다. 실제 결과물과 증거 없이 완료라고 하지 않는다.

## 진행 상태를 실제로 연결하는 방법

- 제어 요청의 정확한 인수는 guide의 request_help 파일에 있다. Read가 없으면 `Get-Content -LiteralPath "<request_help 전체 경로>" -Encoding UTF8`로 읽는다. question 객체의 필수 필드는 id/question/options, 선택 필드는 header(12자 이하)다. 입력 형식 오류를 구현 실패 복구 횟수와 혼동하지 않는다. 기획 중에는 recovery-review를 호출하지 않는다.

- 질문을 표시하기 전에 workflow ask로 질문 ID·문구·선택지를 저장한다. 출력한 질문과 저장한 질문을 동일하게 유지한다.
- 동기 메뉴 응답은 workflow capture-selection의 session-file/session/call/question으로 대조·저장한다. 일반 메시지는 workflow answer로 지정 스레드에서 실제 응답을 읽는다. 선택을 받지 못하면 저장하지 않는다.
- guide의 action_kind=draft이면 담당 스킬로 해당 산출물을 작성한다. action_kind=review이면 문서를 먼저 보여주고 stage-review로 대상 파일·스레드를 연결한다. 사용자의 실제 확인 응답을 stage-confirm으로 읽은 뒤 guide를 다시 실행한다.
- 각 쓰기에는 현재 revision을 사용한다. 충돌하면 최신 guide를 읽고 중복 작업하지 않는다. 쓰기 기능은 현재 작업 프로세스의 BUILD_LOOP_EXPERIMENT_ENABLED=1에서만 사용한다. 전역 설정은 바꾸지 않는다.
- 현재 stage-review가 생성하는 확인 문자열은 개발 버전의 대상 연결 방식이다. 이를 사용자가 이미 했던 일반 승인과 혼동하지 않는다. 사용자 친화적인 문맥 승인·호스트 승인 UI 통합이 완료됐다고 말하지 않는다.

## 훅과 제어 요청

.codex/hooks.json을 호스트의 정상 검토 절차로 활성화한다. 신뢰 검토를 우회하지 않는다. 훅은 단계 밖의 쓰기·임의 셸 실행·미등록 메뉴를 차단한다. PreToolUse/PostToolUse/Stop/Interrupt가 같은 상태를 사용한다.

상태를 바꾸려면 .build-loop/requests/요청명.json을 Write 또는 apply_patch로 작성하고 위 guide 명령의 마지막 파일명만 바꿔 실행한다. 요청명은 영문·숫자·하이픈·밑줄만 쓴다. JSON에는 action과 현재 revision, 필요한 인수를 넣는다.

action: guide, read(path), list(path), search(query), question(question 객체), selection(session_file/session/call/question ID), stage-review(stage/thread), spec-review(version/thread), plan(tasks), check(task), demo(thread), completion(thread), answer(thread/question/text), external-block(reason).

확인 메뉴는 stage-review/spec-review/demo/completion이 반환한 question 객체를 실제 request_user_input으로 표시한다. 응답은 stage-selection(stage 포함)/spec-selection/demo-selection/completion-selection에 session_file/session/call을 넣어 연결한다. 미응답·수정 필요·자유 의견은 승인이 아니다. 수정 의견을 먼저 반영한다.

CR은 docs/changes/open/CR-###.md에 사유·AS-IS/TO-BE·영향 ID를 작성한다. change-review(cr/files 전체 변경 문서 내용/version/thread)로 내용을 묶고 change-apply(session_file/session/call)로 실제 사용자 확인 후 검사·일괄 반영한다. 복구 한도·결과 미확인에서는 recovery-review(thread/reason)와 recovery-selection으로 이전 프로세스 종료·재시도 근거의 사용자 확인을 연결한다. 중단 뒤 재개는 guide가 반환한 question을 표시한 후 resume-selection으로 연결한다.

Read 도구가 없으면 read 요청으로 프로젝트 문서·코드를 읽는다. 지원하지 않는 도구를 다른 도구로 우회하지 않는다. 문서 작성·코드 수정에는 Write/Edit/apply_patch를 사용하고 셸 직접 쓰기는 하지 않는다. 필요한 제어 요청 외의 상태 DB·훅·지침을 수정하지 않는다.

훅 신뢰·실행 여부가 확인되지 않으면 강제 적용됐다고 말하지 않는다. 사용자 입력·계정·권한 대기에서는 멈추고, 할 수 있는 현재 작업이 남았을 때는 완료 선언 없이 계속한다.
"""
    (destination / "AGENTS.md").write_text(instructions, encoding="utf-8")
    skill_source = code_root / "skills/build-loop/SKILL.md"
    if skill_source.is_file():
        skill_target = destination / ".agents/skills/build-loop"
        skill_target.mkdir(parents=True)
        shutil.copyfile(skill_source, skill_target / "SKILL.md")
    from .harness import attach
    attach(destination)
    return destination


def guide(project):
    store = StateStore(Path(project))
    snapshot = store.snapshot()
    events = snapshot["events"]
    starts = [json.loads(e["payload"]) for e in events if e["kind"] == "project_started"]
    if len(starts) != 1:
        raise StateError("관리 프로젝트 시작 기록이 없습니다.")
    flow = Workflow(store).status()
    reviews, confirmations = stage_records(snapshot)
    result = {"project": str(store.project), "idea": starts[0]["idea"], "revision": snapshot["revision"],
              "goal": "합의한 결과물 구현·검증·실제 사용 후 사용자 완료 승인",
              "development_allowed": False, "complete": False, "guidance_only": True,
              "request_help": str(Path(__file__).resolve().parents[1] / "docs/control-requests.md")}
    result["harness_configured"] = (store.project / ".codex/hooks.json").is_file()
    if flow["paused"]:
        pauses = [json.loads(e["payload"]) for e in events if e["kind"] == "paused"]
        return dict(result, stage="paused", action="진행 대기: " + str(pauses[-1].get("reason", "user") if pauses else "user"),
                    question=pauses[-1].get("question") if pauses else None)
    if flow["pending_question"]:
        return dict(result, stage="interview", action="동일 미응답 질문을 실제 선택 도구로 표시합니다.",
                    question=flow["pending_question"])
    changes = [json.loads(e["payload"]) for e in events if e["kind"] == "change_review_requested"]
    feedbacks = [json.loads(e["payload"]) for e in events if e["kind"] == "review_feedback"]
    merged = {json.loads(e["payload"])["nonce"] for e in events if e["kind"] == "change_merged"}
    if changes and changes[-1]["nonce"] not in merged:
        feedback = next((p for p in reversed(feedbacks) if p["question_id"] == changes[-1]["question"]["id"]), None)
        return dict(result, stage="change_review", action="CR 수정 의견을 반영하고 새 변경안을 검토합니다." if feedback else "변경안의 사용자 확인을 기다립니다.",
                    question=None if feedback else changes[-1]["question"], feedback=feedback)
    retries = [json.loads(e["payload"]) for e in events if e["kind"] == "recovery_review_requested"]
    allowed = {json.loads(e["payload"])["nonce"] for e in events if e["kind"] == "recovery_retry_authorized"}
    if retries and retries[-1]["nonce"] not in allowed:
        return dict(result, stage="recovery_review", action="이전 프로세스 종료와 근거 있는 재시도의 사용자 확인을 기다립니다.", question=retries[-1]["question"])
    for stage, filename, skill, purpose in STAGES:
        path = store.project / "docs" / filename
        if not path.resolve().is_relative_to(store.project):
            raise StateError("문서 경계와 충돌합니다.")
        confirmation = confirmations.get(stage)
        if confirmation:
            try:
                unchanged = manifest(store.project, confirmation["files"]) == confirmation["files"]
            except (OSError, StateError):
                unchanged = False
            if not unchanged:
                return dict(result, stage=stage, action_kind="change_required",
                            action="확인된 문서가 변경되었습니다. 승인된 요구 변경은 CR로 처리하고 재검토합니다.")
            continue
        if not path.is_file() or not path.read_text(encoding="utf-8").strip():
            return dict(result, stage=stage, skill=skill, artifact=f"docs/{filename}", action=purpose,
                        action_kind="draft",
                        prerequisite="이전 단계의 실제 사용자 확인을 대화·기록과 대조한다. 미확인이면 먼저 확인한다.",
                        templates=".build-loop/templates", accepted_answers=flow["answers"])
        review = reviews.get(stage)
        feedback = next((p for p in reversed(feedbacks) if review and p["question_id"] == review.get("question", {}).get("id")), None)
        if feedback and manifest(store.project, review["files"]) == review["files"]:
            return dict(result, stage=stage, skill=skill, artifact=f"docs/{filename}", action_kind="revise",
                        action="사용자의 수정 의견을 반영하고 문서를 다시 보여줍니다.", feedback=feedback)
        return dict(result, stage=stage, skill=skill, artifact=f"docs/{filename}", action_kind="review",
                    action="작성한 문서를 사용자에게 보여주고 이 단계의 확인을 받습니다. 다음 단계로 건너뛰지 않습니다.",
                    review=review, accepted_answers=flow["answers"])
    if flow["manifest_matches"] is False:
        return dict(result, stage="spec_review", action="저장된 스펙과 실제 파일 차이를 확인합니다. 승인 변경은 CR로 처리합니다.")
    if flow["development_allowed"]:
        from .delivery import Delivery
        delivery = Delivery(store).status()
        return dict(result, stage="complete" if delivery["complete"] else "delivery",
                    development_allowed=True, complete=delivery["complete"],
                    action=delivery["next_action"], delivery=delivery)
    spec_feedback = next((p for p in reversed(feedbacks) if flow["review"] and p["question_id"] == flow["review"].get("question", {}).get("id")), None)
    if spec_feedback:
        return dict(result, stage="spec_review", action_kind="change_required", action="사용자 수정 의견을 CR에 연결하고 반영한 뒤 스펙 세트를 다시 검토합니다.", feedback=spec_feedback)
    return dict(result, stage="spec_review", skill="plan-status",
                action="스펙 세트 정합성을 확인하고 실제 사용자 승인·대상 버전을 연결합니다. 미검증 상태에서는 구현으로 넘기지 않습니다.",
                remaining_path=["스펙 승인 연결", "REQ별 구현 작업", "구현·테스트·수정", "실제 실행·시연", "사용자 완료 승인"],
                blocked_reason=flow["blocked_reason"])
