# Testing `mcp-flowise`

Everything in this suite runs **offline**: `tests/mock_flowise.py` starts a task-owned HTTP double of the
Flowise API on localhost, and the tests drive the real servers through a real MCP stdio session
(`mcp.client.stdio` + `mcp.ClientSession`). A mock run is *not* proof of live Flowise compatibility.

## Layout

| Path | What it covers |
|---|---|
| `unit/test_config.py` | Endpoint/mode/target/regex/timeout validation, filter policy semantics, legacy env names |
| `unit/test_utils.py` | stderr-only logging, handler closing, HTTP error taxonomy, authorization, catalog swaps |
| `unit/test_chatflow_filters.py` | `filter_chatflows` against the configured policy |
| `integration/test_simple_mode_stdio.py` | simple mode: initialize / `tools/list` / `tools/call`, authorization, error taxonomy |
| `integration/test_lowlevel_mode_stdio.py` | lowlevel mode: dynamic tools, atomic refresh, stale tools, argument validation |
| `integration/test_stdout_purity.py` | every stdout line is JSON-RPC (incl. error paths), no secrets/questions/bodies in logs, start-up failures |
| `integration/test_container_handshake.py` | the built Docker image performs a real MCP handshake and call (opt-in) |
| `integration/test_flowise_integration.py`, `integration/test_tool_prediction.py` | **live gate**, skipped unless real Flowise credentials are provided |
| `mock_flowise.py` | the Flowise HTTP double (records every request) |
| `fake_http.py` | fake `requests.Session` for unit tests |

## Running

```bash
uv sync --frozen
uv run --frozen pytest tests -q
```

Unit tests only (fast):

```bash
uv run --frozen pytest tests/unit -q
```

## Container handshake (opt-in)

```bash
docker build -t mcp-flowise:test .
MCP_FLOWISE_DOCKER_IMAGE=mcp-flowise:test uv run --frozen pytest tests/integration/test_container_handshake.py -q
```

The container reaches the mock through `host.docker.internal`; override with
`MCP_FLOWISE_DOCKER_HOST_BRIDGE` if your Docker setup differs.

## Live compatibility gate

These tests are **skipped** in CI. To run them against a real Flowise deployment:

```bash
FLOWISE_API_ENDPOINT=http://host:3000 FLOWISE_API_KEY=<token> \
  uv run --frozen pytest tests/integration/test_flowise_integration.py tests/integration/test_tool_prediction.py
```

Add `FLOWISE_CHATFLOW_ID=<id>` to also exercise the prediction path.

## What is *not* proven here

* That any specific Flowise version answers `/api/v1/chatflows` and `/api/v1/prediction/{id}` with the
  payload shape assumed here (that is the live gate above).
* Assistant support - it is refused at start-up and has no test beyond that refusal.
