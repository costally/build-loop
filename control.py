"""훅이 허용하는 요청 파일 기반 제어 경로."""
import argparse
import json
from pathlib import Path
import re
import shutil

from build_loop.state import StateStore, StateError
from build_loop.director import guide, request_stage_review, confirm_stage
from build_loop.workflow import Workflow
from build_loop.delivery import Delivery
from build_loop.rpc import ReadOnlyCodex, RpcError
from build_loop.menu_input import question_matches
from build_loop.selection import find_selection, read_selection
import subprocess
import sys


def selection_request(store, data):
    action = data["action"]
    snapshot = store.snapshot()
    if action == "selection":
        pending = Workflow.fold(snapshot)["pending_question"]
        if not pending or pending["id"] != data.get("question"):
            raise StateError("현재 미응답 질문 ID를 확인하세요.")
        return {"question": pending}, None
    mapping = {"stage-selection": ("stage_review_requested", data.get("stage"), "stage"),
               "spec-selection": ("spec_review_requested", "spec", None),
               "demo-selection": ("delivery_confirmation_requested", "demo", "kind"),
               "completion-selection": ("delivery_confirmation_requested", "completion", "kind"),
               "change-apply": ("change_review_requested", "change", None),
               "recovery-selection": ("recovery_review_requested", "recovery", None),
               "resume-selection": ("paused", "resume", None)}
    kind, target, field = mapping[action]
    requests = [json.loads(e["payload"]) for e in snapshot["events"] if e["kind"] == kind]
    if field == "stage":
        requests = [request for request in requests if request.get(field) == target]
    if (not requests or not requests[-1].get("question")
            or (field == "kind" and requests[-1].get(field) != target)):
        raise StateError("현재 요청에 연결된 선택 메뉴가 없습니다.")
    return requests[-1], target


def selection_source(store, data, request):
    fields = {"session_file", "session", "call"}
    if not fields.intersection(data):
        return find_selection(store.project, request["question"], request.get("thread_id"))
    if not fields.issubset(data):
        raise StateError("기존 형식의 세션·호출 필드는 모두 제공하거나 모두 생략하세요.")
    return Path(data["session_file"]), data["session"], data["call"]


def selection_operation(store, data, request, target, selection, operation):
    try:
        return operation()
    except StateError:
        receipt, question, answer = read_selection(*selection, request["question"]["id"])
        if not question_matches(request["question"], question) or selection[1] != request.get("thread_id") or answer == question["options"][0]["label"]:
            raise
        with store._write(data["revision"]) as (db, state):
            return store._event(db, state, "review_feedback", {"target": target, "question_id": question["id"],
                                                               "text": answer, "source": receipt})


