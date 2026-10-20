"""Shared client-facing metadata, independent of Flowise API transport."""

from importlib.metadata import PackageNotFoundError, version
import os

from mcp import types

try:
    PACKAGE_VERSION = version("mcp-flowise")
except PackageNotFoundError:
    PACKAGE_VERSION = "0+unknown"


def advertised_server_name() -> str:
    return os.getenv("MCP_SERVER_NAME", "").strip() or "mcp-flowise"


def list_annotations() -> types.ToolAnnotations:
    return types.ToolAnnotations(
        title="List Flowise chatflows", readOnlyHint=True,
        destructiveHint=False, idempotentHint=True, openWorldHint=True,
    )


def prediction_annotations(title: str = "Run a Flowise chatflow") -> types.ToolAnnotations:
    # Flows write conversation history and may run agent tools. Their effects
    # cannot be inferred from their names or a POST method alone.
    return types.ToolAnnotations(
        title=title, readOnlyHint=False, destructiveHint=True,
        idempotentHint=False, openWorldHint=True,
    )
