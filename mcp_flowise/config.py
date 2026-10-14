"""Centralized, validated configuration and chatflow filter policy.

Every environment variable used by mcp-flowise is read, trimmed and validated
exactly once, here, *before* any network call is made. Both servers and the
Flowise HTTP client consume the resulting :class:`FlowiseConfig` /
:class:`FilterPolicy` objects, so listing, dispatching and startup all agree on
what is allowed.

Filter semantics (unchanged from the original implementation, now centralized
and documented):

* Whitelist takes precedence over blacklist.
* If any whitelist rule is configured (by ID or by name regex), a chatflow is
  exposed **only** when it matches a whitelist rule; blacklists are not
  consulted for whitelisted chatflows and non-whitelisted chatflows are dropped
  regardless of blacklists.
* If no whitelist rule is configured, blacklists (by ID and/or by name regex)
  remove matching chatflows and everything else is exposed.

Legacy names ``FLOWISE_CHATFLOW_WHITELIST`` / ``FLOWISE_CHATFLOW_BLACKLIST``
(from the old FastMCP-only implementation) are still honoured, but only as a
*fallback*: the canonical ``FLOWISE_WHITELIST_ID`` / ``FLOWISE_BLACKLIST_ID`` wins
whenever it is set. Merging the two would let a leftover legacy variable
re-admit IDs the operator deliberately restricted, so the legacy variable is
ignored (and reported) instead of widening access.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, Iterable, List, Mapping, Optional, Pattern, Tuple
from urllib.parse import urlparse

from dotenv import load_dotenv

from mcp_flowise.errors import ConfigError

logger = logging.getLogger("mcp_flowise.config")

__all__ = [
    "MODE_SIMPLE",
    "MODE_LOWLEVEL",
    "VALID_MODES",
    "DEFAULT_MODE",
    "DEFAULT_ENDPOINT",
    "DEFAULT_TIMEOUT",
    "MAX_QUESTION_CHARS",
    "FilterPolicy",
    "FlowiseConfig",
    "load_config",
    "normalize_id",
    "validate_question",
]

MODE_SIMPLE = "simple"
MODE_LOWLEVEL = "lowlevel"
VALID_MODES: Tuple[str, str] = (MODE_SIMPLE, MODE_LOWLEVEL)
# Runtime default. Documented in README / .env.example / smithery.yaml.
DEFAULT_MODE = MODE_SIMPLE

DEFAULT_ENDPOINT = "http://localhost:3000"
DEFAULT_TIMEOUT = 30.0
MAX_TIMEOUT = 600.0
MAX_QUESTION_CHARS = 10_000
MAX_ID_CHARS = 128
MAX_REGEX_CHARS = 512

WHITELIST_ID_ENV = "FLOWISE_WHITELIST_ID"
BLACKLIST_ID_ENV = "FLOWISE_BLACKLIST_ID"
WHITELIST_NAME_ENV = "FLOWISE_WHITELIST_NAME_REGEX"
BLACKLIST_NAME_ENV = "FLOWISE_BLACKLIST_NAME_REGEX"
LEGACY_WHITELIST_ENV = "FLOWISE_CHATFLOW_WHITELIST"
LEGACY_BLACKLIST_ENV = "FLOWISE_CHATFLOW_BLACKLIST"

_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")

_TRUE_VALUES = {"true", "1", "yes", "on"}
_FALSE_VALUES = {"false", "0", "no", "off"}


def normalize_id(value: Any, *, field_name: str = "id") -> Optional[str]:
    """Trim and validate a Flowise identifier (chatflow/assistant ID).

    Whitespace around IDs is a common configuration mistake, so surrounding
    whitespace is trimmed. IDs are then restricted to ``[A-Za-z0-9._-]``: they
    are interpolated into a URL path, and anything else (slashes, ``..``,
    whitespace, control characters, URL escapes) is rejected instead of being
    silently sanitised into a different request.

    Returns ``None`` for empty/blank input. Raises :class:`ConfigError`
    otherwise.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        raise ConfigError(f"{field_name} must be a string, got {type(value).__name__}")
    candidate = value.strip()
    if not candidate:
        return None
    if len(candidate) > MAX_ID_CHARS:
        raise ConfigError(f"{field_name} exceeds {MAX_ID_CHARS} characters")
    if not _ID_RE.match(candidate):
        raise ConfigError(
            f"{field_name} contains unsupported characters; only letters, digits, '.', '_' and '-' are allowed"
        )
    return candidate


