"""현재 사용자 대화의 출처를 읽기 전용으로 관찰하고 민감정보 없이 진단한다."""

from __future__ import annotations

import hashlib
import re
import subprocess
import unicodedata
from pathlib import Path

from .rpc import ReadOnlyCodex, RpcError


def observe_user_reply(client: ReadOnlyCodex, thread_id: str, expected_text: str):
    if not thread_id or not expected_text.strip():
        raise ValueError("thread and expected reply are required")
    metadata = client.call("thread/read", {"threadId": thread_id, "includeTurns": False})
    thread = metadata.get("thread", {})
    # Restrict reads to the explicitly selected existing thread. No new tasks/turns.
    if thread.get("id") != thread_id:
        raise RpcError("thread identity mismatch")
    turns = client.call("thread/turns/list", {
        "threadId": thread_id, "limit": 5, "sortDirection": "desc", "itemsView": "notLoaded",
    })
    client.observation_debug = {"turn_response_keys": sorted(turns), "turn_count": len(turns.get("data", [])), "items": []}
    target_hash = hashlib.sha256(unicodedata.normalize("NFC", expected_text).encode("utf-8")).hexdigest()
    for turn in turns.get("data", []):
        turn_id = turn.get("id")
        if not turn_id:
            continue
        result = client.call("thread/items/list", {
            "threadId": thread_id, "turnId": turn_id, "limit": 20, "sortDirection": "asc",
        })
        client.observation_debug["items"].append({
            "response_keys": sorted(result), "count": len(result.get("data", [])),
            "types": sorted({i.get("item", {}).get("type", "unknown") for i in result.get("data", [])}),
        })
        for entry in result.get("data", []):
            if entry.get("turnId") != turn_id:
                raise RpcError("item belongs to a different turn")
            item = entry.get("item", {})
            if item.get("type") != "userMessage":
                continue
            text = "\n".join(p.get("text", "") for p in item.get("content", []) if p.get("type") == "text")
            if hashlib.sha256(unicodedata.normalize("NFC", text).encode("utf-8")).hexdigest() == target_hash:
                return {
                    "kind": "user_reply_observation", "transport": "codex_app_server_stdio",
                    "thread_id": thread_id, "turn_id": turn_id,
                    "item_id": item["id"], "text_sha256": target_hash,
                }
    return None


def diagnose(executable: Path, thread_id: str | None = None, expected_reply: str | None = None):
    version = subprocess.run([str(executable), "--version"], capture_output=True,
                             text=True, encoding="utf-8", timeout=10, check=True).stdout.strip()
    report = {
        "scope": "read-only first integration probe; not a complete installation",
        "cli_version": version if re.fullmatch(r"codex-cli [0-9A-Za-z.+-]+", version) else "unrecognized",
        "new_model_turns_started": 0, "global_configuration_changed": False,
        "native_capabilities": {k: "unverified" for k in [
            "selection_ui", "verified_user_source", "guard_enforcement", "resume",
        ]},
        "approval_binding": "implemented in workflow/confirmation; this read-only observation alone cannot authorize a transition",
        "ready_for_managed_work": False,
    }
    try:
        with ReadOnlyCodex(executable) as client:
            report["stdio_handshake"] = "passed"
            if thread_id and expected_reply:
                receipt = observe_user_reply(client, thread_id, expected_reply)
                report["user_reply_observation"] = receipt
                report["user_reply_read"] = "passed" if receipt else "not_found"
                if not receipt:
                    report["read_structure"] = getattr(client, "observation_debug", {})
            else:
                report["user_reply_read"] = "not_requested"
    except (RpcError, OSError, subprocess.SubprocessError) as exc:
        report["stdio_or_thread_read"] = type(exc).__name__
        if isinstance(exc, RpcError):
            report["reason"] = str(exc)
    return report

