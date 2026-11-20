"""새 AI 턴을 시작하지 않는 Codex App Server 읽기 전용 연결."""

from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import time
from pathlib import Path
from typing import Any


class RpcError(RuntimeError):
    pass


class ReadOnlyCodex:
    METHODS = frozenset({
        "initialize", "thread/read", "thread/turns/list", "thread/items/list",
        "experimentalFeature/list",
    })

    def __init__(self, executable: Path, timeout: float = 20.0):
        self.executable = executable.resolve(strict=True)
        self.timeout = timeout
        self._messages: queue.Queue[Any] = queue.Queue()
        self._sequence = 0
        self._process: subprocess.Popen[str] | None = None

    def __enter__(self):
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        self._process = subprocess.Popen(
            [str(self.executable), "app-server", "--stdio"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", creationflags=flags,
        )
        threading.Thread(target=self._reader, daemon=True).start()
        try:
            self.initialize_result = self.call("initialize", {
                "clientInfo": {"name": "build_loop_probe", "version": "0.1.0"},
                "capabilities": {"experimentalApi": True},
            })
            self._send({"method": "initialized", "params": {}})
        except BaseException:
            self.__exit__(None, None, None)
            raise
        return self

    def _reader(self):
        assert self._process and self._process.stdout
        try:
            for line in self._process.stdout:
                try:
                    self._messages.put(json.loads(line))
                except json.JSONDecodeError:
                    self._messages.put(RpcError("app-server emitted non-JSON data"))
        except (OSError, ValueError):
            pass
        finally:
            self._messages.put(RpcError("app-server stream closed"))

    def _send(self, payload: dict):
        assert self._process and self._process.stdin
        self._process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
        self._process.stdin.flush()

    def call(self, method: str, params: dict) -> dict:
        if method not in self.METHODS:
            raise RpcError("method is outside the read-only allowlist")
        self._sequence += 1
        request_id = self._sequence
        self._send({"id": request_id, "method": method, "params": params})
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                reply = self._messages.get(timeout=max(0, deadline - time.monotonic()))
            except queue.Empty as exc:
                raise RpcError(f"timeout: {method}") from exc
            if isinstance(reply, BaseException):
                raise reply
            # Unexpected server requests are rejected, never auto-approved.
            if reply.get("method") and "id" in reply:
                self._send({"id": reply["id"], "error": {
                    "code": -32601, "message": "read-only probe does not accept requests",
                }})
                continue
            if reply.get("id") != request_id:
                continue
            if "error" in reply:
                # Never print raw RPC messages or account/credential diagnostics.
                code = reply["error"].get("code", "unknown")
                raise RpcError(f"RPC rejected {method}: {code}")
            return reply.get("result", {})

    def __exit__(self, *_):
        if self._process:
            try:
                if self._process.stdin:
                    self._process.stdin.close()
                self._process.wait(timeout=3)
            except (OSError, subprocess.TimeoutExpired):
                self._process.terminate()
                try:
                    self._process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self._process.kill()
                    self._process.wait(timeout=3)
            if self._process.stdout:
                self._process.stdout.close()

