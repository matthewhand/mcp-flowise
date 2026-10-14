# mcp-flowise

[![smithery badge](https://smithery.ai/badge/@matthewhand/mcp-flowise)](https://smithery.ai/server/@matthewhand/mcp-flowise)

`mcp-flowise` is a Python package implementing a Model Context Protocol (MCP) server that integrates with
the Flowise API. It provides a standardized way to list chatflows, create predictions, and (optionally)
expose every allowed chatflow as its own MCP tool.

It supports two operation modes:

- **FastMCP mode (default, `simple`)**: two static tools, `list_chatflows` and `create_prediction`.
- **LowLevel mode (`lowlevel`)**: one dynamically generated tool per allowed chatflow.

> **Compatibility status.** Verified against the MCP Python SDK `1.30.0` on Python 3.12 and 3.13 with a
> local mock Flowise server (initialize / `tools/list` / `tools/call` in both modes, container handshake).
> It has **not** been verified against a live Flowise deployment - see
> [Live compatibility gate](#live-compatibility-gate). Assistant (as opposed to chatflow) support is
> **not implemented**; see [Assistants](#assistants-not-implemented).

<p align="center">
<img src="https://github.com/user-attachments/assets/d27afb05-c5d3-4cc9-9918-f7be8c715304" alt="Claude Desktop Screenshot">
</p>

---

## Features

- **Two modes**: a static, network-free startup (FastMCP) or a dynamic tool per chatflow (LowLevel).
- **Filtering in both modes**: whitelists and blacklists by ID and by name (regex), applied both when
  building the tool catalog **and** when a tool is dispatched.
- **Fail-closed dispatch**: a chatflow that the policy does not allow is rejected *before* any request is
  sent to Flowise.
- **Clean stdio**: stdout carries MCP JSON-RPC only; every log record - including errors - goes to stderr.
- **Distinguishable failures**: timeouts, connection failures, non-2xx statuses, malformed JSON and
  discovery failures are reported as separate, sanitized errors instead of being returned as answers.

---

## Requirements

- Python **3.12 or newer** (`pyproject.toml`, `uv.lock`, CI matrix and the Docker image all agree)
- [`uv`](https://docs.astral.sh/uv/) (recommended) or `pip`
- A reachable Flowise API

> The MCP Python SDK is constrained to `>=1.30.0,<2`: SDK 2.x removed `mcp.server.fastmcp`
> (FastMCP was renamed to `MCPServer`) and this project does not run on it.

---

## Installation

### Install and run via `uvx`

```bash
uvx --from git+https://github.com/matthewhand/mcp-flowise mcp-flowise
```

This resolves the project from git on first launch (and on every upgrade), so it needs git and network
access at start-up. To run a pinned checkout instead - which is what the Docker image does - use
`uv sync --frozen` in a clone and run the installed `mcp-flowise` entrypoint.

### Run from a checkout

```bash
uv sync --frozen
uv run --frozen mcp-flowise
```

### Adding to an MCP ecosystem (`mcpServers` configuration)

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

The server speaks **stdio**: it does not listen on a port, and it must not be given one.

### Docker

The image runs the checkout that was copied into it - there is no git download at start-up, and no
credentials are baked in.

```bash
docker build -t mcp-flowise .
docker run --rm -i \
  -e FLOWISE_API_ENDPOINT=http://host.docker.internal:3000 \
  -e FLOWISE_API_KEY="$FLOWISE_API_KEY" \
  mcp-flowise
```

`docker run -i` is required: the container speaks MCP over stdin/stdout. There is no `EXPOSE`d port.

---

## Modes of operation

The mode is resolved from configuration **before** the server starts, and no chatflow discovery happens
before that:

| Variable | Effect |
|---|---|
| `FLOWISE_MODE=simple\|lowlevel` | Explicit selection (wins over everything else) |
| `FLOWISE_SIMPLE_MODE=true\|false` | `true` -> simple mode (default), `false` -> lowlevel mode |
| *(neither set)* | **simple mode** |

`simple` is the default because it starts instantly and works even when Flowise is temporarily
unreachable; a dynamic server cannot advertise tools it has never discovered.

### 1. FastMCP mode (default, `FLOWISE_SIMPLE_MODE=true`)

Two static tools:

- `list_chatflows()` - the chatflows the filter policy allows, as a JSON array of `{"id", "name"}`.
- `create_prediction(question, chatflow_id=None)` - asks one chatflow. `chatflow_id` defaults to
  `FLOWISE_CHATFLOW_ID`. Every field of Flowise's JSON answer is returned unchanged.

No discovery happens at start-up. Flowise is contacted when `list_chatflows` is called, when a target
has to be authorized against a filter policy, or when a prediction is requested.

<p align="center">
<img src="https://github.com/user-attachments/assets/0901ef9c-5d56-4f1e-a799-1e5d8e8343bd" alt="FastMCP Mode">
</p>

### 2. LowLevel mode (`FLOWISE_SIMPLE_MODE=false`)

- One tool per allowed chatflow, named after the chatflow name, normalized to
  lowercase with non-alphanumerics replaced by `_` (`"Alpha Chatflow"` -> `alpha_chatflow`).
- Descriptions come from `FLOWISE_CHATFLOW_DESCRIPTIONS` (`id:description` pairs), falling back to the
  chatflow name.
- Each tool takes a single `question` argument.
- Names that collide after normalization are skipped (the first chatflow wins) and the collision is
  reported on stderr.
- The catalog is discovered at start-up and refreshed on every `tools/list`. The tool list and the
  name -> id mapping are always replaced together, so a tool whose chatflow disappeared from Flowise
  stops being callable instead of keeping its old target.
- If a refresh fails (Flowise down, HTTP error, ...), the **last known catalog is kept**; the failure is
  reported on stderr. A legitimately empty catalog is not an error: the server answers `tools/list` with
  an empty list.

---

## Environment Variables

### General

| Variable | Default | Description |
|---|---|---|
| `FLOWISE_API_ENDPOINT` | `http://localhost:3000` | Base URL of Flowise. Must be `http(s)://host[:port][/base]`, without query string or fragment. |
| `FLOWISE_API_KEY` | *(empty)* | Bearer token. Optional for a local, unauthenticated Flowise. Never logged. |
| `FLOWISE_API_TIMEOUT` | `30` | Per-request timeout in seconds (0 < t <= 600). |
| `DEBUG` | `false` | `DEBUG=true` adds debug records to the stderr log. |
| `FLOWISE_LOGFILE_PATH` | `~/mcp_logs/debug-mcp-flowise.log` | Optional log file; logs always go to stderr as well. |

### Targets

| Variable | Mode | Description |
|---|---|---|
| `FLOWISE_CHATFLOW_ID` | simple | Default chatflow for `create_prediction`. Trimmed; must match `[A-Za-z0-9._-]+`. |
| `FLOWISE_ASSISTANT_ID` | - | **Rejected at start-up**: assistant support is not implemented. |
| `FLOWISE_CHATFLOW_DESCRIPTIONS` | lowlevel | `id:description` pairs used as tool descriptions. |

Setting both `FLOWISE_CHATFLOW_ID` and `FLOWISE_ASSISTANT_ID` is a configuration error (conflicting
targets) and the server refuses to start with exit code 1.

### Filtering chatflows (both modes)

| Variable | Effect |
|---|---|
| `FLOWISE_WHITELIST_ID` | Comma-separated chatflow IDs to allow |
| `FLOWISE_BLACKLIST_ID` | Comma-separated chatflow IDs to deny |
| `FLOWISE_WHITELIST_NAME_REGEX` | Regular expression; matching chatflow names are allowed |
| `FLOWISE_BLACKLIST_NAME_REGEX` | Regular expression; matching chatflow names are denied |

**Exact precedence rules:**

1. If **any whitelist rule is set** (by ID or by name regex), a chatflow is exposed **only** if it
   matches a whitelist rule. Blacklists are then irrelevant: nothing outside the whitelist is exposed,
   and whitelisted chatflows are exposed even if a blacklist would have matched them.
2. If **no whitelist rule is set**, the blacklists apply: a chatflow matched by
   `FLOWISE_BLACKLIST_ID` or by `FLOWISE_BLACKLIST_NAME_REGEX` is hidden; everything else is exposed.
3. With **no rules at all**, every chatflow is exposed.

The same policy is applied in three places, so there is no way around it: when building the catalog,
when `create_prediction` resolves its target (explicit `chatflow_id` *and* `FLOWISE_CHATFLOW_ID`), and
again at dispatch time in lowlevel mode. When a policy is active, an unknown or excluded target is
rejected with `policy_denied` and **zero** prediction requests are sent.

**Legacy names.** `FLOWISE_CHATFLOW_WHITELIST` and `FLOWISE_CHATFLOW_BLACKLIST` (used by earlier
FastMCP-only builds) are still read, but **only when the corresponding canonical variable is unset**.
They are never merged into it: a leftover legacy variable cannot re-admit IDs the canonical variable
excluded. Ignored legacy variables are reported on stderr at start-up.

---

## Error reporting

Tool failures are returned as MCP tool errors (`isError: true`) whose text is a small JSON object, so a
failure can never be mistaken for an answer:

```json
{"error": "http_error", "message": "Flowise API returned HTTP 401", "status_code": 401}
```

| `error` | Meaning |
|---|---|
| `config_error` | Bad request arguments or contradictory configuration (raised before any request) |
| `policy_denied` | The chatflow is unknown to Flowise or excluded by the filter policy (no request sent) |
| `timeout` | The request exceeded `FLOWISE_API_TIMEOUT` |
| `connection_failed` | Flowise could not be reached |
| `http_error` | Non-2xx status; `status_code` is included |
| `malformed_response` | The response was not the expected JSON |
| `discovery_failed` | Chatflow listing failed; `cause` names the underlying error (`http_error`, `timeout`, ...) |
| `unknown_tool` | The tool is not part of the current catalog (e.g. it was removed by a refresh) |

Upstream response bodies, API keys and questions are never included in these payloads or in the logs.

---

## Assistants (not implemented)

Earlier versions accepted `FLOWISE_ASSISTANT_ID` and sent it to the *chatflow* prediction endpoint, which
silently addressed the wrong resource. Assistant support is now refused explicitly: setting
`FLOWISE_ASSISTANT_ID` stops the server with a configuration error. It remains a TODO and will stay
unimplemented until it is implemented *and* tested against a real Flowise deployment.

---

## Security notes

- **stdout is the MCP transport.** Log records - including `ERROR`/`CRITICAL` - are written to stderr (and
  optionally to `FLOWISE_LOGFILE_PATH`). API keys, questions and upstream bodies are never logged.
- **Least privilege**: use `FLOWISE_API_KEY` scoped to what the server needs, and keep `.env` out of
  version control (`.env` is already in `.gitignore`).
- **Filtering is enforced server-side**, not by prompting the model.

---

## Troubleshooting

- **`configuration error` on start-up** - check `FLOWISE_API_ENDPOINT`, the mode variables, and that at
  most one of `FLOWISE_CHATFLOW_ID` / `FLOWISE_ASSISTANT_ID` is set. The message names the variable.
- **`Cannot start: Could not retrieve the chatflow catalog`** - lowlevel mode needs Flowise to be
  reachable at start-up. Simple mode does not; use it if Flowise may be down when the client launches.
- **`policy_denied`** - the target is not in the effective whitelist / is blacklisted / does not exist.
  Run with `DEBUG=true` to see the policy summary on stderr.
- **No tools in lowlevel mode** - either Flowise reports no chatflows, or the filter policy excluded all
  of them; both are legitimate. Discovery *failures* are reported as errors, never as an empty catalog.
- **Running on Windows with `uvx`** - if `--from git+https` fails, clone the repository and point the
  configuration at the full path of your `uvx.exe` and the clone (see `tests/README.md` for the CI-style
  alternative).

---

## Development

```bash
uv sync --frozen          # install exactly what uv.lock pins
uv run --frozen pytest tests -q
```

The test suite is offline: it starts a mock Flowise server (`tests/mock_flowise.py`) and drives the real
servers over real stdio MCP sessions. See [`tests/README.md`](tests/README.md).

---

## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.

## TODO

- [x] FastMCP (simple) mode
- [x] LowLevel (dynamic) mode
- [x] Whitelist/blacklist filtering enforced at dispatch
- [x] Claude Desktop integration
- [ ] Assistants (`FLOWISE_ASSISTANT_ID`) - explicitly refused today
- [ ] Live Flowise compatibility gate
