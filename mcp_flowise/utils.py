"""Logging, chatflow discovery and the Flowise HTTP client for mcp-flowise.

Design rules enforced here:

* **stdout belongs to the MCP stdio transport.** Every diagnostic - including
  ERROR/CRITICAL records - is written to stderr (or to an explicit log file).
* **No secrets in logs.** API keys are never logged, questions are never
  logged, and upstream request/response bodies are never logged.
* **Errors are typed and sanitized.** Timeouts, connection failures, non-2xx
  statuses, malformed JSON and discovery failures are distinct exceptions;
  upstream bodies are not relayed to clients.
"""

from __future__ import annotations

import logging
import os
import re
import sys
import threading
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple
from urllib.parse import quote, urlsplit, urlunsplit

import requests

from mcp_flowise.config import (
    FilterPolicy,
    FlowiseConfig,
    load_config,
    normalize_id,
    validate_question,
)
from mcp_flowise.errors import (
    ConfigError,
    DiscoveryError,
    FlowiseConnectionError,
    FlowiseError,
    FlowiseHttpError,
    FlowiseTimeoutError,
    MalformedResponseError,
    PolicyDeniedError,
)

__all__ = [
    "LOGGER_NAME",
    "ChatflowDirectory",
    "ChatflowRecord",
    "FlowiseClient",
    "RuntimeContext",
    "ToolCatalog",
    "filter_chatflows",
    "flowise_predict",
    "fetch_chatflows",
    "get_context",
    "get_logger",
    "normalize_tool_name",
    "redact_api_key",
    "reset_context",
    "set_context",
    "setup_logging",
]

LOGGER_NAME = "mcp_flowise"
DEFAULT_LOG_FILE = "debug-mcp-flowise.log"


class _StderrHandler(logging.StreamHandler):
    """Stream handler that always writes to the *current* ``sys.stderr``.

    ``sys.stdout`` is reserved for the MCP JSON-RPC stream and must never
    receive log records, so the stream is resolved at emit time instead of at
    handler construction time.
    """

    def __init__(self) -> None:
        super().__init__(stream=sys.stderr)

    @property  # type: ignore[override]
    def stream(self):
        return sys.stderr

    @stream.setter
    def stream(self, value) -> None:  # noqa: D401 - trivial
        # Ignore assignments so the handler cannot be pointed at stdout.
        return None


def setup_logging(
    debug: bool = False,
    log_dir: Optional[str] = None,
    log_file: str = DEFAULT_LOG_FILE,
) -> logging.Logger:
    """Configure stderr/file logging for the whole package.

    Handlers replaced by a previous call are closed, so repeated setup (tests,
    re-entrant entry points) does not leak file descriptors.

    Args:
        debug: Emit DEBUG records as well as INFO.
        log_dir: Directory for the log file. Ignored when
            ``FLOWISE_LOGFILE_PATH`` is set.
        log_file: Log file name. Ignored when ``FLOWISE_LOGFILE_PATH`` is set.

    Returns:
        The configured ``mcp_flowise`` logger.
    """
    level = logging.DEBUG if debug else logging.INFO

    log_path = os.getenv("FLOWISE_LOGFILE_PATH")
    if not log_path:
        if log_dir is None:
            log_dir = os.path.join(os.path.expanduser("~"), "mcp_logs")
        try:
            os.makedirs(log_dir, exist_ok=True)
            log_path = os.path.join(log_dir, log_file)
        except OSError as exc:
            print(f"[mcp-flowise] Log directory unavailable, logging to stderr only: {exc}", file=sys.stderr)
            log_path = None

    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(level)
    logger.propagate = False

    replaced = list(logger.handlers)
    for handler in replaced:
        logger.removeHandler(handler)
        try:
            handler.close()
        except Exception:  # pragma: no cover - defensive
            pass

    formatter = logging.Formatter("[%(levelname)s] %(asctime)s %(name)s - %(message)s")

    if log_path:
        try:
            file_handler = logging.FileHandler(log_path, mode="a", encoding="utf-8")
            file_handler.setLevel(level)
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)
        except OSError as exc:
            print(f"[mcp-flowise] Log file unavailable, logging to stderr only: {exc}", file=sys.stderr)

    stderr_handler = _StderrHandler()
    stderr_handler.setLevel(level)
    stderr_handler.setFormatter(formatter)
    logger.addHandler(stderr_handler)

    logger.debug("Logging initialised (stderr%s).", " + file" if log_path else "")
    return logger


