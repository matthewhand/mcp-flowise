"""Task-owned mock of the Flowise HTTP API.

This is a **test double**, not a Flowise installation: it exists so the MCP
servers can be exercised end-to-end over real HTTP and real stdio without
touching any shared or upstream Flowise deployment.

Every request is recorded so tests can assert on *counts* (for example: a denied
tool call must produce zero ``POST /api/v1/prediction/...`` requests).
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional

DEFAULT_PREDICTION = {
    "text": "mock answer",
    "chatId": "chat-mock",
    "sessionId": "session-mock",
    "json": {"answer": 42},
}


@dataclass
class RecordedRequest:
    method: str
    path: str
    authorization: Optional[str]
    question: Optional[str] = None


@dataclass
class MockState:
    """Mutable behaviour knobs of the mock server."""

    chatflows: List[Dict[str, str]] = field(default_factory=list)
    chatflows_status: int = 200
    chatflows_body: Optional[str] = None
    chatflows_content_type: str = "application/json"
    chatflows_delay: float = 0.0

    prediction_status: int = 200
    prediction_body: Optional[str] = None
    prediction_content_type: str = "application/json"
    prediction_delay: float = 0.0

    expected_api_key: Optional[str] = None

    requests: List[RecordedRequest] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)

    # --- recording helpers -------------------------------------------------
    def record(self, method: str, path: str, authorization: Optional[str], question: Optional[str] = None) -> None:
        with self.lock:
            self.requests.append(RecordedRequest(method, path, authorization, question))

    def requests_for(self, method: str) -> List[RecordedRequest]:
        with self.lock:
            return [r for r in self.requests if r.method == method]

    @property
    def discovery_calls(self) -> int:
        return len(self.requests_for("GET"))

    @property
    def prediction_calls(self) -> int:
        return len(self.requests_for("POST"))

    def prediction_paths(self) -> List[str]:
        return [r.path for r in self.requests_for("POST")]

    def reset_recordings(self) -> None:
        with self.lock:
            self.requests.clear()

    def reset(self) -> None:
        """Restore default behaviour (call this between phases of a test)."""
        self.chatflows_status = 200
        self.chatflows_body = None
        self.chatflows_content_type = "application/json"
        self.chatflows_delay = 0.0
        self.prediction_status = 200
        self.prediction_body = None
        self.prediction_content_type = "application/json"
        self.prediction_delay = 0.0
        self.expected_api_key = None
        self.reset_recordings()


class _Handler(BaseHTTPRequestHandler):
    server_version = "MockFlowise/1.0"

    # Requests must never reach stderr of the test runner.
    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003 - stdlib signature
        pass

    @property
    def state(self) -> MockState:
        return self.server.state  # type: ignore[attr-defined]

    def _respond(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):  # client already gave up
            pass

    def _json(self, status: int, payload: Any, content_type: str = "application/json") -> None:
        self._respond(status, json.dumps(payload).encode("utf-8"), content_type)

    def _authorized(self) -> bool:
        expected = self.state.expected_api_key
        if expected is None:
            return True
        return self.headers.get("Authorization") == f"Bearer {expected}"

    def do_GET(self) -> None:  # noqa: N802 - stdlib signature
        state = self.state
        state.record("GET", self.path, self.headers.get("Authorization"))
        if self.path.split("?", 1)[0] != "/api/v1/chatflows":
            self._json(404, {"error": "not found"})
            return
        if not self._authorized():
            self._json(401, {"error": "unauthorized"})
            return
        if state.chatflows_delay:
            _sleep(state.chatflows_delay)
        if state.chatflows_status != 200:
            self._respond(state.chatflows_status, b'{"error":"upstream failure"}', "application/json")
            return
        body = state.chatflows_body
        if body is None:
            body = json.dumps(state.chatflows)
        self._respond(200, body.encode("utf-8"), state.chatflows_content_type)

    def do_POST(self) -> None:  # noqa: N802 - stdlib signature
        state = self.state
        path = self.path.split("?", 1)[0]
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        question = None
        if raw:
            try:
                question = json.loads(raw.decode("utf-8")).get("question")
            except ValueError:
                question = None
        state.record("POST", path, self.headers.get("Authorization"), question)

        if not path.startswith("/api/v1/prediction/"):
            self._json(404, {"error": "not found"})
            return
        if not self._authorized():
            self._json(401, {"error": "unauthorized"})
            return
        if state.prediction_delay:
            _sleep(state.prediction_delay)
        if state.prediction_status != 200:
            self._respond(state.prediction_status, b"<html>flowise error page</html>", "text/html")
            return
        body = state.prediction_body
        if body is None:
            body = json.dumps(DEFAULT_PREDICTION)
        self._respond(200, body.encode("utf-8"), state.prediction_content_type)


def _sleep(seconds: float) -> None:
    import time

    time.sleep(seconds)


class MockFlowise:
    """Context manager starting the mock on an ephemeral localhost port.

    ``host="0.0.0.0"`` is only needed when the server under test runs in a
    container and has to reach the mock through ``host.docker.internal``.
    """

    def __init__(self, state: Optional[MockState] = None, host: str = "127.0.0.1") -> None:
        self.state = state or MockState()
        self.host = host
        self._httpd: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    @property
    def port(self) -> int:
        assert self._httpd is not None, "mock server not started"
        return int(self._httpd.server_address[1])

    @property
    def base_url(self) -> str:
        return f"http://{'127.0.0.1' if self.host == '0.0.0.0' else self.host}:{self.port}"

    def start(self) -> "MockFlowise":
        httpd = ThreadingHTTPServer((self.host, 0), _Handler)
        httpd.daemon_threads = True
        httpd.state = self.state  # type: ignore[attr-defined]
        self._httpd = httpd
        self._thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def __enter__(self) -> "MockFlowise":
        return self.start()

    def __exit__(self, *exc_info: Any) -> None:
        self.stop()

    # convenience -------------------------------------------------------
    @property
    def chatflows(self) -> List[Dict[str, str]]:
        return self.state.chatflows

    @chatflows.setter
    def chatflows(self, value: List[Dict[str, str]]) -> None:
        self.state.chatflows = value

    @property
    def discovery_calls(self) -> int:
        return self.state.discovery_calls

    @property
    def prediction_calls(self) -> int:
        return self.state.prediction_calls
