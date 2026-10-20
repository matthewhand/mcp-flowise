"""
Utility functions for mcp_flowise, including logging setup, chatflow filtering, and Flowise API interactions.

This module centralizes shared functionality such as:
1. Logging configuration for consistent log output across the application.
2. Safe redaction of sensitive data like API keys in logs.
3. Low-level interactions with the Flowise API for predictions and chatflow management.
4. Flexible filtering of chatflows based on whitelist/blacklist criteria.
"""

import os
import sys
import logging
from logging.handlers import RotatingFileHandler
import hashlib
import math
import requests
import re
from dotenv import load_dotenv

# Load environment variables from .env if present
load_dotenv()

# Flowise API configuration
FLOWISE_API_KEY = os.getenv("FLOWISE_API_KEY", "")
FLOWISE_API_ENDPOINT = os.getenv("FLOWISE_API_ENDPOINT", "http://localhost:3000")


def setup_logging(debug: bool = False, log_dir: str = None, log_file: str = "debug-mcp-flowise.log") -> logging.Logger:
    """
    Sets up logging. Standard output is reserved for the MCP protocol.

    Args:
        debug (bool): If True, set log level to DEBUG; otherwise, INFO.
        log_dir (str): Directory where log files will be stored. Ignored if `FLOWISE_LOGFILE_PATH` is set.
        log_file (str): Name of the log file. Ignored if `FLOWISE_LOGFILE_PATH` is set.

    Returns:
        logging.Logger: Configured logger instance.
    """
    log_path = os.getenv("FLOWISE_LOGFILE_PATH")
    if not log_path and log_dir is not None:
        log_path = os.path.join(log_dir, log_file)
    logger = logging.getLogger(__name__)
    logger.setLevel(logging.DEBUG if debug else logging.INFO)
    logger.propagate = False
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
        handler.close()
    formatter = logging.Formatter("[%(levelname)s] %(asctime)s - %(message)s")
    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setFormatter(formatter)
    logger.addHandler(stderr_handler)
    if log_path:
        try:
            os.makedirs(os.path.dirname(os.path.abspath(log_path)), exist_ok=True)
            file_handler = RotatingFileHandler(log_path, maxBytes=5 * 1024 * 1024, backupCount=2, encoding="utf-8")
            file_handler.setLevel(logging.DEBUG if debug else logging.INFO)
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)
        except OSError:
            logger.warning("Could not open configured log file; diagnostics use stderr")
    return logger


def redact_api_key(key: str) -> str:
    """
    Redacts the Flowise API key for safe logging output.

    Args:
        key (str): The API key to redact.

    Returns:
        str: The redacted API key or '<not set>' if the key is invalid.
    """
    if not key or len(key) <= 4:
        return "<not set>"
    return f"{key[:2]}{'*' * (len(key) - 4)}{key[-2:]}"


def normalize_tool_name(name: str) -> str:
    """
    Normalize tool names by converting to lowercase and replacing non-alphanumeric characters with underscores.

    Args:
        name (str): Original tool name.

    Returns:
        str: Normalized tool name. Returns 'unknown_tool' if the input is invalid.
    """
    logger = logging.getLogger(__name__)
    if not name or not isinstance(name, str):
        logger.warning("Invalid tool name; using unknown_tool")
        name = "unknown_tool"
    normalized = re.sub(r"[^a-zA-Z0-9]", "_", name).lower()
    logger.debug("Normalized tool name: %s", normalized)
    prefix = tool_name_prefix()
    return (prefix + (normalized or "unknown_tool"))[:tool_name_limit()]


def tool_name_prefix() -> str:
    prefix = os.getenv("TOOL_NAME_PREFIX", "")
    if not re.fullmatch(r"[A-Za-z0-9_-]*", prefix):
        raise ValueError("TOOL_NAME_PREFIX must contain only letters, digits, underscores or hyphens")
    if len(prefix) > tool_name_limit() - 10:
        raise ValueError("TOOL_NAME_PREFIX must leave at least 10 characters for the tool name")
    return prefix


def tool_name_limit() -> int:
    raw = os.getenv("TOOL_NAME_MAX_LENGTH", "64")
    try:
        limit = int(raw)
    except ValueError:
        raise ValueError("TOOL_NAME_MAX_LENGTH must be an integer from 16 to 64") from None
    if not 16 <= limit <= 64:
        raise ValueError("TOOL_NAME_MAX_LENGTH must be an integer from 16 to 64")
    return limit