def validate_question(question: Any, *, field_name: str = "question") -> str:
    """Validate the user supplied ``question`` argument of a tool call."""
    if question is None:
        raise ConfigError(f"'{field_name}' is required")
    if not isinstance(question, str):
        raise ConfigError(f"'{field_name}' must be a string, got {type(question).__name__}")
    if not question.strip():
        raise ConfigError(f"'{field_name}' must not be empty")
    if len(question) > MAX_QUESTION_CHARS:
        raise ConfigError(f"'{field_name}' exceeds {MAX_QUESTION_CHARS} characters")
    return question


def _split_ids(raw: Optional[str]) -> FrozenSet[str]:
    if not raw:
        return frozenset()
    ids = set()
    for part in raw.split(","):
        candidate = normalize_id(part, field_name="filter ID")
        if candidate:
            ids.add(candidate)
    return frozenset(ids)


def _resolve_id_filter(
    canonical_raw: Optional[str], legacy_raw: Optional[str], legacy_name: str
) -> Tuple[FrozenSet[str], List[str], List[str]]:
    """Resolve one ID filter from the canonical and legacy environment names.

    The legacy variable is a *fallback*: it is only consulted when the canonical
    variable is absent/blank. Merging the two (union) would let a leftover
    legacy variable re-admit IDs that the operator deliberately restricted with
    the canonical variable, i.e. it would broaden access. Falling back keeps the
    effective policy exactly equal to whichever single variable the operator set,
    and the ignored legacy variable is reported so it can be cleaned up.
    """
    if canonical_raw and canonical_raw.strip():
        return _split_ids(canonical_raw), [], ([legacy_name] if (legacy_raw or "").strip() else [])
    if legacy_raw and legacy_raw.strip():
        return _split_ids(legacy_raw), [legacy_name], []
    return frozenset(), [], []


def _compile_regex(raw: Optional[str], env_name: str) -> Optional[Pattern[str]]:
    if raw is None:
        return None
    pattern = raw.strip()
    if not pattern:
        return None
    if len(pattern) > MAX_REGEX_CHARS:
        raise ConfigError(f"{env_name} exceeds {MAX_REGEX_CHARS} characters")
    try:
        return re.compile(pattern)
    except re.error as exc:
        raise ConfigError(f"{env_name} is not a valid regular expression: {exc.msg}") from None


