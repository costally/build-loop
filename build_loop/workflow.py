"""인터뷰와 스펙 검토의 영속 상태. 미실증 호스트는 개발을 허용하지 않는다."""

import hashlib
import json
import unicodedata
import secrets
from pathlib import Path

from .state import StateError


def normalized(value):
    if not isinstance(value, str) or not value.strip():
        raise StateError("빈 입력은 확정할 수 없습니다.")
    return unicodedata.normalize("NFC", value.strip())


def manifest(project, files):
    root = Path(project).resolve(strict=True)
    result = {}
    for name in files:
        path = (root / name).resolve(strict=True)
        if not path.is_relative_to(root) or not path.is_file():
            raise StateError("스펙 파일은 선택한 프로젝트 안에 있어야 합니다.")
        relative = path.relative_to(root).as_posix()
        result[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    if not result:
        raise StateError("스펙 파일이 없습니다.")
    return dict(sorted(result.items()))


class Workflow:
    def __init__(self, store):
        self.store = store

    @staticmethod
    def fold(snapshot):
        view = {"revision": snapshot["revision"], "paused": bool(snapshot["paused"]),
                "stage": "interview", "questions": [], "answers": {}, "review": None,
                "approval": None}
        for event in snapshot["events"]:
            payload = json.loads(event["payload"])
            if event["kind"] == "interview_question":
                view["questions"].append(payload)
            elif event["kind"] == "interview_answer":
                view["answers"][payload["id"]] = payload
            elif event["kind"] == "spec_review_requested":
                view["review"] = payload
                view["stage"] = "spec_review"
                view["approval"] = None
            elif event["kind"] == "spec_approval_observed":
                view["approval"] = payload
        view["pending_question"] = next((q for q in view["questions"]
                                         if q["id"] not in view["answers"]), None)
        return view

    def status(self):
        view = self.fold(self.store.snapshot())
        review = view["review"]
        view["manifest_matches"] = None
        if review:
            try:
                view["manifest_matches"] = manifest(self.store.project, review["files"]) == review["files"]
            except (OSError, StateError):
                view["manifest_matches"] = False
        approval = view["approval"]
        source = approval.get("source", {}) if approval else {}
        valid_reply = bool(review and approval and approval.get("authority") == "verified_user_message_binding"
                           and source.get("thread_id") == review.get("thread_id")
                           and source.get("text_sha256") == hashlib.sha256(review["approval_text"].encode()).hexdigest())
        valid_selection = bool(review and approval and approval.get("authority") == "verified_selection_binding"
                               and source.get("session_id") == review.get("thread_id")
                               and source.get("question_sha256") == hashlib.sha256(json.dumps(review["question"], ensure_ascii=False, sort_keys=True).encode()).hexdigest()
                               and source.get("answer_sha256") == hashlib.sha256(review["question"]["options"][0]["label"].encode()).hexdigest())
        view["development_allowed"] = bool(
            review and approval and not view["paused"] and not view["pending_question"] and view["manifest_matches"] is True
            and review["target"] == "spec-set"
            and (valid_reply or valid_selection)
            and approval.get("files") == review["files"]
            and approval.get("challenge") == review["challenge"]
            )
        view["blocked_reason"] = "" if view["development_allowed"] else "이 스펙 세트에 연결된 사용자 승인을 확인해야 합니다."
        view["next_action"] = ("사용자 중단 상태 유지" if view["paused"] else
                               "스펙 파일 불일치 확인" if view["manifest_matches"] is False else
                               "미응답 질문 표시" if view["pending_question"] else
                               "개발 작업 실행" if view["development_allowed"] else
                               "호스트 승인 바인딩 실증" if view["approval"] else
                               "사용자 스펙 승인 대기" if review else "다음 질문 또는 스펙 검토 준비")
        return view

    def _change(self, revision, kind, build):
        with self.store._write(revision) as (db, state):
            snapshot = dict(state)
            snapshot["events"] = [dict(row) for row in db.execute("SELECT * FROM events ORDER BY revision")]
            view = self.fold(snapshot)
            if view["paused"]:
                raise StateError("사용자가 중단했습니다. 자동 진행할 수 없습니다.")
            payload = build(view)
            return self.store._event(db, state, kind, payload)

    def ask(self, question, revision):
        def build(view):
            if view["pending_question"]:
                raise StateError("현재 미완료 질문 또는 스펙 검토를 먼저 처리해야 합니다.")
            if set(question) != {"id", "question", "options"}:
                raise StateError("질문 형식이 올바르지 않습니다.")
            q = {"id": normalized(question["id"]), "question": normalized(question["question"])}
            if any(old["id"] == q["id"] for old in view["questions"]):
                raise StateError("이미 사용한 질문 ID입니다.")
            options = question["options"]
            if not isinstance(options, list) or not 2 <= len(options) <= 3:
                raise StateError("선택지는 2~3개여야 합니다.")
            q["options"] = [{"label": normalized(o["label"]), "description": normalized(o["description"])}
                            for o in options]
            labels = [o["label"] for o in q["options"]]
            if len(set(labels)) != len(labels) or "추천" not in labels[0]:
                raise StateError("첫 선택지에 추천 표시가 있어야 하고 선택지는 중복될 수 없습니다.")
            return q
        return self._change(revision, "interview_question", build)

    def answer(self, question_id, text, receipt, revision):
        text = normalized(text)
        required = {"kind", "transport", "thread_id", "turn_id", "item_id", "text_sha256"}
        if (not isinstance(receipt, dict) or set(receipt) != required
                or receipt.get("kind") != "user_reply_observation"
                or receipt.get("transport") != "codex_app_server_stdio"
                or any(not isinstance(v, str) or not v for v in receipt.values())
                or receipt["text_sha256"] != hashlib.sha256(text.encode()).hexdigest()):
            raise StateError("사용자 응답 관찰과 답변이 일치하지 않습니다.")
        def build(view):
            pending = view["pending_question"]
            if not pending or pending["id"] != question_id:
                raise StateError("현재 미응답 질문과 다릅니다.")
            if any(a["source"]["item_id"] == receipt["item_id"] and
                   a["source"]["thread_id"] == receipt["thread_id"] for a in view["answers"].values()):
                raise StateError("다른 질문에 사용한 응답입니다.")
            return {"id": question_id, "text": text, "source": receipt,
                    "authority": "observation_only"}
        return self._change(revision, "interview_answer", build)

    def review(self, target, version, files, revision, thread_id=None):
        if normalized(target) == "spec-set":
            from .director import stage_records, STAGES
            _, confirmations = stage_records(self.store.snapshot())
            if set(confirmations) != {row[0] for row in STAGES} or not thread_id:
                raise StateError("기획 단계별 사용자 확인과 승인할 스레드가 필요합니다.")
            for confirmed in confirmations.values():
                if manifest(self.store.project, confirmed["files"]) != confirmed["files"]:
                    raise StateError("확인 이후 바뀐 문서가 있습니다.")
            from .spec_check import inspect_spec
            check = inspect_spec(self.store.project)
            if not check["structural_passed"]:
                raise StateError("스펙 구조 검사가 완료되지 않았습니다: " + ", ".join(check["issues"]))
            if manifest(self.store.project, files) != check["files"]:
                raise StateError("스펙 검토 목록과 검사된 전체 파일 목록이 다릅니다.")
        payload = {"target": normalized(target), "version": normalized(version),
                   "files": manifest(self.store.project, files),
                   "challenge": secrets.token_hex(16)}
        if thread_id:
            payload["thread_id"] = normalized(thread_id)
        payload["approval_text"] = (f"승인 {payload['target']} {payload['version']} "
                                    f"{payload['challenge']}")
        from .confirmation import question
        payload["question"] = question(payload["target"], payload["version"], payload["challenge"])
        def build(view):
            if view["pending_question"] or not view["answers"]:
                raise StateError("확정 답변과 미응답 질문을 먼저 확인해야 합니다.")
            return payload
        return self._change(revision, "spec_review_requested", build)

    def capture_selection(self, path, session_id, call_id, question_id, revision):
        from .selection import read_selection
        receipt, question, text = read_selection(path, session_id, call_id, question_id)
        def build(view):
            pending = view["pending_question"]
            if not pending or pending["id"] != question_id:
                raise StateError("현재 미응답 질문과 다릅니다.")
            actual = {k: question.get(k) for k in ["id", "question", "options"]}
            if actual != pending:
                raise StateError("표시한 질문·선택지가 저장된 질문과 다릅니다.")
            if any(a["source"].get("session_id") == session_id and a["source"].get("call_id") == call_id
                   for a in view["answers"].values()):
                raise StateError("다른 질문에 사용한 선택입니다.")
            return {"id": question_id, "text": normalized(text), "source": receipt,
                    "authority": "observation_only"}
        return self._change(revision, "interview_answer", build)

    def observe_approval(self, client, thread_id, revision, selection=None):
        """CLI가 선택한 실제 스레드에서 읽은 응답만 저장. 호스트 실증 전 권한 없음."""
        from .probe import observe_user_reply

        snapshot = self.status()
        if snapshot["revision"] != revision:
            from .state import RevisionConflict
            raise RevisionConflict("stale revision; reload current state")
        review = snapshot["review"]
        if not review or snapshot["manifest_matches"] is not True:
            raise StateError("승인 대상 또는 실제 스펙 파일을 확인해 주세요.")
        if review.get("thread_id") and review["thread_id"] != thread_id:
            raise StateError("승인을 요청한 스레드와 응답 스레드가 다릅니다.")
        if selection:
            from .confirmation import selected
            receipt = selected(review, *selection)
        else:
            receipt = observe_user_reply(client, thread_id, review["approval_text"])
        if not receipt:
            raise StateError("해당 스펙에 대한 명시적 승인 응답이 없습니다.")
        def build(view):
            if view["review"] != review or manifest(self.store.project, review["files"]) != review["files"]:
                raise StateError("승인 대기 중 스펙이 변경되었습니다.")
            if view["approval"]:
                raise StateError("동일 승인 관찰이 이미 저장되어 있습니다.")
            return {"target": review["target"], "version": review["version"],
                    "files": review["files"], "challenge": review["challenge"],
                    "source": receipt, "authority": "verified_selection_binding" if selection and review["target"] == "spec-set" else "verified_user_message_binding" if review["target"] == "spec-set"
                    and review.get("thread_id") == thread_id else "binding_pending_host_validation"}
        return self._change(revision, "spec_approval_observed", build)