def execute(project, request):
    root = Path(project).resolve(strict=True)
    if not re.fullmatch(r"[A-Za-z0-9_-]+\.json", request):
        raise StateError("invalid request name")
    path = root / ".build-loop/requests" / request
    if not path.resolve().is_relative_to(root):
        raise StateError("request escapes project")
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    action = data["action"]
    store = StateStore(root, enabled=True)
    flow = Workflow(store)
    delivery = Delivery(store)
    revision = data.get("revision")
    if action == "guide":
        return guide(root)
    if action == "read":
        target = (root / data["path"]).resolve(strict=True)
        if not target.is_relative_to(root) or (target.is_relative_to(root / ".build-loop")
                                             and not target.is_relative_to(root / ".build-loop/templates")):
            raise StateError("read path escapes document/code area")
        return {"path": data["path"], "text": target.read_text(encoding="utf-8-sig")}
    if action == "list":
        target = (root / data.get("path", ".")).resolve(strict=True)
        if not target.is_dir() or not target.is_relative_to(root) or target.is_relative_to(root / ".build-loop"):
            raise StateError("목록 조회 경로를 확인하세요.")
        return {"paths": sorted(str(p.relative_to(root)) for p in target.iterdir() if p.resolve().is_relative_to(root))}
    if action == "search":
        query = data.get("query")
        if not isinstance(query, str) or not query:
            raise StateError("검색어가 필요합니다.")
        matches = []
        for folder in [root / "docs", root / "code"]:
            for target in folder.rglob("*"):
                if target.suffix not in {".md", ".py", ".js", ".ts", ".tsx", ".jsx", ".html", ".css", ".json", ".toml", ".txt"} or not target.is_file() or not target.resolve().is_relative_to(root):
                    continue
                if any(part in {"node_modules", ".git", ".venv", "__pycache__"} for part in target.parts):
                    continue
                for number, line in enumerate(target.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                    if query in line:
                        matches.append({"path": str(target.relative_to(root)), "line": number, "text": line[:500]})
                        if len(matches) >= 100:
                            return {"matches": matches, "truncated": True}
        return {"matches": matches, "truncated": False}
    if action == "question":
        flow.ask(data["question"], revision)
    elif action == "stage-review":
        return request_stage_review(store, data["stage"], data["thread"], revision)
    elif action == "spec-review":
        from build_loop.spec_check import inspect_spec
        result = inspect_spec(root)
        flow.review("spec-set", data["version"], list(result["files"]), revision, data["thread"])
    elif action == "plan":
        delivery.plan(data["tasks"], revision)
    elif action == "change-review":
        from build_loop.changes import Changes
        changes = Changes(store)
        return changes.request(data["cr"], data["files"], data["version"], data["thread"], revision)
    elif action == "check":
        delivery.run_check(data["task"], revision)
    elif action == "recovery-review":
        return delivery.request_recovery(data["thread"], data["reason"], revision)
    elif action in {"demo", "completion"}:
        return delivery.request_user_confirmation(action, data["thread"], revision)
    elif action == "external-block":
        reason = data.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise StateError("외부 차단 근거가 필요합니다.")
        store.pause(revision, "external_block: " + reason)
    elif action in {"selection", "resume-selection", "stage-selection", "spec-selection",
                    "demo-selection", "completion-selection", "change-apply", "recovery-selection"}:
        selection_data, target = selection_request(store, data)
        selection = selection_source(store, data, selection_data)
        if action == "selection":
            flow.capture_selection(*selection, data["question"], revision)
            return guide(root)
        if action == "resume-selection":
            store.resume_selection(*selection, revision)
            return guide(root)
        if action == "stage-selection":
            operation = lambda: confirm_stage(store, None, data["stage"], revision, selection)
        elif action == "spec-selection":
            operation = lambda: flow.observe_approval(None, selection[1], revision, selection)
        elif action == "change-apply":
            from build_loop.changes import Changes
            operation = lambda: Changes(store).apply(*selection, revision)
        elif action == "recovery-selection":
            operation = lambda: delivery.authorize_recovery(*selection, revision)
        else:
            operation = lambda: delivery.confirm_user(None, target, revision, selection)
        selection_operation(store, data, selection_data, target, selection, operation)
    elif action in {"stage-confirm", "spec-confirm", "demo-confirm", "completion-confirm", "answer"}:
        executable = shutil.which("codex.exe")
        if not executable:
            # The npm wrapper's native executable is used, never a model-supplied path.
            wrapper = shutil.which("codex.cmd")
            candidates = list((Path(wrapper).parent / "node_modules/@openai/codex/node_modules").glob("codex-win32-*/vendor/*/bin/codex.exe")) if wrapper else []
            executable = str(candidates[0]) if len(candidates) == 1 else None
        if not executable:
            raise StateError("native Codex executable not found")
        with ReadOnlyCodex(Path(executable)) as client:
            if action == "stage-confirm":
                confirm_stage(store, client, data["stage"], revision)
            elif action == "spec-confirm":
                flow.observe_approval(client, data["thread"], revision)
            elif action == "answer":
                from build_loop.probe import observe_user_reply
                receipt = observe_user_reply(client, data["thread"], data["text"])
                flow.answer(data["question"], data["text"], receipt, revision)
            else:
                delivery.confirm_user(client, action.split("-")[0], revision)
    else:
        raise StateError("unsupported control action")
    return guide(root)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--request", required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(execute(args.project, args.request), ensure_ascii=False, indent=2))
    except (StateError, RpcError, ValueError, OSError, KeyError, subprocess.SubprocessError) as error:
        print(json.dumps({"blocked": True, "error": str(error),
                          "request_help": str(Path(__file__).resolve().parent / "docs/control-requests.md"),
                          "next_action": "요청 형식 오류는 위 계약으로 수정하세요. 구현 작업이 없으면 recovery-review를 호출하지 마세요. paused이면 guide의 재개 메뉴부터 처리하세요.",
                          "question_example": {"action": "question", "revision": "현재 guide의 revision",
                            "question": {"id": "use_case", "header": "사용 상황", "question": "어떤 용도인가요?",
                              "options": [{"label": "개인 메모 (추천)", "description": "최소 사용 흐름"},
                                          {"label": "여러 메모", "description": "목록 관리"}]}}}, ensure_ascii=False))
        raise SystemExit(2)
