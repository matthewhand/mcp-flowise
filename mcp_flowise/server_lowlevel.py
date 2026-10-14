"""Low-level (dynamic) mode server for mcp-flowise.

One MCP tool is generated per Flowise chatflow. The catalog is discovered at
startup (a dynamic server cannot advertise tools it has never seen) and refreshed
atomically on every ``tools/list`` call: the tool list *and* the
name -> chatflow ID mapping are replaced together, so a tool name that
disappeared from Flowise stops being callable instead of silently keeping its
old target.

Tool names come from the chatflow names (``FLOWISE_CHATFLOW_DESCRIPTIONS``
overrides the description). Only chatflows allowed by the filter policy are ever
registered, and dispatch re-checks the policy against the current catalog before
issuing a prediction POST.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any, Dict, List, Mapping, Optional, Tuple

from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.models import InitializationOptions
from mcp.server.stdio import stdio_server

from mcp_flowise.config import FlowiseConfig, validate_question
from mcp_flowise.errors import DiscoveryError, FlowiseError
from mcp_flowise.utils import (
    ChatflowRecord,
    FlowiseClient,
    RuntimeContext,
    ToolCatalog,
    get_context,
    get_logger,
    normalize_tool_name,
    setup_logging,
)

SERVER_NAME = "FlowiseMCP-with-EnvAuth"
SERVER_VERSION = "0.1.0"

logger = get_logger("lowlevel")

mcp = Server(SERVER_NAME)

catalog = ToolCatalog()
_context: Optional[RuntimeContext] = None


def get_server_context() -> RuntimeContext:
    """Return the runtime context, creating it from the environment if needed."""
    global _context
    if _context is None:
        _context = get_context()
    return _context


def set_server_context(context: Optional[RuntimeContext]) -> None:
    """Install (or clear) the context used by this module."""
    global _context
    _context = context


def get_chatflow_descriptions(env: Optional[Mapping[str, str]] = None) -> Dict[str, str]:
    """Parse ``FLOWISE_CHATFLOW_DESCRIPTIONS`` (``id:description`` pairs)."""
    environ = os.environ if env is None else env
    raw = environ.get("FLOWISE_CHATFLOW_DESCRIPTIONS") or ""
    if not raw.strip():
        return {}

    descriptions: Dict[str, str] = {}
    for pair in raw.split(","):
        pair = pair.strip()
        if not pair:
            continue
        if ":" not in pair:
            logger.warning("Ignoring malformed FLOWISE_CHATFLOW_DESCRIPTIONS entry (expected 'id:description')")
            continue
        raw_id, description = pair.split(":", 1)
        identifier = raw_id.strip()
        description = description.strip()
        if identifier and description:
            descriptions[identifier] = description
    return descriptions


def build_tool(chatflow: ChatflowRecord, descriptions: Mapping[str, str]) -> types.Tool:
    """Build the MCP tool definition for a chatflow record."""
    description = descriptions.get(chatflow.id) or chatflow.name
    return types.Tool(
        name=normalize_tool_name(chatflow.name),
        description=description,
        inputSchema={
            "type": "object",
            "required": ["question"],
            "properties": {"question": {"type": "string", "minLength": 1}},
        },
    )


def register_tools(
    records: List[ChatflowRecord], descriptions: Mapping[str, str]
) -> Tuple[List[types.Tool], Dict[str, str]]:
    """Build and atomically install the tool catalog.

    Tools and the dispatch mapping are swapped in one step under a lock, so the
    mapping can never keep pointing at chatflows that are no longer exposed.

    Returns:
        ``(tools, mapping)`` for the freshly installed catalog.
    """
    tools: List[types.Tool] = []
    mapping: Dict[str, str] = {}
    for record in records:
        try:
            tool = build_tool(record, descriptions)
        except Exception as exc:  # pragma: no cover - defensive
            logger.error("Skipping chatflow '%s' (ID: '%s'): %s", record.name, record.id, exc)
            continue
        if tool.name in mapping:
            logger.warning(
                "Tool name conflict: '%s' already exists. Skipping chatflow '%s' (ID: '%s').",
                tool.name,
                record.name,
                record.id,
            )
            continue
        mapping[tool.name] = record.id
        tools.append(tool)
        logger.debug("Registered tool: %s (ID: %s)", tool.name, record.id)

    installed = catalog.replace(tools, mapping)
    return list(installed), dict(mapping)


def refresh_catalog(context: Optional[RuntimeContext] = None, descriptions: Optional[Mapping[str, str]] = None) -> Tuple[List[types.Tool], Dict[str, str]]:
    """Re-discover the chatflows and atomically replace the catalog.

    Raises:
        DiscoveryError: Flowise could not be reached. The previously installed
            catalog is left untouched - a broken endpoint must never be
            reported as "this server has no tools".
    """
    ctx = context or get_server_context()
    allowed, _index = ctx.directory.refresh()
    return register_tools(list(allowed), descriptions if descriptions is not None else get_chatflow_descriptions())


def _error_result(payload: Mapping[str, Any]) -> types.ServerResult:
    """Build an ``isError`` tool result carrying a sanitized JSON payload."""
    return types.ServerResult(
        root=types.CallToolResult(content=[types.TextContent(type="text", text=json.dumps(payload))], isError=True)
    )


def _error_payload(exc: FlowiseError) -> Dict[str, Any]:
    logger.error("%s: %s", exc.code, exc.message)
    return exc.to_payload()


async def dispatcher_handler(request: types.CallToolRequest) -> types.ServerResult:
    """Route ``tools/call`` to the chatflow currently mapped to the tool name."""
    tool_name = request.params.name
    chatflow_id = catalog.resolve(tool_name)
    if chatflow_id is None:
        # Covers both unknown names and names that were dropped by the last
        # catalog refresh: the mapping is the single source of truth.
        return _error_result(
            {"error": "unknown_tool", "message": f"Tool '{tool_name}' is not part of the current chatflow catalog"}
        )

    arguments = request.params.arguments or {}
    question = arguments.get("question")
    try:
        question = validate_question(question)
    except FlowiseError as exc:
        return _error_result(exc.to_payload())

    context = get_server_context()
    try:
        # Re-authorize against the live catalog: zero prediction POSTs when the
        # target is unknown to Flowise or excluded by the filter policy.
        record = context.directory.authorize(chatflow_id, source=f"tool '{tool_name}'")
        payload = context.client.predict(record.id, question)
    except FlowiseError as exc:
        return _error_result(_error_payload(exc))
    except Exception:  # pragma: no cover - defensive
        logger.error("Unhandled exception while dispatching tool '%s'.", tool_name, exc_info=True)
        return _error_result({"error": "internal_error", "message": "Unexpected internal error"})

    return types.ServerResult(root=types.CallToolResult(content=[types.TextContent(type="text", text=json.dumps(payload))]))


async def list_tools(request: types.ListToolsRequest) -> types.ServerResult:
    """Return the current tool catalog, refreshing it from Flowise first.

    A failed refresh keeps the last known good catalog (and is reported on
    stderr); it never empties the catalog.
    """
    context = get_server_context()
    try:
        refresh_catalog(context)
    except DiscoveryError as exc:
        logger.error("Keeping previous tool catalog: %s", exc.message)
    return types.ServerResult(root=types.ListToolsResult(tools=list(catalog.tools())))


def register_handlers() -> None:
    """Wire the request handlers onto the low-level server."""
    mcp.request_handlers[types.CallToolRequest] = dispatcher_handler
    mcp.request_handlers[types.ListToolsRequest] = list_tools
    logger.debug("Registered low-level request handlers.")


async def start_server() -> None:
    """Serve the MCP protocol over stdio."""
    async with stdio_server() as (read_stream, write_stream):
        await mcp.run(
            read_stream,
            write_stream,
            initialization_options=InitializationOptions(
                server_name=SERVER_NAME,
                server_version=SERVER_VERSION,
                capabilities=types.ServerCapabilities(),
            ),
        )


def run_server(config: Optional[FlowiseConfig] = None, client: Optional[FlowiseClient] = None) -> None:
    """Discover chatflows, install the tool catalog and serve over stdio."""
    context = get_context(config=config, client=client)
    set_server_context(context)
    setup_logging(debug=context.config.debug)
    logger.debug("Starting LowLevel MCP server (endpoint=%s).", context.config.endpoint)

    try:
        refresh_catalog(context)
    except DiscoveryError as exc:
        logger.critical("Cannot start: %s (%s)", exc.message, exc.cause)
        raise SystemExit(1) from None

    tool_count = len(catalog)
    if tool_count == 0:
        # A legitimately empty catalog (no chatflows, or all filtered out) is
        # not a startup failure: the server is up and answers tools/list.
        logger.warning("No chatflow passed the filter policy; starting with an empty tool catalog.")
    else:
        logger.debug("Registered %d tool(s).", tool_count)

    register_handlers()

    try:
        asyncio.run(start_server())
    except KeyboardInterrupt:  # pragma: no cover - interactive
        logger.debug("Server shutdown requested.")
    except Exception as exc:  # pragma: no cover - defensive
        logger.critical("Failed to start MCP server: %s", exc)
        raise SystemExit(1) from None


if __name__ == "__main__":  # pragma: no cover - manual invocation
    run_server()
    sys.exit(0)
