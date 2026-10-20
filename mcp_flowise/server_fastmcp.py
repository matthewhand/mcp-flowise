"""
Provides the FastMCP server logic for mcp_flowise.

This server exposes a limited set of tools (list_chatflows, create_prediction)
and uses environment variables to determine the chatflow or assistant configuration.
"""

import os
import sys
import json
from mcp.server.fastmcp import FastMCP
from mcp_flowise.utils import flowise_predict, fetch_chatflows, redact_api_key, setup_logging

# Environment variables
FLOWISE_API_KEY = os.getenv("FLOWISE_API_KEY", "")
FLOWISE_API_ENDPOINT = os.getenv("FLOWISE_API_ENDPOINT", "http://localhost:3000")
FLOWISE_CHATFLOW_ID = os.getenv("FLOWISE_CHATFLOW_ID")
FLOWISE_ASSISTANT_ID = os.getenv("FLOWISE_ASSISTANT_ID")
FLOWISE_CHATFLOW_DESCRIPTION = os.getenv("FLOWISE_CHATFLOW_DESCRIPTION")
FLOWISE_CHATFLOW_WHITELIST = os.getenv("FLOWISE_CHATFLOW_WHITELIST")
FLOWISE_CHATFLOW_BLACKLIST = os.getenv("FLOWISE_CHATFLOW_BLACKLIST")

DEBUG = os.getenv("DEBUG", "").lower() in ("true", "1", "yes")

# Configure logging
logger = setup_logging(debug=DEBUG)

# Log key environment variable values
logger.debug(f"Flowise API Key (redacted): {redact_api_key(FLOWISE_API_KEY)}")
logger.debug(f"Flowise API Endpoint: {FLOWISE_API_ENDPOINT}")
logger.debug(f"Flowise Chatflow ID: {FLOWISE_CHATFLOW_ID}")
logger.debug(f"Flowise Assistant ID: {FLOWISE_ASSISTANT_ID}")
logger.debug(f"Flowise Chatflow Description: {FLOWISE_CHATFLOW_DESCRIPTION}")

# Initialize MCP Server
mcp = FastMCP("FlowiseMCP-with-EnvAuth")


@mcp.tool()
def list_chatflows() -> str:
    """
    List all available chatflows from the Flowise API.

    This function respects optional whitelisting or blacklisting if configured
    via FLOWISE_CHATFLOW_WHITELIST or FLOWISE_CHATFLOW_BLACKLIST.

    Returns:
        str: A JSON-encoded string of filtered chatflows.
    """
    logger.debug("Handling list_chatflows tool.")
    chatflows = fetch_chatflows()

    logger.debug(f"Filtered chatflows: {chatflows}")
    return json.dumps(chatflows)


@mcp.tool()
def create_prediction(*, chatflow_id: str = None, question: str) -> str:
    """
    Create a non-streaming prediction by sending a question to an available chatflow.

    Args:
        chatflow_id (str, optional): The ID of the chatflow to use. Defaults to FLOWISE_CHATFLOW_ID.
        question (str): The question or prompt to send to the chatflow.

    Returns:
        str: The raw JSON response from Flowise API or an error message if something goes wrong.
    """
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
