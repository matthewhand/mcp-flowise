# mcp-flowise

[![smithery badge](https://smithery.ai/badge/@matthewhand/mcp-flowise)](https://smithery.ai/server/@matthewhand/mcp-flowise)
[![MseeP.ai Security Assessment Badge](https://mseep.net/pr/matthewhand-mcp-flowise-badge.png)](https://mseep.ai/app/matthewhand-mcp-flowise)

`mcp-flowise` lets MCP-enabled assistants discover and run your Flowise chatflows, with authentication and configurable flow filtering.

It supports two operation modes:

- **FastMCP Mode (default)**: Provides `list_chatflows` and `create_prediction` tools.
- **LowLevel Mode** (`FLOWISE_SIMPLE_MODE=false`): Dynamically registers a tool for each visible chatflow.

## Verification status

Core chatflow integration has been verified in both MCP modes. See the [verification report](docs/verification-2026-10-20.md) for the tested version and scope, and the [OpenAPI proxy comparison](docs/codebase-comparison.md) for the follow-up improvements and their verification.

<p align="center">
<img src="https://github.com/user-attachments/assets/d27afb05-c5d3-4cc9-9918-f7be8c715304" alt="Claude Desktop Screenshot">
</p>

---

## Features

- **Dynamic Tool Exposure**: LowLevel mode dynamically creates tools for each visible chatflow. Colliding names receive an ID-derived suffix.
- **Simpler Configuration**: FastMCP mode exposes `list_chatflows` and `create_prediction` tools for minimal setup.
- **Flexible Filtering**: Both modes support filtering chatflows via whitelists and blacklists by IDs or names (regex).
- **Client-friendly Tools**: Readable titles and behavior hints distinguish discovery from predictions that may write history or run agent tools.
- **Responsive Startup**: Dynamic mode discovers flows on first use, so MCP initialization does not wait for Flowise. Failed discovery can be retried by the next request.

---

## Installation

### Installing via Smithery

To install mcp-flowise for Claude Desktop automatically via [Smithery](https://smithery.ai/server/@matthewhand/mcp-flowise):

```bash
npx -y @smithery/cli install @matthewhand/mcp-flowise --client claude
```

### Prerequisites

- Python 3.12 or higher
- `uvx` package manager

### Docker

```bash
docker build -t mcp-flowise:local .
docker run --rm -i --env-file .env mcp-flowise:local
```

The image installs the copied checkout from `uv.lock` and runs as a non-root
user. No Git download occurs at startup. Supply credentials at runtime;
`.env` files are excluded from the build context. This is stdio MCP, so use
`-i` and do not publish a port. Inside Docker, `localhost` is the container:
set `FLOWISE_API_ENDPOINT` to a reachable Flowise address (for a host service
in Docker Desktop, use `host.docker.internal` with its actual service port).
Directory listings such as Glama may require separate account-side setup;
providing a repository Dockerfile does not configure those accounts.

### Install and Run via `uvx`

Confirm you can run the server directly from the GitHub repository using `uvx`:

```bash
uvx --from git+https://github.com/matthewhand/mcp-flowise mcp-flowise
```

### Adding to MCP Ecosystem (`mcpServers` Configuration)

You can integrate `mcp-flowise` into your MCP ecosystem by adding it to the `mcpServers` configuration. Example:

```json
{
    "mcpServers": {
        "mcp-flowise": {
            "command": "uvx",
            "args": [
                "--from",
                "git+https://github.com/matthewhand/mcp-flowise",
                "mcp-flowise"
            ],
            "env": {
                "FLOWISE_API_KEY": "${FLOWISE_API_KEY}",
                "FLOWISE_API_ENDPOINT": "${FLOWISE_API_ENDPOINT}"
            }
        }
    }
}
```

---

## Modes of Operation

### 1. FastMCP Mode (Simple Mode)

Enabled by setting `FLOWISE_SIMPLE_MODE=true`. This mode:
- Exposes two tools: `list_chatflows` and `create_prediction`.
- Allows a default flow using `FLOWISE_CHATFLOW_ID`, or an explicit `chatflow_id` on each call.
- Lists all available chatflows via `list_chatflows`.

<p align="center">
<img src="https://github.com/user-attachments/assets/0901ef9c-5d56-4f1e-a799-1e5d8e8343bd" alt="FastMCP Mode">
</p>

### 2. LowLevel Mode (FLOWISE_SIMPLE_MODE=False)

**Features**:
- Dynamically registers all chatflows as separate tools.
- Tools are named after chatflow names (normalized).
- Uses descriptions from the `FLOWISE_CHATFLOW_DESCRIPTIONS` variable, falling back to chatflow names if no description is provided.

**Example**:
- `my_tool(question: str) -> str` dynamically created for a chatflow.

---
## Running on Windows with `uvx`

If you're using `uvx` on Windows and encounter issues with `--from git+https`, the recommended solution is to clone the repository locally and configure the `mcpServers` with the full path to `uvx.exe` and the cloned repository. Additionally, include `APPDATA`, `LOGLEVEL`, and other environment variables as required.

### Example Configuration for MCP Ecosystem (`mcpServers` on Windows)

```json
{
  "mcpServers": {
    "flowise": {
      "command": "C:\\Users\\matth\\.local\\bin\\uvx.exe",
      "args": [
        "--from",
        "C:\\Users\\matth\\downloads\\mcp-flowise",
        "mcp-flowise"
      ],
      "env": {
        "LOGLEVEL": "ERROR",
        "APPDATA": "C:\\Users\\matth\\AppData\\Roaming",
        "FLOWISE_API_KEY": "your-api-key-goes-here",
        "FLOWISE_API_ENDPOINT": "http://localhost:3000/"
      }
    }
  }
}
```

### Notes

- **Full Paths**: Use full paths for both `uvx.exe` and the cloned repository.
- **Environment Variables**: Point `APPDATA` to your Windows user profile (e.g., `C:\\Users\\<username>\\AppData\\Roaming`) if needed.
- **Log Level**: Adjust `LOGLEVEL` as needed (`ERROR`, `INFO`, `DEBUG`, etc.).

## Environment Variables

### General

- `FLOWISE_API_KEY`: Your Flowise API Bearer token (**required**).
- `FLOWISE_API_ENDPOINT`: Base URL for Flowise (default: `http://localhost:3000`).
- `FLOWISE_HTTP_TIMEOUT`: Finite positive HTTP timeout in seconds (default: `30`). Predictions are never automatically retried.
- `MCP_SERVER_NAME`: Name shown to MCP clients (default: `mcp-flowise`), useful when connecting several instances.
- `TOOL_NAME_PREFIX`: Optional prefix for tool names in both modes. Letters, digits, underscores and hyphens only.
- `TOOL_NAME_MAX_LENGTH`: Tool-name length limit, including the prefix (default: `64`, range: `16`–`64`). Leave at least ten characters after the prefix. Changing these settings changes the names clients call.
- `FLOWISE_LOGFILE_PATH`: Optional rotating log file. Diagnostics otherwise use stderr; no log file is created by default. Files rotate at 5 MiB, retaining two backups.

### LowLevel Mode (`FLOWISE_SIMPLE_MODE=false`)

- `FLOWISE_CHATFLOW_DESCRIPTIONS`: Comma-separated list of `chatflow_id:description` pairs. Example:
  ```
  FLOWISE_CHATFLOW_DESCRIPTIONS="abc123:Chatflow One,xyz789:Chatflow Two"
  ```

### FastMCP Mode (`FLOWISE_SIMPLE_MODE=true`)

- `FLOWISE_CHATFLOW_ID`: Single Chatflow ID (optional).
- `FLOWISE_ASSISTANT_ID`: Unsupported legacy setting; use a chatflow ID.

---

## Filtering Chatflows

Filters can be applied in both modes using the following environment variables:

- **Whitelist by ID**:  
  `FLOWISE_WHITELIST_ID="id1,id2,id3"`
- **Blacklist by ID**:  
  `FLOWISE_BLACKLIST_ID="id4,id5"`
- **Whitelist by Name (Regex)**:  
  `FLOWISE_WHITELIST_NAME_REGEX=".*important.*"`
- **Blacklist by Name (Regex)**:  
  `FLOWISE_BLACKLIST_NAME_REGEX=".*deprecated.*"`

Whitelist IDs and name patterns are combined with OR. If either whitelist is configured, matching flows are included and the primary blacklist is ignored (existing behavior). Without a whitelist, either blacklist excludes a flow. Comma-separated IDs are trimmed. Legacy `FLOWISE_CHATFLOW_WHITELIST` and `FLOWISE_CHATFLOW_BLACKLIST` are additional restrictions in both modes. The same filters are enforced during prediction, including explicit IDs.

-
## Security

- **Protect Your API Key**: Ensure the `FLOWISE_API_KEY` is kept secure and not exposed in logs or repositories.
- **Environment Configuration**: Use `.env` files or environment variables for sensitive configurations.
- **API permissions**: Flowise 3 API keys need permission to view chatflows, plus access to protected prediction flows. A UI login password is not an API key. Keep the service on a trusted network.
- **Protocol and errors**: Diagnostic logs use stderr or the configured log file. Authentication, HTTP failures, and invalid calls produce MCP tool errors instead of successful-looking responses. Prompts and generated answers are not written to adapter logs.

Add `.env` to your `.gitignore`:

```bash
# .gitignore
.env
```

---

## Troubleshooting

- **Missing API Key**: Ensure `FLOWISE_API_KEY` is set correctly.
- **Invalid Configuration**: If both `FLOWISE_CHATFLOW_ID` and `FLOWISE_ASSISTANT_ID` are set, the server will refuse to start.
- **Connection Errors**: Verify `FLOWISE_API_ENDPOINT` is reachable.

---

## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.

## Development checks

```bash
uv sync --frozen --group dev
uv run pytest tests/unit tests/integration/test_stdio_protocol.py -q
```

The protocol tests launch real MCP subprocesses against a local HTTP fixture. Live tests are explicitly opt-in and make real model requests:

```bash
# Supply a key and choose a harmless flow on your own Flowise instance.
export FLOWISE_LIVE_TESTS=1
export FLOWISE_API_ENDPOINT=http://localhost:3030
export FLOWISE_API_KEY=your_private_key
export FLOWISE_TEST_CHATFLOW_ID=your_chatflow_id
uv run pytest tests/integration/test_stdio_live.py -q
```

Dynamic tools are a startup snapshot: restart the MCP process after adding, renaming, or changing visibility of flows. Current filters are checked again for each prediction.

## Scope and compatibility

The adapter uses stdio and MCP Python SDK 1.x. It supports non-streaming chatflow predictions. Flowise assistants, streaming predictions and native Streamable HTTP are not implemented. The SDK 2 port in `mcp-openapi-proxy` is a separate migration and has not been copied into this adapter; clients requiring its newer protocol need that migration or a compatible gateway.

Dynamic discovery is cached in memory after a successful fetch, including an empty result. Discovery failures are not cached; the next list/call retries. Predictions recheck visibility against Flowise each time. Flow metadata and authorization are not loaded from a stale disk cache.

## TODO

- [x] Fastmcp mode
- [x] Lowlevel mode
- [x] Filtering
- [x] Claude desktop integration
- [ ] Assistants
