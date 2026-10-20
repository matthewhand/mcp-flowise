# Lessons from mcp-openapi-proxy

Compared on 20 October 2026:

- `mcp-openapi-proxy` public main: [`c37256c`](https://github.com/matthewhand/mcp-openapi-proxy/tree/c37256c), package 0.4.0.
- `mcp-flowise` starting point: [`1e46419`](https://github.com/matthewhand/mcp-flowise/tree/1e46419), package 0.1.1.

The OpenAPI proxy grew out of the Flowise adapter. Their two-mode layout and normalized tool discovery still show that relationship. Public OpenAPI main has since acquired a separate protocol layer, a shared OpenAPI operation model, an SDK 2 migration, native HTTP transport, client annotations and lazy discovery. An older experimental checkout does not represent that published code.

## Improvements applied in this patch

| Lesson from the proxy | Flowise adaptation |
|---|---|
| `ensure_spec_loaded` defers network I/O and uses a lock | Dynamic mode now initializes MCP before contacting Flowise. First list/call performs discovery in a worker thread, with concurrent discovery serialized. Failed discovery retries on a later request. |
| `protocol.py` owns server identity and version | Both modes use `MCP_SERVER_NAME`; the advertised version comes from installed package metadata. SDK-specific compatibility access stays localized. |
| Tool annotations help clients distinguish reads and writes | Listing is marked read-only/idempotent. Predictions are explicitly not read-only or idempotent, because flows write history and can invoke external tools. No attempt to infer an agent's safety from its name. |
| Prefixes, length limits and deduplication help multi-server clients | Both modes support `TOOL_NAME_PREFIX` and bounded `TOOL_NAME_MAX_LENGTH`. Dynamic registrations sort by ID, preserve conflicting names through ID suffixes, check suffix collisions and commit the new registry together. |
| The tool schema is a contract | Dynamic tools reject unknown argument names and describe a nonempty question. FastMCP continues to generate the static tool schemas. |
| Diagnostics belong outside the protocol | Method/tool identity can be logged without arguments or answers. Stderr is the default; optional file output is bounded and rotates. No raw-command or prompt logging was copied. |
| Configuration needs a declared contract | HTTP timeout is configurable and validated. The minimum SDK is now 1.30.0, matching the annotation API in use; the `<2` boundary remains explicit. |

Default static tool names remain `list_chatflows` and `create_prediction`. Existing short, non-conflicting dynamic names also remain unchanged. Colliding dynamic names may be reassigned between flows because their ordering is now deterministic; clients should refresh tool discovery. Custom prefix/length settings deliberately change callable names.

## Features that need a separate migration

The proxy's [0.4 migration](https://github.com/matthewhand/mcp-openapi-proxy/blob/c37256c/docs/MIGRATION-0.4.md) changes SDK imports, handler signatures, model field names, capability registration and HTTP behavior. Copying its dependency requirement alone would break the current Flowise adapter. SDK 2 and native HTTP are not enabled by this patch. A coordinated migration needs both-mode protocol/client checks and an explicit HTTP authentication design; the Flowise API key protects the upstream connection, not a new MCP HTTP listener.

Run the two packages in separate `uvx`/virtual environments: their current SDK-major requirements differ.

OpenAPI parsing, schema resources, generic API authentication, shell-command audit logs and operation-specific header/body handling are not needed for the Flowise chatflow interface. They would add dependencies and configuration unrelated to this adapter's job.

The proxy's disk fallback for API descriptions is not used for Flowise visibility: stale discovery must not authorize a flow that has since been removed or excluded. Each prediction keeps its fresh visibility check. Failed discovery also is not cached permanently. No automatic prediction retry is introduced, because a flow may already have produced side effects before a timeout.

## Review and verification status

The 0.1.2 follow-up passes **46 unit/local protocol checks** on Windows Python 3.12.9, including the added discovery, empty inventory, recovery, concurrent discovery, naming, timeout, logging, annotation, invalid-regex, startup-error and revoked-visibility regressions. Actual MCP subprocess initialization makes zero Flowise requests. Both modes advertise the configured identity and appropriate tool annotations.

The protected/unprotected live matrix also passed: four test executions with actual predictions against the existing Flowise service, covering both modes, filtering and invalid calls. A separate Linux Python 3.12.8 checkout passed the same 46 offline checks and four live test executions before rollout. The [earlier verification report](verification-2026-10-20.md) remains the baseline record for 0.1.1.
