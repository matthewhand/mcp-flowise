"""Helpers to drive the real MCP servers over a real stdio transport.

These tests spawn ``python -m mcp_flowise`` as a subprocess and talk to it with
the official ``mcp`` client (``stdio_client`` + ``ClientSession``): initialize,
``tools/list`` and ``tools/call`` all travel through real MCP JSON-RPC over real
pipes. The Flowise side is the task-owned mock in ``tests/mock_flowise.py``, which
records every request so tests can assert on call counts.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable, Dict, Iterable, Mapping, Optional

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

REPO_ROOT = Path(__file__).resolve().parents[2]

# Environment variables that must never leak in from the developer machine.
_STRIPPED_PREFIXES = ("FLOWISE_",)
_STRIPPED_NAMES = {"DEBUG", "LOGLEVEL"}


def server_env(endpoint: str, **overrides: str) -> Dict[str, str]:
    """Build a clean environment for the server subprocess."""
    env = {key: value for key, value in os.environ.items() if key not in _STRIPPED_NAMES}
    for key in list(env):
        if any(key.startswith(prefix) for prefix in _STRIPPED_PREFIXES):
            del env[key]
    env["PYTHONPATH"] = str(REPO_ROOT)
    env["PYTHONIOENCODING"] = "utf-8"
    env["FLOWISE_API_ENDPOINT"] = endpoint
    env["FLOWISE_API_KEY"] = "test-api-key"
    env["FLOWISE_API_TIMEOUT"] = "10"
    env.update(overrides)
    return env


def server_params(env: Mapping[str, str], args: Optional[Iterable[str]] = None) -> StdioServerParameters:
    return StdioServerParameters(
        command=sys.executable,
        args=list(args or ["-m", "mcp_flowise"]),
        env=dict(env),
        cwd=str(REPO_ROOT),
    )


@contextlib.asynccontextmanager
async def client_session(env: Mapping[str, str], errlog: Any = None) -> AsyncIterator[ClientSession]:
    """Start the server and yield an initialized MCP client session."""
    params = server_params(env)
    async with stdio_client(params, errlog=errlog if errlog is not None else sys.stderr) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


def run_session(env: Mapping[str, str], body: Callable[[ClientSession], Awaitable[Any]], errlog: Any = None) -> Any:
    """Run ``body`` against a freshly started server, returning its result."""

    async def _main() -> Any:
        async with client_session(env, errlog) as session:
            return await body(session)

    return asyncio.run(_main())


def tool_names(tools_result: Any) -> list:
    return [tool.name for tool in tools_result.tools]


def result_text(result: Any) -> str:
    """Concatenate the text content of a CallToolResult."""
    return "".join(getattr(block, "text", "") for block in result.content)


def result_json(result: Any) -> Any:
    """Decode the sanitized JSON payload of a tool result.

    FastMCP prefixes tool errors with ``Error executing tool <name>: ``, so the
    payload starts at the first ``{`` (or ``[`` for the catalog).
    """
    text = result_text(result)
    starts = [pos for pos in (text.find("{"), text.find("[")) if pos >= 0]
    if not starts:
        raise AssertionError(f"no JSON payload in tool result: {text!r}")
    return json.loads(text[min(starts) :])


def raw_protocol_exchange(
    env: Mapping[str, str],
    requests: Iterable[Mapping[str, Any]],
    *,
    timeout: float = 30.0,
    args: Optional[Iterable[str]] = None,
) -> "RawExchange":
    """Speak raw JSON-RPC to the server and capture *everything* it writes.

    stdout and stderr are drained concurrently so a chatty server cannot
    deadlock the test, and both streams are returned verbatim for assertions
    about protocol purity and secret hygiene.
    """
    proc = subprocess.Popen(
        [sys.executable, *list(args or ["-m", "mcp_flowise"])],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(REPO_ROOT),
        env=dict(env),
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    stdout_chunks: list = []
    stderr_chunks: list = []

    def drain(stream, sink):
        for line in stream:
            sink.append(line)
        stream.close()

    threads = [
        threading.Thread(target=drain, args=(proc.stdout, stdout_chunks), daemon=True),
        threading.Thread(target=drain, args=(proc.stderr, stderr_chunks), daemon=True),
    ]
    for thread in threads:
        thread.start()

    def send(message: Mapping[str, Any]) -> None:
        assert proc.stdin is not None
        proc.stdin.write(json.dumps(message) + "\n")
        proc.stdin.flush()

    initialize = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "raw-probe", "version": "1.0"},
        },
    }
    deadline = time.monotonic() + timeout
    try:
        send(initialize)
        _wait_for_response(stdout_chunks, 1, deadline)
        send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        for index, request in enumerate(requests, start=2):
            send({"jsonrpc": "2.0", "id": index, **dict(request)})
            _wait_for_response(stdout_chunks, index, deadline)
    except Exception:
        proc.kill()
        raise
    finally:
        try:
            if proc.stdin is not None:
                proc.stdin.close()
        except OSError:
            pass

    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover - defensive
            proc.kill()
    for thread in threads:
        thread.join(timeout=5)

    return RawExchange(
        stdout="".join(stdout_chunks),
        stderr="".join(stderr_chunks),
        returncode=proc.returncode,
    )


@dataclass
class RawExchange:
    stdout: str
    stderr: str
    returncode: Optional[int]

    @property
    def stdout_lines(self) -> list:
        return [line for line in self.stdout.splitlines() if line.strip()]


def _wait_for_response(chunks: list, request_id: int, deadline: float) -> str:
    while time.monotonic() < deadline:
        for line in list(chunks):
            try:
                message = json.loads(line)
            except ValueError:
                continue
            if message.get("id") == request_id:
                return line
        time.sleep(0.02)
    raise AssertionError(f"no JSON-RPC response with id={request_id} within the deadline")


def run_to_completion(
    env: Mapping[str, str], *, timeout: float = 30.0, args: Optional[Iterable[str]] = None
) -> RawExchange:
    """Start the server, let it fail or exit on its own, and capture its output."""
    proc = subprocess.Popen(
        [sys.executable, *list(args or ["-m", "mcp_flowise"])],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(REPO_ROOT),
        env=dict(env),
        encoding="utf-8",
        errors="replace",
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:  # pragma: no cover - defensive
        proc.kill()
        stdout, stderr = proc.communicate()
    return RawExchange(stdout=stdout or "", stderr=stderr or "", returncode=proc.returncode)
