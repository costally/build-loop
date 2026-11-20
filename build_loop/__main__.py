import argparse
import json
import os
import sys
from pathlib import Path

from .probe import diagnose
from .state import StateStore
from .workflow import Workflow
from .probe import observe_user_reply
from .rpc import ReadOnlyCodex


def main():
    # JSON output has the same encoding in Windows terminals and redirected pipes.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="build-loop: idea to delivery")
    from . import __version__
    parser.add_argument("--version", action="version", version=__version__)
    subs = parser.add_subparsers(dest="command", required=True)
    start = subs.add_parser("start")
    start.add_argument("--root", type=Path, default=Path("C:/build-loop"))
    start.add_argument("--name", required=True)
    start.add_argument("--idea", required=True)
    start.add_argument("--templates", type=Path, default=Path(__file__).resolve().parents[1] / "templates")
    projects = subs.add_parser("projects")
    projects.add_argument("--root", type=Path, default=Path("C:/build-loop"))
    guide_parser = subs.add_parser("guide")
    guide_parser.add_argument("--project", type=Path, required=True)
    delivery = subs.add_parser("delivery")
    delivery.add_argument("--project", type=Path, required=True)
    delivery_actions = delivery.add_subparsers(dest="delivery_action", required=True)
    delivery_actions.add_parser("status")
    task_plan = delivery_actions.add_parser("plan")
    task_plan.add_argument("--input", type=Path, required=True)
    task_plan.add_argument("--revision", type=int, required=True)
    task_run = delivery_actions.add_parser("check")
    task_run.add_argument("--task", required=True)
    task_run.add_argument("--revision", type=int, required=True)
    request = delivery_actions.add_parser("request-confirmation")
    request.add_argument("--kind", choices=["demo", "completion"], required=True)
    request.add_argument("--thread", required=True)
    request.add_argument("--revision", type=int, required=True)
    confirm = delivery_actions.add_parser("confirm")
    confirm.add_argument("--kind", choices=["demo", "completion"], required=True)
    confirm.add_argument("--codex", type=Path, required=True)
    confirm.add_argument("--revision", type=int, required=True)
    stage_review = subs.add_parser("stage-review")
    stage_review.add_argument("--project", type=Path, required=True)
    stage_review.add_argument("--stage", required=True)
    stage_review.add_argument("--thread", required=True)
    stage_review.add_argument("--revision", type=int, required=True)
    stage_confirm = subs.add_parser("stage-confirm")
    stage_confirm.add_argument("--project", type=Path, required=True)
    stage_confirm.add_argument("--stage", required=True)
    stage_confirm.add_argument("--codex", type=Path, required=True)
    stage_confirm.add_argument("--revision", type=int, required=True)
    doctor = subs.add_parser("doctor")
    doctor.add_argument("--codex", type=Path, required=True)
    doctor.add_argument("--thread")
    doctor.add_argument("--expected-reply")
    doctor.add_argument("--output", type=Path)
    state = subs.add_parser("state")
    state.add_argument("--project", type=Path, required=True)
    state.add_argument("--initialize", action="store_true")
    spec = subs.add_parser("check-spec")
    spec.add_argument("--project", type=Path, required=True)
    spec.add_argument("--docs", default="docs")
    spec.add_argument("--output", type=Path)
    selection = subs.add_parser("observe-selection")
    selection.add_argument("--session-file", type=Path, required=True)
    selection.add_argument("--session", required=True)
    selection.add_argument("--call", required=True)
    selection.add_argument("--question", required=True)
    flow = subs.add_parser("workflow")
    flow.add_argument("--project", type=Path, required=True)
    actions = flow.add_subparsers(dest="action", required=True)
    actions.add_parser("status")
    ask = actions.add_parser("ask")
    ask.add_argument("--input", type=Path, required=True)
    ask.add_argument("--revision", type=int, required=True)
    answer = actions.add_parser("answer")
    answer.add_argument("--id", required=True)
    answer.add_argument("--text", required=True)
    answer.add_argument("--thread", required=True)
    answer.add_argument("--codex", type=Path, required=True)
    answer.add_argument("--revision", type=int, required=True)
    capture = actions.add_parser("capture-selection")
    capture.add_argument("--session-file", type=Path, required=True)
    capture.add_argument("--session", required=True)
    capture.add_argument("--call", required=True)
    capture.add_argument("--question", required=True)
    capture.add_argument("--revision", type=int, required=True)
    review = actions.add_parser("review")
    review.add_argument("--target", required=True)
    review.add_argument("--version", required=True)
    review.add_argument("--files", nargs="+", required=True)
    review.add_argument("--revision", type=int, required=True)
    review.add_argument("--thread")
    approval = actions.add_parser("observe-approval")
    approval.add_argument("--thread", required=True)
    approval.add_argument("--codex", type=Path, required=True)
    approval.add_argument("--revision", type=int, required=True)
    args = parser.parse_args()
    if args.command == "projects":
        directory = args.root.resolve(strict=True) / "projects"
        paths = sorted(str(p.resolve()) for p in directory.iterdir()
                       if p.is_dir() and (p / ".build-loop/state.sqlite").is_file()) if directory.is_dir() else []
        print(json.dumps({"projects": paths}, ensure_ascii=False, indent=2))
        return 0
    if args.command in {"start", "guide"}:
        from .director import create_project, guide
        project = args.project if args.command == "guide" else create_project(
            args.root, args.name, args.idea, args.templates,
            enabled=os.getenv("BUILD_LOOP_EXPERIMENT_ENABLED") == "1")
        print(json.dumps(guide(project), ensure_ascii=False, indent=2))
        return 0
    if args.command == "observe-selection":
        from .selection import observe_selection
        print(json.dumps(observe_selection(args.session_file, args.session, args.call, args.question),
                         ensure_ascii=False, indent=2))
        return 0
    if args.command == "check-spec":
        from .spec_check import inspect_spec
        report = inspect_spec(args.project, args.docs)
        if args.output:
            args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["structural_passed"] else 2
    if args.command == "doctor":
        report = diagnose(args.codex, args.thread, args.expected_reply)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report.get("stdio_handshake") == "passed" else 2
    store = StateStore(args.project, enabled=os.getenv("BUILD_LOOP_EXPERIMENT_ENABLED") == "1")
    if args.command == "delivery":
        from .delivery import Delivery
        delivery = Delivery(store)
        if args.delivery_action == "plan":
            delivery.plan(json.loads(args.input.read_text(encoding="utf-8")), args.revision)
        elif args.delivery_action == "check":
            delivery.run_check(args.task, args.revision)
        elif args.delivery_action == "request-confirmation":
            request = delivery.request_user_confirmation(args.kind, args.thread, args.revision)
            print(json.dumps(request, ensure_ascii=False, indent=2))
            return 0
        elif args.delivery_action == "confirm":
            with ReadOnlyCodex(args.codex) as client:
                delivery.confirm_user(client, args.kind, args.revision)
        print(json.dumps(delivery.status(), ensure_ascii=False, indent=2))
        return 0
    if args.command in {"stage-review", "stage-confirm"}:
        from .director import request_stage_review, confirm_stage, guide
        if args.command == "stage-review":
            request_stage_review(store, args.stage, args.thread, args.revision)
        else:
            with ReadOnlyCodex(args.codex) as client:
                confirm_stage(store, client, args.stage, args.revision)
        print(json.dumps(guide(args.project), ensure_ascii=False, indent=2))
        return 0
    if args.command == "workflow":
        workflow = Workflow(store)
        if args.action == "ask":
            workflow.ask(json.loads(args.input.read_text(encoding="utf-8")), args.revision)
        elif args.action == "answer":
            with ReadOnlyCodex(args.codex) as client:
                receipt = observe_user_reply(client, args.thread, args.text)
            workflow.answer(args.id, args.text, receipt, args.revision)
        elif args.action == "capture-selection":
            workflow.capture_selection(args.session_file, args.session, args.call, args.question, args.revision)
        elif args.action == "review":
            workflow.review(args.target, args.version, args.files, args.revision, args.thread)
        elif args.action == "observe-approval":
            with ReadOnlyCodex(args.codex) as client:
                workflow.observe_approval(client, args.thread, args.revision)
        print(json.dumps(workflow.status(), ensure_ascii=False, indent=2))
        return 0
    if args.initialize:
        store.initialize()
    print(json.dumps(store.snapshot(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
