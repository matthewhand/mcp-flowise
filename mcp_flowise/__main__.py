"""
Entry point for the mcp_flowise package.

This script determines which server to run based on the presence of 
the FLOWISE_SIMPLE_MODE environment variable:
- Low-Level Server: For dynamic tool creation based on chatflows.
- FastMCP Server: For static tool configurations.
"""

import os
import sys
from dotenv import load_dotenv
from mcp_flowise.utils import setup_logging
from mcp_flowise.utils import request_timeout, tool_name_prefix, validate_filter_patterns

# Load environment variables from .env if present
load_dotenv()

def main():
    """
    Main entry point for the mcp_flowise package.

    Depending on the FLOWISE_SIMPLE_MODE environment variable, this function
    launches either:
    - Low-Level Server (dynamic tools)
    - FastMCP Server (static tools)
    """
    # Configure logging
    DEBUG = os.getenv("DEBUG", "").lower() in ("true", "1", "yes")
    logger = setup_logging(debug=DEBUG)

    logger.debug("Starting mcp_flowise package entry point.")
    try:
        request_timeout()
        tool_name_prefix()
        validate_filter_patterns()
    except ValueError as error:
        logger.critical("Invalid configuration: %s", error)
        sys.exit(1)

    # Default to Simple Mode unless explicitly disabled
    FLOWISE_SIMPLE_MODE = os.getenv("FLOWISE_SIMPLE_MODE", "true").lower() not in ("false", "0", "no")
    if FLOWISE_SIMPLE_MODE:
        logger.debug("FLOWISE_SIMPLE_MODE is enabled. Launching FastMCP Server.")
        from mcp_flowise.server_fastmcp import run_simple_server
        selected_server = run_simple_server
    else:
        logger.debug("FLOWISE_SIMPLE_MODE is disabled. Launching Low-Level Server.")
        from mcp_flowise.server_lowlevel import run_server
        selected_server = run_server

    # Run the selected server
    try:
        selected_server()
    except Exception as e:
        logger.critical("Unhandled exception occurred while running the server.", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
