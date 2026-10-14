"""FastMCP (simple) mode server for mcp-flowise.

Exposes two static tools, ``list_chatflows`` and ``create_prediction``. Neither
performs startup discovery: the Flowise API is contacted only when a tool call
actually needs it (and, when a filter policy is configured, when a target has to
be authorized against the catalog).

Every failure is surfaced to the client as an MCP tool error
(``isError: true``) carrying a sanitized JSON payload - never as an ordinary
text result, so a broken deployment can never be mistaken for a valid answer.
"""

from __future__ import annotations

import json
from typing import Optional

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from mcp_flowise.config import FlowiseConfig, validate_question
from mcp_flowise.errors import ConfigError, FlowiseError
from mcp_flowise.utils import FlowiseClient, RuntimeContext, get_context, get_logger, setup_logging

SERVER_NAME = "FlowiseMCP-with-EnvAuth"

logger = get_logger("fastmcp")

mcp = FastMCP(SERVER_NAME)


def _context() -> RuntimeContext:
    return get_context()


def _tool_error(exc: FlowiseError) -> ToolError:
    """Convert a typed error into a sanitized MCP tool error."""
    if isinstance(exc, FlowiseError):
        payload = exc.to_payload()
        logger.error("%s: %s", exc.code, exc.message)
    else:  # pragma: no cover - defensive
        payload = {"error": "internal_error", "message": "Unexpected internal error"}
        logger.error("Unexpected internal error", exc_info=True)
    return ToolError(json.dumps(payload))


@mcp.tool()
def list_chatflows() -> str:
    """List the chatflows this server is allowed to use.

    Honors the configured whitelist/blacklist policy
    (``FLOWISE_WHITELIST_ID``, ``FLOWISE_BLACKLIST_ID``,
    ``FLOWISE_WHITELIST_NAME_REGEX``, ``FLOWISE_BLACKLIST_NAME_REGEX``).

    Returns:
        A JSON array of ``{"id": ..., "name": ...}`` objects. An empty array
        means Flowise really has no chatflow left after filtering; any failure
        is reported as an MCP tool error instead.
    """
    context = _context()
    try:
        # Refreshed on every call so the listing reflects Flowise's current state
        # (and so a discovery failure surfaces instead of serving a stale list).
        allowed, _index = context.directory.refresh()
        return json.dumps([record.as_dict() for record in allowed])
    except FlowiseError as exc:
        raise _tool_error(exc) from None


@mcp.tool()
def create_prediction(*, question: str, chatflow_id: Optional[str] = None) -> str:
    """Send a question to a Flowise chatflow and return the raw JSON answer.

    Args:
        question: The question or prompt to send to the chatflow.
        chatflow_id: Chatflow to query. Defaults to ``FLOWISE_CHATFLOW_ID``.
            When a filter policy is configured the id must be allowed by it.

    Returns:
        The JSON payload returned by Flowise, with all of its fields intact.
    """
    context = _context()
    try:
        question = validate_question(question)
        target = chatflow_id or context.config.chatflow_id
        if not target:
            raise ConfigError(
                "No chatflow_id argument was provided and FLOWISE_CHATFLOW_ID is not configured"
            )
        # Authorization happens before any prediction POST is issued.
        record = context.directory.authorize(target, source="create_prediction")
        payload = context.client.predict(record.id, question)
        return json.dumps(payload)
    except FlowiseError as exc:
        raise _tool_error(exc) from None


def run_simple_server(config: Optional[FlowiseConfig] = None, client: Optional[FlowiseClient] = None) -> None:
    """Run the FastMCP server over stdio."""
    context = get_context(config=config, client=client)
    setup_logging(debug=context.config.debug)
    logger.debug("Starting FastMCP server (mode=simple, endpoint=%s).", context.config.endpoint)
    try:
        mcp.run(transport="stdio")
    except KeyboardInterrupt:  # pragma: no cover - interactive
        logger.debug("Server shutdown requested.")
    except Exception as exc:  # pragma: no cover - defensive
        logger.critical("Unhandled exception in MCP server: %s", exc)
        raise SystemExit(1) from None


if __name__ == "__main__":  # pragma: no cover - manual invocation
    run_simple_server()
