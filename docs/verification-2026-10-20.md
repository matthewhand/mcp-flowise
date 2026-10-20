# Verification — 20 October 2026

## Environment and scope

- Windows MCP client process, Python 3.13.2, MCP Python SDK 1.30.0. The 25 offline checks also pass on the documented minimum Python 3.12.9.
- The installed Linux adapter also passed all 25 offline checks on Python 3.12.8. A Windows client connected to it using stdio over SSH: two static tools in simple mode, 18 tools in dynamic mode, and nonempty predictions from both selected flows in each mode.
- Real Linux Flowise Docker service upgraded from 2.2.2 to **3.1.3**.
- Official image pinned to `flowiseai/flowise:3.1.3@sha256:fa39f54c82ebecabb817cbd2d01fc9fa7bb26d58a7e818c36bb6c2bad44b366a`.
- The staged upgrade used a protected copy before the live deployment was switched.
- All 18 flows, 12 encrypted credentials, three document stores, five assistants and existing chat history were preserved. SQLite integrity checks passed. Preserving assistants does not imply MCP assistant support.

## Evidence

| Check | Result |
|---|---|
| Unit and local HTTP fixture protocol tests | 25 passed |
| Real stdio initialization and tool capabilities | Both modes passed |
| Real tool listing and prediction | Both modes passed against one unprotected flow and one protected flow: four live test executions |
| Generated output | Actual model responses, nonempty text; existing flow personas do not obey an exact connection-test marker |
| Invalid arguments, unknown tools and hidden flow IDs | MCP tool errors |
| Upstream HTTP 503 and discovery HTTP 401 | MCP tool errors; subsequent stdio tool listing remains intact |
| Original API key | Preserved identity and protected-flow bindings; discovery works with `chatflows:view` permission |
| Missing/invalid discovery key | HTTP 401 |
| Missing protected-prediction key | Denied, but upstream Flowise wraps `Unauthorized` as HTTP 500 |
| Administrator authentication | New email login and session access verified |
| Container after cutover | Healthy, zero restarts |

## Repairs

The adapter now keeps diagnostics on stderr, advertises dynamic tools, reports upstream failures rather than returning successful-looking errors, enforces discovery filters on explicit prediction IDs, validates questions/IDs, clears stale mappings on registration and preserves colliding flow names with stable ID-derived suffixes. Prompt and answer logging was removed. The default mode and unsupported assistant behavior are documented accurately. Python metadata now declares 3.12+, and the lock file resolves MCP 1.30.0 within the supported 1.x series. Initial compatibility checks also passed with MCP 1.2.0.

Two deployment defects were found through real inference: the upstream model gateway forwarded context-limit metadata as an unsupported provider parameter, and one simple flow had a 64-token completion limit that produced empty answers with its reasoning model. The gateway was repaired without changing routing/budgets, and only that flow's limit was raised to 1,024. Immediate and delayed predictions passed after the repair.

Legacy file-backed API keys did not automatically populate the Flowise 3 API-key table. The existing key was imported offline into the migrated workspace with its original ID/secret and the minimum discovery permission. Encrypted model credentials retained their existing encryption key.

## Limits and concerns

- All flows were inventoried; only two harmless chatflows were exercised for inference. Agent side effects, retrieval stores, Zep memory, every external credential, and the remaining flows were not validated.
- MCP predictions are non-streaming. Flowise assistants, streaming, cancellation, concurrency and production load were not tested or implemented by this adapter.
- The official **3.1.4 image failed startup** with the SQLite session-store error reported in [upstream issue #6688](https://github.com/FlowiseAI/Flowise/issues/6688), so the working 3.1.3 image was selected. A copied-data migration was tested before production use.
- The 3.1.3 image logs import failures for two legacy ReAct agent nodes (`ReActAgentChat`/`ReActAgentLLM`). The two exercised flows do not use them; those nodes remain unavailable.
- [FlowiseAI/Flowise](https://github.com/FlowiseAI/Flowise) is archived. This deployment check is not a security certification or a claim of ongoing upstream maintenance. Keep it on a trusted network and evaluate maintenance options before broader exposure.
- The deployment host has limited free disk space. Original data, old container, image and protected backups were retained for rollback.

Keys, passwords, private flow definitions, user prompts, endpoints and backups are excluded from this repository.