@dataclass(frozen=True)
class FilterPolicy:
    """Immutable whitelist/blacklist policy applied to catalog and dispatch."""

    whitelist_ids: FrozenSet[str] = frozenset()
    blacklist_ids: FrozenSet[str] = frozenset()
    whitelist_name: Optional[Pattern[str]] = None
    blacklist_name: Optional[Pattern[str]] = None
    whitelist_name_env: str = WHITELIST_NAME_ENV
    blacklist_name_env: str = BLACKLIST_NAME_ENV
    legacy_env_names: Tuple[str, ...] = ()
    ignored_legacy_env_names: Tuple[str, ...] = ()

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "FilterPolicy":
        whitelist_ids, legacy_wl, ignored_wl = _resolve_id_filter(
            env.get(WHITELIST_ID_ENV), env.get(LEGACY_WHITELIST_ENV), LEGACY_WHITELIST_ENV
        )
        blacklist_ids, legacy_bl, ignored_bl = _resolve_id_filter(
            env.get(BLACKLIST_ID_ENV), env.get(LEGACY_BLACKLIST_ENV), LEGACY_BLACKLIST_ENV
        )
        return cls(
            whitelist_ids=whitelist_ids,
            blacklist_ids=blacklist_ids,
            whitelist_name=_compile_regex(env.get(WHITELIST_NAME_ENV), WHITELIST_NAME_ENV),
            blacklist_name=_compile_regex(env.get(BLACKLIST_NAME_ENV), BLACKLIST_NAME_ENV),
            legacy_env_names=tuple(legacy_wl + legacy_bl),
            ignored_legacy_env_names=tuple(ignored_wl + ignored_bl),
        )

    @property
    def enabled(self) -> bool:
        """True when at least one allow/deny rule is configured."""
        return bool(self.whitelist_ids or self.blacklist_ids or self.whitelist_name or self.blacklist_name)

    @property
    def needs_name(self) -> bool:
        """True when a rule can only be evaluated against a chatflow *name*."""
        return bool(self.whitelist_name or self.blacklist_name)

    def allows(self, chatflow_id: Any, chatflow_name: Any = "") -> bool:
        """Return True when ``chatflow_id``/``chatflow_name`` may be used."""
        identifier = chatflow_id.strip() if isinstance(chatflow_id, str) else chatflow_id
        name = chatflow_name if isinstance(chatflow_name, str) else ""

        if self.whitelist_ids or self.whitelist_name:
            if self.whitelist_ids and identifier in self.whitelist_ids:
                return True
            if self.whitelist_name and self.whitelist_name.search(name):
                return True
            return False

        if self.blacklist_ids and identifier in self.blacklist_ids:
            return False
        if self.blacklist_name and self.blacklist_name.search(name):
            return False
        return True

    def filter_chatflows(self, chatflows: Iterable[Mapping[str, Any]]) -> List[Dict[str, str]]:
        """Apply the policy to ``chatflows`` preserving order."""
        result: List[Dict[str, str]] = []
        for chatflow in chatflows:
            identifier = chatflow.get("id", "")
            name = chatflow.get("name") or ""
            if self.allows(identifier, name):
                result.append({"id": identifier, "name": name})
        return result

    def summary(self) -> Dict[str, Any]:
        """Sanitized description of the active policy (no secret material)."""
        return {
            "enabled": self.enabled,
            "whitelist_ids": sorted(self.whitelist_ids),
            "blacklist_ids": sorted(self.blacklist_ids),
            "whitelist_name_regex": self.whitelist_name.pattern if self.whitelist_name else None,
            "blacklist_name_regex": self.blacklist_name.pattern if self.blacklist_name else None,
            "legacy_env_names": list(self.legacy_env_names),
            "ignored_legacy_env_names": list(self.ignored_legacy_env_names),
        }


@dataclass(frozen=True)
class FlowiseConfig:
    """Fully validated runtime configuration."""

    endpoint: str = DEFAULT_ENDPOINT
    api_key: str = field(default="", repr=False)
    mode: str = DEFAULT_MODE
    chatflow_id: Optional[str] = None
    assistant_id: Optional[str] = None
    descriptions: Mapping[str, str] = field(default_factory=dict)
    policy: FilterPolicy = field(default_factory=FilterPolicy)
    timeout: float = DEFAULT_TIMEOUT
    debug: bool = False

    @property
    def uses_static_target(self) -> bool:
        return self.chatflow_id is not None

    def auth_headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def read_headers(self) -> Dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def describe(self) -> Dict[str, Any]:
        """Sanitized configuration summary, safe to log."""
        return {
            "endpoint": self.endpoint,
            "mode": self.mode,
            "chatflow_id_configured": self.chatflow_id is not None,
            "assistant_id_configured": self.assistant_id is not None,
            "descriptions_configured": sorted(self.descriptions),
            "timeout_seconds": self.timeout,
            "debug": self.debug,
            "policy": self.policy.summary(),
        }


def _parse_mode(env: Mapping[str, str]) -> str:
    raw_mode = (env.get("FLOWISE_MODE") or "").strip()
    if raw_mode:
        normalized = raw_mode.lower()
        if normalized in {MODE_SIMPLE, "fastmcp"}:
            return MODE_SIMPLE
        if normalized in {MODE_LOWLEVEL, "dynamic"}:
            return MODE_LOWLEVEL
        raise ConfigError(f"FLOWISE_MODE must be one of {', '.join(VALID_MODES)}; got '{raw_mode}'")

    raw_simple = (env.get("FLOWISE_SIMPLE_MODE") or "").strip()
    if not raw_simple:
        return DEFAULT_MODE
    lowered = raw_simple.lower()
    if lowered in _TRUE_VALUES:
        return MODE_SIMPLE
    if lowered in _FALSE_VALUES:
        return MODE_LOWLEVEL
    raise ConfigError(f"FLOWISE_SIMPLE_MODE must be a boolean value (true/false); got '{raw_simple}'")