def get_logger(name: Optional[str] = None) -> logging.Logger:
    """Return the package logger (or one of its children)."""
    if not name or name == LOGGER_NAME:
        return logging.getLogger(LOGGER_NAME)
    if name.startswith(f"{LOGGER_NAME}."):
        return logging.getLogger(name)
    return logging.getLogger(f"{LOGGER_NAME}.{name}")


def redact_api_key(key: Optional[str]) -> str:
    """Return a non-reversible representation of an API key."""
    if not key or len(key) <= 4:
        return "<not set>"
    return f"{key[:2]}{'*' * (len(key) - 4)}{key[-2:]}"


def safe_url(url: str) -> str:
    """Strip credentials, query string and fragment from a URL for logging."""
    try:
        parts = urlsplit(url)
    except ValueError:  # pragma: no cover - defensive
        return "<unparsable url>"
    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"
    return urlunsplit((parts.scheme, host, parts.path, "", ""))


def normalize_tool_name(name: Any) -> str:
    """Normalize a chatflow name into a tool name (lowercase + underscores)."""
    logger = get_logger("tools")
    if not name or not isinstance(name, str):
        logger.warning("Invalid tool name input: %s. Using default 'unknown_tool'.", type(name).__name__)
        return "unknown_tool"
    normalized = re.sub(r"[^a-zA-Z0-9]", "_", name).lower()
    if not normalized:
        logger.warning("Chatflow name normalized to an empty tool name; using 'unknown_tool'.")
        return "unknown_tool"
    return normalized


class FlowiseClient:
    """Thin, typed HTTP client for the Flowise API."""

    def __init__(self, config: FlowiseConfig, session: Optional[requests.Session] = None) -> None:
        self._config = config
        self._session = session or requests.Session()
        self._logger = get_logger("client")

    @property
    def config(self) -> FlowiseConfig:
        return self._config

    def _send(self, method: str, url: str, *, json_body: Optional[Mapping[str, Any]] = None):
        """Perform an HTTP request and translate transport/status failures."""
        headers = self._config.auth_headers() if json_body is not None else self._config.read_headers()
        self._logger.debug("%s %s", method, safe_url(url))
        try:
            response = self._session.request(
                method,
                url,
                json=json_body,
                headers=headers,
                timeout=self._config.timeout,
            )
        except requests.Timeout:
            raise FlowiseTimeoutError(f"Flowise API timed out after {self._config.timeout:g}s") from None
        except requests.ConnectionError:
            raise FlowiseConnectionError(f"Could not connect to Flowise API at {self._config.endpoint}") from None
        except requests.RequestException as exc:
            raise FlowiseConnectionError(
                f"Flowise API request failed ({type(exc).__name__}) for {self._config.endpoint}"
            ) from None

        status = response.status_code
        if not 200 <= status < 300:
            # The upstream body is intentionally discarded: Flowise returns HTML
            # error pages and JSON error payloads that must not reach clients.
            self._logger.warning("Flowise API returned HTTP %s for %s", status, safe_url(url))
            raise FlowiseHttpError(f"Flowise API returned HTTP {status}", status_code=status)
        return response

    @staticmethod
    def _decode_json(response: Any) -> Any:
        try:
            return response.json()
        except ValueError:
            content_type = (response.headers.get("Content-Type") or "").split(";")[0].strip()
            raise MalformedResponseError(
                f"Flowise API returned a non-JSON response (content-type: {content_type or 'unknown'})"
            ) from None

    def list_chatflows(self) -> List[Dict[str, str]]:
        """Return every chatflow exposed by the API (``id``/``name`` only)."""
        url = f"{self._config.endpoint}/api/v1/chatflows"
        response = self._send("GET", url)
        payload = self._decode_json(response)
        if not isinstance(payload, list):
            raise MalformedResponseError("Flowise API returned a chatflow catalog that is not a JSON array")

        chatflows: List[Dict[str, str]] = []
        for entry in payload:
            if not isinstance(entry, dict):
                self._logger.warning("Skipping non-object entry in chatflow catalog")
                continue
            raw_id = entry.get("id")
            if raw_id is None or (isinstance(raw_id, str) and not raw_id.strip()):
                self._logger.warning("Skipping chatflow catalog entry without an id")
                continue
            chatflows.append({"id": str(raw_id).strip(), "name": str(entry.get("name") or "")})
        return chatflows

    def predict(self, chatflow_id: str, question: str) -> Dict[str, Any]:
        """POST a question to ``chatflow_id`` and return the decoded JSON body.

        Successful payloads are returned unchanged so that every field Flowise
        provides is preserved for the MCP client.
        """
        url = f"{self._config.endpoint}/api/v1/prediction/{quote(chatflow_id, safe='')}"
        response = self._send("POST", url, json_body={"question": question})
        payload = self._decode_json(response)
        if not isinstance(payload, dict):
            raise MalformedResponseError("Flowise API returned a prediction result that is not a JSON object")
        return payload


