# Testing `mcp-flowise`

Install the locked development environment, then run the offline suite:

```bash
uv sync --frozen --group dev
uv run --frozen pytest -q
```

Unit checks and real MCP subprocess sessions against a local HTTP fixture run
without external credentials. Live tests are skipped by default, even when an
existing `.env` contains credentials. Collection is restricted to `tests/`.

For real inference, explicitly set `FLOWISE_LIVE_TESTS=1`,
`FLOWISE_TEST_CHATFLOW_ID` to a harmless chosen flow, `FLOWISE_API_ENDPOINT`, and
`FLOWISE_API_KEY`. Then run:

```bash
uv run --frozen pytest tests/integration/test_stdio_live.py -q
```

This exercises both MCP modes against the same chosen flow. The two legacy HTTP
smoke-test modules share the explicit opt-in and never select a random flow.
The old self-mocking discovery test and import-time smoke scripts were removed;
the real stdio protocol tests cover those paths.