def unique_tool_name(name: str, flow_id: str, registered_names: set[str]) -> str:
    """Keep all colliding flows callable, including collisions with a generated suffix."""
    if name not in registered_names:
        return name
    limit = tool_name_limit()
    digest = hashlib.sha256(flow_id.encode()).hexdigest()
    counter = 1
    while True:
        tail = "" if counter == 1 else f"_{counter}"
        digest_length = min(12, limit - len(tool_name_prefix()) - len(tail) - 2)
        if digest_length < 4:
            raise ValueError("Too many conflicting tool names for the configured name limit")
        suffix = "_" + digest[:digest_length] + tail
        candidate = name[:limit - len(suffix)] + suffix
        if candidate not in registered_names:
            return candidate
        counter += 1


def request_timeout() -> float:
    try:
        timeout = float(os.getenv("FLOWISE_HTTP_TIMEOUT", "30"))
    except ValueError:
        raise ValueError("FLOWISE_HTTP_TIMEOUT must be a finite positive number of seconds") from None
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("FLOWISE_HTTP_TIMEOUT must be a finite positive number of seconds")
    return timeout


def filter_chatflows(chatflows: list[dict]) -> list[dict]:
    """
    Filters chatflows based on whitelist and blacklist criteria.
    Whitelist takes precedence over blacklist.

    Args:
        chatflows (list[dict]): A list of chatflow dictionaries.

    Returns:
        list[dict]: Filtered list of chatflows.
    """
    logger = logging.getLogger(__name__)

    # Dynamically fetch filtering criteria
    whitelist_ids = {value.strip() for value in os.getenv("FLOWISE_WHITELIST_ID", "").split(",") if value.strip()}
    blacklist_ids = {value.strip() for value in os.getenv("FLOWISE_BLACKLIST_ID", "").split(",") if value.strip()}
    whitelist_name_regex = os.getenv("FLOWISE_WHITELIST_NAME_REGEX", "")
    blacklist_name_regex = os.getenv("FLOWISE_BLACKLIST_NAME_REGEX", "")

    filtered_chatflows = []

    for chatflow in chatflows:
        chatflow_id = chatflow.get("id", "")
        chatflow_name = chatflow.get("name", "")

        # Flags to determine inclusion
        is_whitelisted = False

        # Check Whitelist
        if whitelist_ids or whitelist_name_regex:
            if whitelist_ids and chatflow_id in whitelist_ids:
                is_whitelisted = True
            if whitelist_name_regex and re.search(whitelist_name_regex, chatflow_name):
                is_whitelisted = True

            if is_whitelisted:
                # If whitelisted, include regardless of blacklist
                logger.debug("Including whitelisted chatflow '%s' (ID: '%s').", chatflow_name, chatflow_id)
                filtered_chatflows.append(chatflow)
                continue  # Skip blacklist checks
            else:
                # If not whitelisted, exclude regardless of blacklist
                logger.debug("Excluding non-whitelisted chatflow '%s' (ID: '%s').", chatflow_name, chatflow_id)
                continue
        else:
            # If no whitelist, apply blacklist directly
            if blacklist_ids and chatflow_id in blacklist_ids:
                logger.debug("Skipping chatflow '%s' (ID: '%s') - In blacklist.", chatflow_name, chatflow_id)
                continue  # Exclude blacklisted by ID
            if blacklist_name_regex and re.search(blacklist_name_regex, chatflow_name):
                logger.debug("Skipping chatflow '%s' (ID: '%s') - Name matches blacklist regex.", chatflow_name, chatflow_id)
                continue  # Exclude blacklisted by name

            # Include the chatflow if it passes all filters
            logger.debug("Including chatflow '%s' (ID: '%s').", chatflow_name, chatflow_id)
            filtered_chatflows.append(chatflow)

    logger.debug("Filtered chatflows: %d out of %d", len(filtered_chatflows), len(chatflows))
    legacy_allow = {value.strip() for value in os.getenv("FLOWISE_CHATFLOW_WHITELIST", "").split(",") if value.strip()}
    legacy_deny = {value.strip() for value in os.getenv("FLOWISE_CHATFLOW_BLACKLIST", "").split(",") if value.strip()}
    return [cf for cf in filtered_chatflows if (not legacy_allow or cf["id"] in legacy_allow) and cf["id"] not in legacy_deny]