def _parse_endpoint(raw: Optional[str]) -> str:
    candidate = (raw or DEFAULT_ENDPOINT).strip()
    if not candidate:
        candidate = DEFAULT_ENDPOINT
    parsed = urlparse(candidate)
    if parsed.scheme not in ("http", "https"):
        raise ConfigError(f"FLOWISE_API_ENDPOINT must start with http:// or https://; got '{parsed.scheme or candidate}'")
    if not parsed.netloc:
        raise ConfigError(f"FLOWISE_API_ENDPOINT is missing a host; got '{candidate}'")
    if parsed.query or parsed.fragment:
        raise ConfigError("FLOWISE_API_ENDPOINT must not contain a query string or fragment")
    normalized = f"{parsed.scheme}://{parsed.netloc}{parsed.path}".rstrip("/")
    return normalized or f"{parsed.scheme}://{parsed.netloc}"


def _parse_timeout(raw: Optional[str]) -> float:
    if raw is None or not str(raw).strip():
        return DEFAULT_TIMEOUT
    try:
        value = float(str(raw).strip())
    except (TypeError, ValueError):
        raise ConfigError(f"FLOWISE_API_TIMEOUT must be a number; got '{raw}'") from None
    if value <= 0 or value > MAX_TIMEOUT:
        raise ConfigError(f"FLOWISE_API_TIMEOUT must be > 0 and <= {MAX_TIMEOUT:g} seconds; got {value:g}")
    return value


def _parse_descriptions(env: Mapping[str, str]) -> Dict[str, str]:
    raw = env.get("FLOWISE_CHATFLOW_DESCRIPTIONS") or ""
    descriptions: Dict[str, str] = {}
    for pair in raw.split(","):
        pair = pair.strip()
        if not pair:
            continue
        if ":" not in pair:
            logger.warning("Ignoring malformed FLOWISE_CHATFLOW_DESCRIPTIONS entry (expected 'id:description')")
            continue
        raw_id, description = pair.split(":", 1)
        try:
            identifier = normalize_id(raw_id, field_name="FLOWISE_CHATFLOW_DESCRIPTIONS id")
        except ConfigError as exc:
            logger.warning("Ignoring malformed FLOWISE_CHATFLOW_DESCRIPTIONS entry: %s", exc.message)
            continue
        description = description.strip()
        if identifier and description:
            descriptions[identifier] = description
    return descriptions


def load_config(env: Optional[Mapping[str, str]] = None, *, load_env_file: bool = True) -> FlowiseConfig:
    """Load, trim and validate the full configuration.

    Raises :class:`ConfigError` for an invalid endpoint, an unknown mode,
    contradictory targets (both chatflow and assistant IDs), an unsupported
    assistant target, malformed filter regexes or a bad timeout - always before
    any network traffic happens.
    """
    if load_env_file:
        load_dotenv(override=False)
    environ: Mapping[str, str] = os.environ if env is None else env

    endpoint = _parse_endpoint(environ.get("FLOWISE_API_ENDPOINT"))
    mode = _parse_mode(environ)
    timeout = _parse_timeout(environ.get("FLOWISE_API_TIMEOUT"))
    chatflow_id = normalize_id(environ.get("FLOWISE_CHATFLOW_ID"), field_name="FLOWISE_CHATFLOW_ID")
    assistant_id = normalize_id(environ.get("FLOWISE_ASSISTANT_ID"), field_name="FLOWISE_ASSISTANT_ID")
    descriptions = _parse_descriptions(environ)
    policy = FilterPolicy.from_env(environ)

    if chatflow_id and assistant_id:
        raise ConfigError(
            "FLOWISE_CHATFLOW_ID and FLOWISE_ASSISTANT_ID are both set; set only one (conflicting targets)"
        )
    if assistant_id:
        raise ConfigError(
            "FLOWISE_ASSISTANT_ID is set but assistant support is not implemented; "
            "unset it and use FLOWISE_CHATFLOW_ID with a chatflow ID"
        )

    debug = (environ.get("DEBUG") or "").strip().lower() in _TRUE_VALUES

    return FlowiseConfig(
        endpoint=endpoint,
        api_key=(environ.get("FLOWISE_API_KEY") or "").strip(),
        mode=mode,
        chatflow_id=chatflow_id,
        assistant_id=assistant_id,
        descriptions=descriptions,
        policy=policy,
        timeout=timeout,
        debug=debug,
    )
