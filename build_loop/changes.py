"""명시적으로 확인한 CR의 문서 변경을 일괄 반영하고 이력을 보존한다."""

import hashlib
import json
import secrets
from pathlib import Path

from .confirmation import question, selected
from .state import StateError
from .workflow import Workflow, manifest


class Changes:
    def __init__(self, store):
        self.store = store

    def request(self, cr_path, files, version, thread, revision):
        flow = Workflow(self.store).status()
        if not flow["review"] or flow["manifest_matches"] is not True or not files or not version or not thread:
            raise StateError("현재 승인 스펙과 변경 대상·버전·스레드가 필요합니다.")
        proposal = (self.store.project / cr_path).resolve(strict=True)
        open_root = self.store.project / "docs/changes/open"
        if not proposal.is_relative_to(open_root) or proposal.suffix != ".md":
            raise StateError("열린 CR 문서가 필요합니다.")
        for name, content in files.items():
            target = (self.store.project / name).resolve(strict=True)
            if name not in flow["review"]["files"] or not target.is_relative_to(self.store.project / "docs") or not isinstance(content, str) or not content.strip():
                raise StateError("승인 스펙 안의 기존 문서와 변경 내용만 지정하세요.")
        nonce = secrets.token_hex(12)
        payload = {"cr": cr_path, "cr_hash": hashlib.sha256(proposal.read_bytes()).hexdigest(), "files": files,
                   "before": flow["review"]["files"], "version": version, "thread_id": thread,
                   "question": question(proposal.stem + " 변경안", version, nonce), "nonce": nonce,
                   "was_approved": flow["development_allowed"]}
        with self.store._write(revision) as (db, state):
            if state["paused"]:
                raise StateError("중단 상태입니다.")
            self.store._event(db, state, "change_review_requested", payload)
        return {k: v for k, v in payload.items() if k not in {"files", "before"}}

    def apply(self, path, session, call, revision):
        from .spec_check import inspect_spec
        from .director import stage_records
        snapshot = self.store.snapshot()
        requests = [json.loads(e["payload"]) for e in snapshot["events"] if e["kind"] == "change_review_requested"]
        if not requests:
            raise StateError("확인할 변경안이 없습니다.")
        request = requests[-1]
        if any(e["kind"] == "change_merged" and json.loads(e["payload"])["nonce"] == request["nonce"] for e in snapshot["events"]):
            raise StateError("이미 반영한 CR입니다.")
        receipt = selected(request, path, session, call)
        proposal = self.store.project / request["cr"]
        if hashlib.sha256(proposal.read_bytes()).hexdigest() != request["cr_hash"] or manifest(self.store.project, request["before"]) != request["before"]:
            raise StateError("변경 검토 이후 원본 또는 CR이 바뀌었습니다.")
        _, confirmations = stage_records(snapshot)
        originals = {name: (self.store.project / name).read_bytes() for name in request["files"]}
        archive = self.store.project / ".build-loop/evidence/changes" / request["nonce"]
        archive.mkdir(parents=True)
        (archive / "originals.json").write_text(json.dumps({k: v.decode("utf-8") for k, v in originals.items()}, ensure_ascii=False), encoding="utf-8")
        written = {}
        try:
            with self.store._write(revision) as (db, state):
                if state["paused"]:
                    raise StateError("중단 상태입니다.")
                for name, content in request["files"].items():
                    target = self.store.project / name
                    target.write_text(content, encoding="utf-8")
                    written[name] = hashlib.sha256(target.read_bytes()).hexdigest()
                check = inspect_spec(self.store.project)
                if not check["structural_passed"]:
                    raise StateError("CR 반영 후 스펙 검사가 실패했습니다: " + ", ".join(check["issues"]))
                def event(kind, payload):
                    state["revision"] = self.store._event(db, state, kind, payload)
                for stage, previous in confirmations.items():
                    event("stage_user_confirmed", {**previous, "files": manifest(self.store.project, previous["files"]), "source": receipt, "cr": request["cr"]})
                review = {"target": "spec-set", "version": request["version"], "files": check["files"],
                          "thread_id": request["thread_id"], "challenge": request["nonce"], "approval_text": "CR 확인",
                          "question": request["question"]}
                if not request["was_approved"]:
                    review["challenge"] = secrets.token_hex(12)
                    review["question"] = question("spec-set", request["version"], review["challenge"])
                    review["approval_text"] = f"승인 spec-set {request['version']} {review['challenge']}"
                event("spec_review_requested", review)
                if request["was_approved"]:
                    event("spec_approval_observed", {**review, "source": receipt, "authority": "verified_selection_binding"})
                event("change_merged", {"cr": request["cr"], "nonce": request["nonce"], "version": request["version"]})
        except BaseException:
            for name, original in originals.items():
                target = self.store.project / name
                if name in written and hashlib.sha256(target.read_bytes()).hexdigest() == written[name]:
                    target.write_bytes(original)
            raise
        merged = self.store.project / "docs/changes/merged" / proposal.name
        merged.parent.mkdir(parents=True, exist_ok=True)
        if not merged.exists():
            proposal.rename(merged)
        with (self.store.project / "docs/CHANGELOG.md").open("a", encoding="utf-8") as stream:
            stream.write(f"\n| CR 반영 | {request['version']} | {', '.join(request['files'])} | {proposal.stem} |\n")
        return Workflow(self.store).status()