@dataclass(frozen=True)
class ChatflowRecord:
    """A chatflow as known to the server."""

    id: str
    name: str

    def as_dict(self) -> Dict[str, str]:
        return {"id": self.id, "name": self.name}


class ChatflowDirectory:
    """Cached chatflow catalog with policy-aware authorization.

    The directory performs *discovery* (``GET /api/v1/chatflows``) lazily: only
    when the configured policy needs the catalog to authorize a target. This is
    what keeps FastMCP mode from touching the network at startup unless a
    filter actually requires catalog authorization.
    """

    def __init__(self, client: FlowiseClient, policy: FilterPolicy) -> None:
        self._client = client
        self._policy = policy
        self._logger = get_logger("directory")
        self._lock = threading.Lock()
        self._records: Optional[Tuple[Tuple[ChatflowRecord, ...], Dict[str, str]]] = None

    @property
    def policy(self) -> FilterPolicy:
        return self._policy

    @property
    def loaded(self) -> bool:
        with self._lock:
            return self._records is not None

    def _store(self, allowed: Sequence[ChatflowRecord], index: Mapping[str, str]) -> Tuple[Tuple[ChatflowRecord, ...], Dict[str, str]]:
        snapshot = (tuple(allowed), dict(index))
        with self._lock:
            self._records = snapshot
        return snapshot

    def refresh(self) -> Tuple[Tuple[ChatflowRecord, ...], Dict[str, str]]:
        """Re-read the catalog from Flowise and atomically replace the cache.

        Raises:
            DiscoveryError: the catalog could not be retrieved. Callers must
                never treat this as "the catalog is now empty".
        """
        try:
            chatflows = self._client.list_chatflows()
        except FlowiseError as exc:
            self._logger.error("Chatflow discovery failed (%s): %s", exc.code, exc.message)
            raise DiscoveryError(
                f"Could not retrieve the chatflow catalog from {self._client.config.endpoint}",
                cause=exc.code,
            ) from None

        index: Dict[str, str] = {}
        records: List[ChatflowRecord] = []
        for chatflow in chatflows:
            index[chatflow["id"]] = chatflow["name"]
            if self._policy.allows(chatflow["id"], chatflow["name"]):
                records.append(ChatflowRecord(id=chatflow["id"], name=chatflow["name"]))
            else:
                self._logger.debug("Chatflow '%s' excluded by filter policy.", chatflow["id"])
        snapshot = self._store(records, index)
        self._logger.debug(
            "Chatflow catalog refreshed: %d exposed of %d known.", len(records), len(index)
        )
        return snapshot

    def snapshot(self) -> Tuple[Tuple[ChatflowRecord, ...], Dict[str, str]]:
        """Return the cached snapshot, discovering once if needed."""
        with self._lock:
            cached = self._records
        if cached is None:
            return self.refresh()
        return cached

    def records(self) -> Tuple[ChatflowRecord, ...]:
        return self.snapshot()[0]

    def lookup(self, chatflow_id: str) -> Optional[ChatflowRecord]:
        """Return the chatflow record for ``chatflow_id`` (refresh on miss)."""
        with self._lock:
            cached = self._records
        if cached is not None and chatflow_id in cached[1]:
            return ChatflowRecord(id=chatflow_id, name=cached[1][chatflow_id])
        allowed, index = self.refresh()
        del allowed
        name = index.get(chatflow_id)
        if name is None:
            return None
        return ChatflowRecord(id=chatflow_id, name=name)

    def authorize(self, chatflow_id: str, *, source: str = "tool call") -> ChatflowRecord:
        """Authorize a prediction target, failing closed.

        When no filter is configured the target is accepted as-is (no network
        access). Otherwise the target must be known to Flowise *and* allowed by
        the policy. Any failure raises before a prediction request is sent.
        """
        try:
            identifier = normalize_id(chatflow_id, field_name="chatflow_id")
        except ConfigError as exc:
            raise PolicyDeniedError(f"Invalid chatflow id rejected by {source}: {exc.message}") from None
        if not identifier:
            raise PolicyDeniedError(f"No chatflow id provided for {source}")

        if not self._policy.enabled:
            return ChatflowRecord(id=identifier, name="")

        record = self.lookup(identifier)
        if record is None:
            raise PolicyDeniedError(
                f"Chatflow '{identifier}' is unknown to Flowise and cannot be authorized by the filter policy"
            )
        if not self._policy.allows(record.id, record.name):
            raise PolicyDeniedError(
                f"Chatflow '{identifier}' is not permitted by the configured filter policy"
            )
        return record


