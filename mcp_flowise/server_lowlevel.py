'''
Low-Level Server for the Flowise MCP.

This server dynamically registers tools based on the provided chatflows
retrieved from the Flowise API. Tool names are normalized for safety 
and consistency, and potential conflicts are logged.

Descriptions for tools are prioritized from FLOWISE_CHATFLOW_DESCRIPTIONS,
falling back to the chatflow names when not provided.

Conflicting names remain callable through ID-derived suffixes.
'''

import os
import sys
import asyncio
import json
from typing import List, Dict, Any
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.models import InitializationOptions
from mcp.server.stdio import stdio_server
from mcp_flowise.utils import (
    FlowiseAPIError,
    flowise_predict,
    fetch_chatflows,
    normalize_tool_name,
    setup_logging,
    tool_name_prefix,
    unique_tool_name,
)
from mcp_flowise.protocol import PACKAGE_VERSION, advertised_server_name, prediction_annotations

# Configure logging
DEBUG = os.getenv("DEBUG", "").lower() in ("true", "1", "yes")
logger = setup_logging(debug=DEBUG)

# Global tool mapping: tool name to chatflow ID
NAME_TO_ID_MAPPING: Dict[str, str] = {}
tools: List[types.Tool] = []

# Initialize the Low-Level MCP Server
mcp = Server(advertised_server_name(), version=PACKAGE_VERSION)
_tools_loaded = False
_tools_load_lock = asyncio.Lock()


async def ensure_tools_loaded() -> None:
    """Delay Flowise I/O until discovery or a call; retry failed discovery on next request."""
    if _tools_loaded:
        return
    async with _tools_load_lock:
        if _tools_loaded:
            return
        chatflows = await asyncio.to_thread(fetch_chatflows)
        register_tools(chatflows, get_chatflow_descriptions())


def get_chatflow_descriptions() -> Dict[str, str]:
    """
    Parse the FLOWISE_CHATFLOW_DESCRIPTIONS environment variable for descriptions.

    Returns:
        dict: A dictionary mapping chatflow IDs to descriptions.
    """
    descriptions_env = os.getenv("FLOWISE_CHATFLOW_DESCRIPTIONS", "")
    if not descriptions_env:
        logger.debug("No FLOWISE_CHATFLOW_DESCRIPTIONS provided.")
        return {}

    descriptions = {}
    for pair in descriptions_env.split(","):
        if ":" not in pair:
            logger.warning("Ignoring a malformed FLOWISE_CHATFLOW_DESCRIPTIONS entry")
            continue
        chatflow_id, description = map(str.strip, pair.split(":", 1))
        if chatflow_id and description:
            descriptions[chatflow_id] = description
    logger.debug("Loaded %d custom chatflow descriptions", len(descriptions))
    return descriptions


async def dispatcher_handler(request: types.CallToolRequest) -> types.ServerResult:
    """
    Dispatcher handler that routes CallToolRequest to the appropriate tool handler based on the tool name.
    """
    try:
        tool_name = request.params.name
        await ensure_tools_loaded()

        if tool_name not in NAME_TO_ID_MAPPING:
            logger.error("Unknown tool requested")
            return types.ServerResult(
                root=types.CallToolResult(
                    content=[types.TextContent(type="text", text="Unknown tool requested")], isError=True
                )
            )

        chatflow_id = NAME_TO_ID_MAPPING[tool_name]
        logger.info("mcp rpc method=tools/call name=%s", tool_name)
        arguments = request.params.arguments or {}
        if set(arguments) - {"question"}:
            return types.ServerResult(root=types.CallToolResult(
                content=[types.TextContent(type="text", text="Only the 'question' argument is supported")], isError=True))
        question = arguments.get("question", "")
        if not isinstance(question, str) or not question.strip():
            logger.error("Missing 'question' argument for tool: %s", tool_name)
            return types.ServerResult(
                root=types.CallToolResult(
                    content=[types.TextContent(type="text", text="A non-empty 'question' argument is required.")], isError=True
                )
            )

        logger.debug("Dispatching prediction for chatflow_id: %s", chatflow_id)

        # Call the prediction function
        try:
            result = await asyncio.to_thread(flowise_predict, chatflow_id, question)
        except (FlowiseAPIError, ValueError) as pred_err:
            logger.error("Error during prediction: %s", type(pred_err).__name__)
            return types.ServerResult(root=types.CallToolResult(
                content=[types.TextContent(type="text", text=str(pred_err))], isError=True))

        # Pass the raw JSON response or error JSON back to the client
        return types.ServerResult(
            root=types.CallToolResult(
                content=[types.TextContent(type="text", text=result)]
            )
        )
    except FlowiseAPIError as e:
        return types.ServerResult(root=types.CallToolResult(
            content=[types.TextContent(type="text", text=str(e))], isError=True))
    except Exception:
        logger.error("Unexpected prediction failure")
        return types.ServerResult(
            root=types.CallToolResult(
                content=[types.TextContent(type="text", text=json.dumps({"error": "Internal server error."}))], isError=True
            )
        )


