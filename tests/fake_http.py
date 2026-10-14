"""Minimal fake ``requests.Session`` used by unit tests."""

from __future__ import annotations

import json as _json
from typing import Any, Dict, List, Mapping, Optional

import requests


class FakeResponse:
    def __init__(
        self,
        status_code: int = 200,
        body: str = "[]",
        headers: Optional[Mapping[str, str]] = None,
    ) -> None:
        self.status_code = status_code
        self.text = body
        self.headers = dict(headers or {"Content-Type": "application/json"})

    def json(self) -> Any:
        return _json.loads(self.text)


class FakeSession:
    """Records calls and replays a queue of responses/exceptions."""

    def __init__(self, outcomes: Optional[List[Any]] = None) -> None:
        self.outcomes: List[Any] = list(outcomes or [])
        self.calls: List[Dict[str, Any]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> Any:
        self.calls.append({"method": method, "url": url, **kwargs})
        if not self.outcomes:
            raise AssertionError(f"unexpected extra request: {method} {url}")
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        if callable(outcome):
            return outcome(method, url, kwargs)
        return outcome

    @property
    def urls(self) -> List[str]:
        return [call["url"] for call in self.calls]

    def count(self, method: str) -> int:
        return sum(1 for call in self.calls if call["method"] == method)


def timeout_error() -> requests.Timeout:
    return requests.Timeout("timed out")


def connection_error() -> requests.ConnectionError:
    return requests.ConnectionError("connection refused")
