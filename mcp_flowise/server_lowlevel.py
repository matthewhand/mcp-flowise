'''
Low-Level Server for the Flowise MCP.

This server dynamically registers tools based on the provided chatflows
retrieved from the Flowise API. Tool names are normalized for safety 
and consistency, and potential conflicts are logged.

Descriptions for tools are prioritized from FLOWISE_CHATFLOW_DESCRIPTIONS,
falling back to the chatflow names when not provided.

Conflicts in tool names after normalization are handled gracefully by
skipping those chatflows.
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
    flowise_predict,
    fetch_chatflows,
    normalize_tool_name,
    setup_logging,
)

# Configure logging
DEBUG = os.getenv("DEBUG", "").lower() in ("true", "1", "yes")
logger = setup_logging(debug=DEBUG)

# Global tool mapping: tool name to chatflow ID
NAME_TO_ID_MAPPING: Dict[str, str] = {}
tools: List[types.Tool] = []

# Initialize the Low-Level MCP Server
mcp = Server("FlowiseMCP-with-EnvAuth")


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

    logger.debug("Retrieved FLOWISE_CHATFLOW_DESCRIPTIONS: %s", descriptions_env)
    descriptions = {}
    for pair in descriptions_env.split(","):
        if ":" not in pair:
            logger.warning("Invalid format in FLOWISE_CHATFLOW_DESCRIPTIONS: %s", pair)
            continue
        chatflow_id, description = map(str.strip, pair.split(":", 1))
        if chatflow_id and description:
            descriptions[chatflow_id] = description
    logger.debug("Parsed FLOWISE_CHATFLOW_DESCRIPTIONS: %s", descriptions)
    return descriptions


async def dispatcher_handler(request: types.CallToolRequest) -> types.ServerResult:
    """
    Dispatcher handler that routes CallToolRequest to the appropriate tool handler based on the tool name.
    """
    try:
        tool_name = request.params.name
        logger.debug("Dispatcher received CallToolRequest for tool: %s", tool_name)

        if tool_name not in NAME_TO_ID_MAPPING:
            logger.error("Unknown tool requested: %s", tool_name)
            return types.ServerResult(
                root=types.CallToolResult(
                    content=[types.TextContent(type="text", text="Unknown tool requested")], isError=True
                )
            )

        chatflow_id = NAME_TO_ID_MAPPING[tool_name]
        question = (request.params.arguments or {}).get("question", "")
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
        except Exception as pred_err:
            logger.error("Error during prediction: %s", type(pred_err).__name__)
            return types.ServerResult(root=types.CallToolResult(
                content=[types.TextContent(type="text", text=str(pred_err))], isError=True))

        # Pass the raw JSON response or error JSON back to the client
        return types.ServerResult(
            root=types.CallToolResult(
                content=[types.TextContent(type="text", text=result)]
            )
        )
    except Exception as e:
        logger.error("Unhandled exception in dispatcher_handler: %s", e, exc_info=True)
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
    logger.debug("Handling list_tools request.")
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
    global tools
    tools = []  # Clear existing tools before re-registration
    NAME_TO_ID_MAPPING.clear()
    for chatflow in chatflows:
        try:
            normalized_name = normalize_tool_name(chatflow["name"])

            if normalized_name in NAME_TO_ID_MAPPING:
                import hashlib
                suffix = hashlib.sha256(chatflow["id"].encode()).hexdigest()[:12]
                normalized_name = normalized_name[:51] + "_" + suffix

            NAME_TO_ID_MAPPING[normalized_name] = chatflow["id"]
            description = chatflow_descriptions.get(chatflow["id"], chatflow["name"])

            tool = types.Tool(
                name=normalized_name,
                description=description,
                inputSchema={
                    "type": "object",
                    "required": ["question"],
                    "properties": {"question": {"type": "string"}},
                },
            )
            tools.append(tool)
            logger.debug("Registered tool: %s (ID: %s)", tool.name, chatflow["id"])

        except Exception as e:
            logger.error("Error registering chatflow '%s' (ID: '%s'): %s", chatflow["name"], chatflow["id"], e)

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
                    server_name="FlowiseMCP-with-EnvAuth",
                    server_version="0.1.1",
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
    try:
        chatflows = fetch_chatflows()
        if not chatflows:
            raise ValueError("No chatflows retrieved from the Flowise API.")
    except Exception as e:
        logger.critical("Failed to start server: %s", e)
        sys.exit(1)

    chatflow_descriptions = get_chatflow_descriptions()
    register_tools(chatflows, chatflow_descriptions)

    if not tools:
        logger.critical("No valid tools registered. Shutting down the server.")
        sys.exit(1)

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
