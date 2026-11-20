"""승인 스펙의 요구사항을 구현·검증·시연·완료 대기로 잇는다."""

import hashlib
import json
import platform
import re
import secrets
import subprocess
import unicodedata
from pathlib import Path

from .state import StateError
from .workflow import Workflow, manifest


class Delivery:
    def __init__(self, store):
        self.store = store

    def code_version(self):
        root = self.store.project / "code"
        if not root.is_dir() or not root.resolve().is_relative_to(self.store.project):
            raise StateError("프로젝트 code/ 경로를 확인해 주세요.")
        files = [p.relative_to(self.store.project).as_posix() for p in sorted(root.rglob("*"))
                 if p.is_file() and not any(part in {".git", "__pycache__"} for part in p.relative_to(root).parts)]
        return manifest(self.store.project, files) if files else {}

    def binding(self):
        flow = Workflow(self.store).status()
        if not flow["development_allowed"]:
            raise StateError(flow["blocked_reason"])
        if flow["paused"] or flow["manifest_matches"] is not True:
            raise StateError("중단 또는 스펙 불일치 상태입니다.")
        return {"spec": flow["review"]["files"], "version": flow["review"]["version"]}

    def _append(self, revision, kind, payload):
        with self.store._write(revision) as (db, state):
            if state["paused"]:
                raise StateError("사용자가 중단했습니다.")
            return self.store._event(db, state, kind, payload)

    def error_identity(self, task):
        identity = {"project_id": str(self.store.project), "task_id": task["id"], "check_id": "validation:" + task["id"],
                    "failure_category": "validation_failure", "environment_id": task["environment"]}
        return {key: unicodedata.normalize("NFC", value) for key, value in identity.items()}

    def prepare_recovery(self, task, revision):
        events = self.store.snapshot()["events"]
        checks = [(e["revision"], json.loads(e["payload"])) for e in events if e["kind"] == "task_check_finished"]
        latest = next(((r, p) for r, p in reversed(checks) if p["task"]["id"] == task["id"] and p["task"]["environment"] == task["environment"]), None)
        if not latest or latest[1]["result"] != "failed":
            return revision
        identity = self.error_identity(task)
        if any(e["revision"] > latest[0] and e["kind"] == "recovery_started" and json.loads(e["payload"]).get("identity") == identity for e in events):
            return revision
        return self.store.begin_recovery(identity, revision)

    def request_recovery(self, thread, reason, revision):
        from .confirmation import question
        view = self.status()
        task = view.get("current_task")
        if not task or not thread or not isinstance(reason, str) or not reason.strip():
            raise StateError("현재 미완료 작업과 재시도 근거가 필요합니다.")
        identity = self.error_identity(task)
        base = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        events = self.store.snapshot()["events"]
        finished = {json.loads(e["payload"])["token"] for e in events if e["kind"] == "task_check_finished"}
        unknown = [json.loads(e["payload"])["token"] for e in events if e["kind"] == "task_check_started"
                   and json.loads(e["payload"])["task"]["id"] == task["id"] and json.loads(e["payload"])["token"] not in finished]
        request = {"base": base, "reason": reason, "thread_id": thread, "code": self.code_version(),
                   "nonce": secrets.token_hex(12), "abandoned_tokens": unknown, **self.binding()}
        request["question"] = question("이전 검증 프로세스 종료 확인 및 재시도: " + reason, request["version"], request["nonce"])
        self._append(revision, "recovery_review_requested", request)
        return request

    def authorize_recovery(self, path, session, call, revision):
        from .confirmation import selected
        requests = [json.loads(e["payload"]) for e in self.store.snapshot()["events"] if e["kind"] == "recovery_review_requested"]
        if not requests:
            raise StateError("재시도 확인 요청이 없습니다.")
        request = requests[-1]
        receipt = selected(request, path, session, call)
        if self.code_version() != request["code"] or self.binding()["spec"] != request["spec"]:
            raise StateError("재시도 검토 이후 대상 버전이 바뀌었습니다.")
        events = self.store.snapshot()["events"]
        if any(e["kind"] == "recovery_retry_authorized" and json.loads(e["payload"])["nonce"] == request["nonce"] for e in events):
            raise StateError("이미 확인한 재시도입니다.")
        return self._append(revision, "recovery_retry_authorized", {**request, "source": receipt})

    def plan(self, tasks, revision):
        """계획 기록은 개발 허용이 아니다. 실제 실행 때 승인 상태를 다시 검사한다."""
        flow = Workflow(self.store).status()
        if not flow["review"] or flow["manifest_matches"] is not True:
            raise StateError("검토할 스펙 manifest가 필요합니다.")
        prd = self.store.project / "docs/01-prd.md"
        qa = self.store.project / "docs/05-qa-tc.md"
        requirements = set(re.findall(r"^###\s+(REQ-\d+)\b", prd.read_text(encoding="utf-8"), re.M))
        tests = set(re.findall(r"^###\s+(TC-\d+)\b", qa.read_text(encoding="utf-8"), re.M))
        if not isinstance(tasks, list) or not tasks:
            raise StateError("개발 작업이 필요합니다.")
        ids, covered = set(), set()
        for task in tasks:
            if not isinstance(task, dict) or set(task) != {"id", "req", "tc", "title", "acceptance", "check", "environment"}:
                raise StateError("작업에는 ID·REQ·TC·내용·완료 조건·검증 명령·환경이 필요합니다.")
            if task["id"] in ids or task["req"] not in requirements or task["tc"] not in tests:
                raise StateError("중복 작업 또는 존재하지 않는 REQ/TC입니다.")
            if any(not isinstance(task[k], str) or not task[k].strip() for k in ["id", "title", "acceptance", "environment"]):
                raise StateError("작업 내용과 합격 조건이 비어 있습니다.")
            if not isinstance(task["check"], list) or not task["check"] or any(not isinstance(s, str) or not s for s in task["check"]):
                raise StateError("검증 명령은 비어 있지 않은 argv 배열이어야 합니다.")
            ids.add(task["id"])
            covered.add(task["req"])
        if covered != requirements:
            raise StateError("요구사항에 연결되지 않은 개발 작업이 있습니다.")
        return self._append(revision, "delivery_plan", {"tasks": tasks, "spec": flow["review"]["files"]})

    def status(self):
        snapshot = self.store.snapshot()
        payloads = [(e["kind"], json.loads(e["payload"])) for e in snapshot["events"]]
        plans = [p for kind, p in payloads if kind == "delivery_plan"]
        result = {"revision": snapshot["revision"], "complete": False, "tasks": [],
                  "next_action": "REQ별 개발 작업과 검증을 등록"}
        if not plans:
            return result
        plan = plans[-1]
        plan_hash = hashlib.sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest()
        result["plan_sha256"] = plan_hash
        flow = Workflow(self.store).status()
        if snapshot["paused"]:
            return dict(result, next_action="사용자 중단 상태 유지")
        if not flow["development_allowed"]:
            return dict(result, next_action="스펙 세트의 사용자 승인 확인")
        if not flow["review"] or plan["spec"] != flow["review"]["files"] or flow["manifest_matches"] is not True:
            return dict(result, next_action="스펙 변경을 확인하고 CR 및 작업 계획 갱신")
        code = self.code_version()
        issue_revision = max((event["revision"] for event in snapshot["events"] if event["kind"] == "review_feedback"
                              and json.loads(event["payload"])["target"] in {"demo", "completion"}), default=-1)
        for task in plan["tasks"]:
            checks = [json.loads(event["payload"]) for event in snapshot["events"] if event["kind"] == "task_check_finished"
                      and event["revision"] > issue_revision and json.loads(event["payload"])["task"] == task
                      and json.loads(event["payload"])["spec"] == plan["spec"] and json.loads(event["payload"])["code"] == code]
            last = checks[-1] if checks else None
            finished = {p["token"] for kind, p in payloads if kind == "task_check_finished"}
            finished.update(token for kind, p in payloads if kind == "recovery_retry_authorized" for token in p["abandoned_tokens"])
            unknown = any(kind == "task_check_started" and p["task"]["id"] == task["id"]
                          and p["token"] not in finished for kind, p in payloads)
            result["tasks"].append(dict(task, result="unknown" if unknown else last["result"] if last else "unrun"))
        pending = next((t for t in result["tasks"] if t["result"] != "passed"), None)
        if pending:
            identity = self.error_identity(pending)
            base = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
            cycle = sum(kind in {"recovery_resolved", "recovery_retry_authorized"} and p.get("base") == base for kind, p in payloads)
            fingerprint = base if not cycle else hashlib.sha256(f"{base}:{cycle}".encode()).hexdigest()
            attempts = [attempt for attempt in snapshot["attempts"] if attempt["fingerprint"] == fingerprint]
            if len(attempts) >= 3 and attempts[-1]["result"] == "failed":
                return dict(result, next_action="동일 오류 복구 3회 실패: 사용자 재시도 근거 확인 대기", current_task=dict(pending, result="exhausted"))
            action = "이전 검증 결과 미확인: 실행 상태와 증거 확인, 자동 재실행 금지" if pending["result"] == "unknown" else "구현 또는 수정 후 해당 검증 실행"
            return dict(result, next_action=action, current_task=pending)
        demos = [json.loads(event["payload"]) for event in snapshot["events"] if event["kind"] == "demo_user_confirmed"
                 and event["revision"] > issue_revision and json.loads(event["payload"])["code"] == code
                 and json.loads(event["payload"])["spec"] == plan["spec"] and json.loads(event["payload"])["plan_sha256"] == plan_hash]
        if not demos:
            return dict(result, next_action="합의한 환경에서 실제 결과물을 시연하고 사용자 확인")
        completions = [json.loads(event["payload"]) for event in snapshot["events"] if event["kind"] == "completion_user_confirmed"
                       and event["revision"] > issue_revision and json.loads(event["payload"])["code"] == code
                       and json.loads(event["payload"])["spec"] == plan["spec"] and json.loads(event["payload"])["plan_sha256"] == plan_hash]
        return dict(result, complete=bool(completions), next_action="완료" if completions else "사용자 최종 완료 승인 대기")

    def run_check(self, task_id, revision, timeout=60):
        binding = self.binding()
        view = self.status()
        task = next((t for t in view["tasks"] if t["id"] == task_id), None)
        if not task:
            raise StateError("현재 계획에 없는 작업입니다.")
        if task["result"] == "unknown":
            raise StateError("이전 검증의 실행 결과를 먼저 확인해야 합니다.")
        task = {k: v for k, v in task.items() if k != "result"}
        revision = self.prepare_recovery(task, revision)
        code = self.code_version()
        if not code:
            raise StateError("실제 구현 파일이 없습니다.")
        token = secrets.token_hex(12)
        started = self._append(revision, "task_check_started", {"task": task, "token": token, "code": code, **binding})
        try:
            process = subprocess.run(task["check"], cwd=self.store.project / "code", shell=False,
                                     capture_output=True, timeout=timeout)
            passed = process.returncode == 0
            output_hash = hashlib.sha256(process.stdout + process.stderr).hexdigest()
            outcome = "passed" if passed else "failed"
            exit_code = process.returncode
        except subprocess.TimeoutExpired:
            outcome, output_hash, exit_code = "blocked", None, None
        except OSError:
            outcome, output_hash, exit_code = "blocked", None, None
        if self.code_version() != code or self.binding() != binding:
            outcome = "stale"
        payload = {"task": task, "token": token, "code": code, **binding, "result": outcome,
                   "output_sha256": output_hash, "exit_code": exit_code, "host": platform.system()}
        finished = self._append(started, "task_check_finished", payload)
        if outcome in {"passed", "failed"}:
            self.store.resolve_recovery(self.error_identity(task), token, finished)
        return self.status()

    def request_user_confirmation(self, kind, thread_id, revision):
        if kind not in {"demo", "completion"}:
            raise StateError("알 수 없는 확인 단계입니다.")
        binding = self.binding()
        view = self.status()
        expected = "합의한 환경에서 실제 결과물을 시연하고 사용자 확인" if kind == "demo" else "사용자 최종 완료 승인 대기"
        if view["next_action"] != expected or not thread_id:
            raise StateError("현재 검증 또는 시연 확인이 부족합니다.")
        payload = {"kind": kind, "thread_id": thread_id, "code": self.code_version(), **binding,
                   "plan_sha256": view["plan_sha256"],
                   "reply": f"{kind} 확인 {secrets.token_hex(12)}"}
        from .confirmation import question
        environments = ", ".join(sorted({task["environment"] for task in view["tasks"]}))
        code_hash = hashlib.sha256(json.dumps(payload["code"], sort_keys=True).encode()).hexdigest()[:12]
        payload["question"] = question("실제 시연·사용" if kind == "demo" else "최종 결과물 완료",
                                       binding["version"] + " / " + environments + " / code:" + code_hash, secrets.token_hex(12))
        self._append(revision, "delivery_confirmation_requested", payload)
        return payload

    def confirm_user(self, client, kind, revision, selection=None):
        from .probe import observe_user_reply
        requests = [json.loads(e["payload"]) for e in self.store.snapshot()["events"]
                    if e["kind"] == "delivery_confirmation_requested"]
        if not requests or requests[-1]["kind"] != kind:
            raise StateError("현재 확인 요청이 없습니다.")
        request = requests[-1]
        binding = self.binding()
        view = self.status()
        expected = "합의한 환경에서 실제 결과물을 시연하고 사용자 확인" if kind == "demo" else "사용자 최종 완료 승인 대기"
        if view["next_action"] != expected or view.get("plan_sha256") != request["plan_sha256"]:
            raise StateError("현재 검증 또는 시연 상태가 바뀌었습니다.")
        if request["code"] != self.code_version() or request["spec"] != binding["spec"]:
            raise StateError("확인 대기 중 결과물 또는 스펙이 바뀌었습니다.")
        if selection:
            from .confirmation import selected
            receipt = selected(request, *selection)
        else:
            receipt = observe_user_reply(client, request["thread_id"], request["reply"])
        if not receipt:
            raise StateError("이 결과물에 대한 명시적 사용자 응답이 없습니다.")
        if request["code"] != self.code_version():
            raise StateError("사용자 응답 확인 중 코드가 바뀌었습니다.")
        return self._append(revision, kind + "_user_confirmed", {**request, "source": receipt})
