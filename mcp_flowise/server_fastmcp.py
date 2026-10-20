"""
Provides the FastMCP server logic for mcp_flowise.

This server exposes a limited set of tools (list_chatflows, create_prediction)
and uses environment variables to determine the chatflow or assistant configuration.
"""

import os
import sys
import json
from mcp.server.fastmcp import FastMCP
from mcp_flowise.utils import flowise_predict, fetch_chatflows, normalize_tool_name, setup_logging
from mcp_flowise.protocol import PACKAGE_VERSION, advertised_server_name, list_annotations, prediction_annotations

# Environment variables
FLOWISE_CHATFLOW_ID = os.getenv("FLOWISE_CHATFLOW_ID")
FLOWISE_ASSISTANT_ID = os.getenv("FLOWISE_ASSISTANT_ID")
FLOWISE_CHATFLOW_DESCRIPTION = os.getenv("FLOWISE_CHATFLOW_DESCRIPTION")

DEBUG = os.getenv("DEBUG", "").lower() in ("true", "1", "yes")

# Configure logging
logger = setup_logging(debug=DEBUG)

# Initialize MCP Server
mcp = FastMCP(advertised_server_name())
# SDK 1.x FastMCP has no version constructor argument. Keep this compatibility
# access in one place until a coordinated SDK 2 migration removes it.
mcp._mcp_server.version = PACKAGE_VERSION


@mcp.tool(name=normalize_tool_name("list_chatflows"), annotations=list_annotations())
def list_chatflows() -> str:
    """
    List all available chatflows from the Flowise API.

    This function respects optional whitelisting or blacklisting if configured
    via FLOWISE_CHATFLOW_WHITELIST or FLOWISE_CHATFLOW_BLACKLIST.

    Returns:
        str: A JSON-encoded string of filtered chatflows.
    """
    logger.info("mcp rpc method=tools/call name=%s", normalize_tool_name("list_chatflows"))
    chatflows = fetch_chatflows()

    logger.debug(f"Filtered chatflows: {chatflows}")
    return json.dumps(chatflows)


@mcp.tool(name=normalize_tool_name("create_prediction"),
          description=FLOWISE_CHATFLOW_DESCRIPTION or None,
          annotations=prediction_annotations())
def create_prediction(*, chatflow_id: str = None, question: str) -> str:
    """
    Create a non-streaming prediction by sending a question to an available chatflow.

    Args:
        chatflow_id (str, optional): The ID of the chatflow to use. Defaults to FLOWISE_CHATFLOW_ID.
        question (str): The question or prompt to send to the chatflow.

    Returns:
        str: The raw JSON response from Flowise API or an error message if something goes wrong.
    """
    logger.info("mcp rpc method=tools/call name=%s", normalize_tool_name("create_prediction"))
    chatflow_id = chatflow_id or FLOWISE_CHATFLOW_ID

    if not chatflow_id and not FLOWISE_ASSISTANT_ID:
        logger.error("No chatflow_id or assistant_id provided or pre-configured.")
        raise ValueError("chatflow_id is required")

    if not chatflow_id:
        raise ValueError("FLOWISE_ASSISTANT_ID is unsupported; configure a chatflow ID")
    return flowise_predict(chatflow_id, question)

def run_simple_server():
    """
    Run the FastMCP version of the Flowise server.

    This function ensures proper configuration and handles server initialization.

    Raises:
        SystemExit: If both FLOWISE_CHATFLOW_ID and FLOWISE_ASSISTANT_ID are set simultaneously.
    """
    if FLOWISE_CHATFLOW_ID and FLOWISE_ASSISTANT_ID:
        logger.error("Both FLOWISE_CHATFLOW_ID and FLOWISE_ASSISTANT_ID are set. Set only one.")
        sys.exit(1)

    try:
        logger.debug("Starting MCP server (FastMCP version)...")
        mcp.run(transport="stdio")
    except Exception as e:
        logger.error("Unhandled exception in MCP server.", exc_info=True)
        sys.exit(1)