class FlowiseAPIError(RuntimeError):
    """A safe error message suitable for an MCP tool response."""


def flowise_predict(chatflow_id: str, question: str) -> str:
    """
    Sends a question to a specific chatflow ID via the Flowise API and returns the response JSON text.

    Args:
        chatflow_id (str): The ID of the Flowise chatflow to be used.
        question (str): The question or prompt to send to the chatflow.

    Returns:
        str: The raw response text from the Flowise API.

    Raises:
        FlowiseAPIError: Discovery or prediction failed.
        ValueError: Arguments are invalid or the flow is excluded.
    """
    logger = logging.getLogger(__name__)
    if not isinstance(chatflow_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", chatflow_id):
        raise ValueError("A valid chatflow_id is required")
    if not isinstance(question, str) or not question.strip():
        raise ValueError("A non-empty question is required")
    # Apply the same visibility rules to calls as to discovery.
    if chatflow_id not in {flow["id"] for flow in fetch_chatflows()}:
        raise ValueError("Chatflow is unavailable or excluded by the configured filters")

    # Construct the Flowise API URL for predictions
    url = f"{FLOWISE_API_ENDPOINT.rstrip('/')}/api/v1/prediction/{chatflow_id}"
    headers = {
        "Content-Type": "application/json",
    }
    if FLOWISE_API_KEY:
        headers["Authorization"] = f"Bearer {FLOWISE_API_KEY}"

    payload = {"question": question}
    logger.debug("Sending prediction request")

    try:
        # Send POST request to the Flowise API
        response = requests.post(url, json=payload, headers=headers, timeout=request_timeout())
        logger.debug(f"Prediction response code: HTTP {response.status_code}")
        response.raise_for_status()

        # Return the raw JSON response text
        return response.text

    except requests.RequestException as e:
        status = e.response.status_code if e.response is not None else None
        detail = f"HTTP {status}" if status else type(e).__name__
        logger.error("Flowise prediction failed (%s)", detail)
        raise FlowiseAPIError(f"Flowise prediction failed ({detail})") from None


def fetch_chatflows() -> list[dict]:
    """
    Fetch a list of all chatflows from the Flowise API.

    Returns:
        list of dict: Each dict contains the 'id' and 'name' of a chatflow.
                      Raises FlowiseAPIError on HTTP or malformed-response errors.
    """
    logger = logging.getLogger(__name__)

    # Construct the Flowise API URL for fetching chatflows
    url = f"{FLOWISE_API_ENDPOINT.rstrip('/')}/api/v1/chatflows"
    headers = {}
    if FLOWISE_API_KEY:
        headers["Authorization"] = f"Bearer {FLOWISE_API_KEY}"

    logger.debug("Fetching chatflow inventory")

    try:
        # Send GET request to the Flowise API
        response = requests.get(url, headers=headers, timeout=request_timeout())
        response.raise_for_status()

        # Parse and simplify the response data
        chatflows_data = response.json()
        if not isinstance(chatflows_data, list) or any(
            not isinstance(cf, dict) or not isinstance(cf.get("id"), str)
            or not cf["id"] or not isinstance(cf.get("name"), str)
            for cf in chatflows_data
        ):
            raise ValueError("Malformed chatflow inventory")
        simplified_chatflows = [{"id": cf["id"], "name": cf["name"]} for cf in chatflows_data]

        logger.debug("Fetched %d chatflows", len(simplified_chatflows))
        return filter_chatflows(simplified_chatflows)
    except (requests.RequestException, ValueError, TypeError, KeyError) as e:
        status = e.response.status_code if isinstance(e, requests.RequestException) and e.response is not None else None
        detail = f"HTTP {status}" if status else type(e).__name__
        logger.error("Flowise chatflow discovery failed (%s)", detail)
        raise FlowiseAPIError(f"Flowise chatflow discovery failed ({detail})") from None


# Set up logging before obtaining the logger
DEBUG = os.getenv("DEBUG", "").lower() in ("true", "1", "yes")
logger = setup_logging(debug=DEBUG)
