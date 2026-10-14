"""Typed, sanitizable error taxonomy for mcp-flowise.

Every failure that can reach an MCP client is represented by one of these
exceptions. Each carries a stable machine-readable ``code`` plus a short,
already-sanitized message, so that callers can distinguish

* configuration problems (``config_error``),
* policy/authorization denials (``policy_denied``),
* transport problems (timeout vs connection failure vs non-2xx HTTP status),
* protocol problems (malformed JSON payload), and
* chatflow discovery failures (``discovery_failed`` + ``cause``)

without ever echoing secrets, questions or upstream response bodies.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

__all__ = [
    "FlowiseError",
    "ConfigError",
    "PolicyDeniedError",
    "FlowiseTimeoutError",
    "FlowiseConnectionError",
    "FlowiseHttpError",
    "MalformedResponseError",
    "DiscoveryError",
]


class FlowiseError(Exception):
    """Base class for all errors that mcp-flowise reports to MCP clients."""

    code = "flowise_error"

    def __init__(self, message: str, *, detail: Optional[str] = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.message

    def to_payload(self) -> Dict[str, Any]:
        """Return a JSON-serializable, sanitized description of the failure."""
        payload: Dict[str, Any] = {"error": self.code, "message": self.message}
        if self.detail:
            payload["detail"] = self.detail
        return payload


class ConfigError(FlowiseError):
    """Invalid or contradictory environment configuration."""

    code = "config_error"


class PolicyDeniedError(FlowiseError):
    """The requested chatflow is not permitted by the configured filter policy."""

    code = "policy_denied"


class FlowiseTimeoutError(FlowiseError):
    """The upstream Flowise API did not answer within the configured timeout."""

    code = "timeout"


class FlowiseConnectionError(FlowiseError):
    """The upstream Flowise API could not be reached."""

    code = "connection_failed"


class FlowiseHttpError(FlowiseError):
    """The upstream Flowise API answered with a non-2xx status code.

    The upstream response body is deliberately *not* captured or reported:
    Flowise HTML error pages and JSON error payloads can contain internal
    details that should never be relayed to an MCP client.
    """

    code = "http_error"

    def __init__(self, message: str, *, status_code: Optional[int] = None) -> None:
        super().__init__(message)
        self.status_code = status_code

    def to_payload(self) -> Dict[str, Any]:
        payload = super().to_payload()
        if self.status_code is not None:
            payload["status_code"] = self.status_code
        return payload


class MalformedResponseError(FlowiseError):
    """The upstream Flowise API answered with a body that is not usable JSON."""

    code = "malformed_response"


class DiscoveryError(FlowiseError):
    """Chatflow discovery failed.

    ``cause`` keeps the underlying reason (timeout / connection_failed /
    http_error / malformed_response) so callers can tell a broken deployment
    apart from a legitimately empty catalog.
    """

    code = "discovery_failed"

    def __init__(self, message: str, *, cause: str = "unknown") -> None:
        super().__init__(message)
        self.cause = cause

    def to_payload(self) -> Dict[str, Any]:
        payload = super().to_payload()
        payload["cause"] = self.cause
        return payload