class ToolCatalog:
    """Atomically swapped tool list + name -> chatflow ID mapping.

    Tools and the mapping are always replaced together, so a refreshed catalog
    can never leave stale, still-callable tool names behind.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tools: Tuple[Any, ...] = ()
        self._mapping: Dict[str, str] = {}

    def replace(self, tools: Sequence[Any], mapping: Mapping[str, str]) -> Tuple[Any, ...]:
        with self._lock:
            self._tools = tuple(tools)
            self._mapping = dict(mapping)
            return self._tools

    def tools(self) -> Tuple[Any, ...]:
        with self._lock:
            return self._tools

    def resolve(self, tool_name: str) -> Optional[str]:
        with self._lock:
            return self._mapping.get(tool_name)

    def mapping(self) -> Dict[str, str]:
        with self._lock:
            return dict(self._mapping)

    def __len__(self) -> int:
        with self._lock:
            return len(self._tools)


@dataclass
class RuntimeContext:
    """Everything the servers need to talk to Flowise under one policy."""

    config: FlowiseConfig
    client: FlowiseClient
    directory: ChatflowDirectory


_context_lock = threading.Lock()
_context: Optional[RuntimeContext] = None


def get_context(config: Optional[FlowiseConfig] = None, client: Optional[FlowiseClient] = None) -> RuntimeContext:
    """Return the process-wide runtime context, creating it on first use."""
    global _context
    with _context_lock:
        if config is not None or client is not None:
            _context = _build_context(config or load_config(), client)
        elif _context is None:
            _context = _build_context(load_config(), None)
        return _context


def _build_context(config: FlowiseConfig, client: Optional[FlowiseClient]) -> RuntimeContext:
    resolved_client = client or FlowiseClient(config)
    return RuntimeContext(
        config=config,
        client=resolved_client,
        directory=ChatflowDirectory(resolved_client, config.policy),
    )


def set_context(context: Optional[RuntimeContext]) -> None:
    """Install (or clear) the process-wide runtime context."""
    global _context
    with _context_lock:
        _context = context


def reset_context() -> None:
    """Drop the cached runtime context (used by tests)."""
    set_context(None)


def filter_chatflows(chatflows: Iterable[Mapping[str, Any]], policy: Optional[FilterPolicy] = None) -> List[Dict[str, str]]:
    """Apply the configured whitelist/blacklist policy to ``chatflows``."""
    active = policy if policy is not None else load_config().policy
    return active.filter_chatflows(chatflows)


def fetch_chatflows(policy: Optional[FilterPolicy] = None, client: Optional[FlowiseClient] = None) -> List[Dict[str, str]]:
    """Discover and filter chatflows.

    Raises:
        DiscoveryError: when Flowise cannot be reached or returns garbage.
            An empty list is returned **only** for a genuinely empty catalog.
    """
    context = get_context(client=client)
    directory = context.directory if policy is None else ChatflowDirectory(context.client, policy)
    return [record.as_dict() for record in directory.records()]


def flowise_predict(chatflow_id: str, question: str, config: Optional[FlowiseConfig] = None) -> Dict[str, Any]:
    """Authorize and send a prediction, returning the decoded JSON payload.

    Raises:
        ConfigError: the question is invalid.
        PolicyDeniedError: the target is not permitted (no request is sent).
        FlowiseTimeoutError / FlowiseConnectionError / FlowiseHttpError /
        MalformedResponseError: transport or upstream protocol problems.
    """
    context = get_context(config=config) if config is not None else get_context()
    question = validate_question(question)
    record = context.directory.authorize(chatflow_id)
    return context.client.predict(record.id, question)