async def list_tools(request: types.ListToolsRequest) -> types.ServerResult:
    """
    Handler for ListToolsRequest to list all registered tools.

    Args:
        request (types.ListToolsRequest): The request to list tools.

    Returns:
        types.ServerResult: The result containing the list of tools.
    """
    logger.info("mcp rpc method=tools/list")
    await ensure_tools_loaded()
    return types.ServerResult(root=types.ListToolsResult(tools=tools))


def register_tools(chatflows: List[Dict[str, Any]], chatflow_descriptions: Dict[str, str]) -> List[types.Tool]:
    """
    Register tools dynamically based on the provided chatflows.

    Args:
        chatflows (List[Dict[str, Any]]): List of chatflows retrieved from the Flowise API.
        chatflow_descriptions (Dict[str, str]): Dictionary mapping chatflow IDs to descriptions.

    Returns:
        List[types.Tool]: List of registered tools.
    """
    global tools, _tools_loaded
    tool_name_prefix()  # Validate configuration before changing any registration.
    new_tools = []
    new_mapping = {}
    registered_ids = set()
    # Stable ordering makes collision names independent of API result order.
    for chatflow in sorted(chatflows, key=lambda flow: flow["id"]):
        try:
            if chatflow["id"] in registered_ids:
                continue
            normalized_name = normalize_tool_name(chatflow["name"])
            normalized_name = unique_tool_name(normalized_name, chatflow["id"], set(new_mapping))
            description = chatflow_descriptions.get(chatflow["id"], chatflow["name"])

            tool = types.Tool(
                name=normalized_name,
                description=description,
                inputSchema={
                    "type": "object",
                    "required": ["question"],
                    "properties": {"question": {"type": "string", "minLength": 1}},
                    "additionalProperties": False,
                },
                annotations=prediction_annotations(chatflow["name"]),
            )
            new_tools.append(tool)
            new_mapping[normalized_name] = chatflow["id"]
            registered_ids.add(chatflow["id"])
            logger.debug("Registered tool: %s (ID: %s)", tool.name, chatflow["id"])

        except (TypeError, KeyError):
            logger.error("Skipping malformed chatflow metadata")
    tools = new_tools
    NAME_TO_ID_MAPPING.clear()
    NAME_TO_ID_MAPPING.update(new_mapping)
    _tools_loaded = True
    return tools


async def start_server():
    """
    Start the Low-Level MCP server.
    """
    logger.debug("Starting Low-Level MCP server...")
    try:
        async with stdio_server() as (read_stream, write_stream):
            await mcp.run(
                read_stream,
                write_stream,
                initialization_options=InitializationOptions(
                    server_name=advertised_server_name(),
                    server_version=PACKAGE_VERSION,
                    capabilities=types.ServerCapabilities(tools=types.ToolsCapability(listChanged=False)),
                ),
            )
    except Exception as e:
        logger.critical("Unhandled exception in MCP server: %s", e)
        sys.exit(1)


def run_server():
    """
    Run the Low-Level Flowise server by registering tools dynamically.
    """
    # No network request here: initialize must not depend on Flowise availability.

    mcp.request_handlers[types.CallToolRequest] = dispatcher_handler
    logger.debug("Registered dispatcher_handler for CallToolRequest.")

    mcp.request_handlers[types.ListToolsRequest] = list_tools
    logger.debug("Registered list_tools handler.")

    try:
        asyncio.run(start_server())
    except KeyboardInterrupt:
        logger.debug("MCP server shutdown initiated by user.")
    except Exception as e:
        logger.critical("Failed to start MCP server: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    run_server()
