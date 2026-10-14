"""Entry point for mcp-flowise.

The mode is resolved from configuration *before* anything else: no chatflow
discovery is performed here, so a misconfigured endpoint or an invalid filter
fails fast with a sanitized message on stderr instead of a stray network call
(and, in simple mode, instead of blocking startup on an unreachable Flowise).

Exit codes:
    0 - the server ran and shut down cleanly
    1 - invalid configuration, or the server could not start
"""

from __future__ import annotations

import sys

from mcp_flowise.config import MODE_LOWLEVEL, MODE_SIMPLE, load_config
from mcp_flowise.errors import ConfigError
from mcp_flowise.utils import get_logger, setup_logging


def main() -> int:
    """Load configuration and run the selected server."""
    try:
        config = load_config()
    except ConfigError as exc:
        # stderr keeps stdout free for the MCP JSON-RPC stream.
        print(f"[mcp-flowise] configuration error: {exc.message}", file=sys.stderr)
        return 1

    setup_logging(debug=config.debug)
    logger = get_logger("main")
    logger.debug("Starting mcp-flowise (mode=%s, endpoint=%s).", config.mode, config.endpoint)

    try:
        if config.mode == MODE_LOWLEVEL:
            from mcp_flowise.server_lowlevel import run_server

            run_server(config)
        elif config.mode == MODE_SIMPLE:
            from mcp_flowise.server_fastmcp import run_simple_server

            run_simple_server(config)
        else:  # pragma: no cover - guarded by config validation
            raise ConfigError(f"Unsupported mode '{config.mode}'")
    except SystemExit as exc:
        return int(exc.code or 0)
    except ConfigError as exc:
        print(f"[mcp-flowise] configuration error: {exc.message}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:  # pragma: no cover - interactive
        logger.debug("Interrupted.")
        return 0
    except Exception:
        logger.critical("Unhandled exception while running the server.", exc_info=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
