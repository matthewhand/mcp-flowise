"""Unit tests for mcp_flowise.utils: logging hygiene, HTTP taxonomy, policy gate."""

from __future__ import annotations

import io
import json
import logging
import sys
import unittest

import requests

from fake_http import FakeResponse, FakeSession, connection_error, timeout_error
from mcp_flowise.config import load_config
from mcp_flowise.errors import (
    DiscoveryError,
    FlowiseConnectionError,
    FlowiseHttpError,
    FlowiseTimeoutError,
    MalformedResponseError,
    PolicyDeniedError,
)
from mcp_flowise.utils import (
    ChatflowDirectory,
    FlowiseClient,
    ToolCatalog,
    filter_chatflows,
    flowise_predict,
    normalize_tool_name,
    redact_api_key,
    safe_url,
    setup_logging,
)


def config(**env):
    env.setdefault("FLOWISE_API_ENDPOINT", "http://flowise.invalid:3000")
    env.setdefault("FLOWISE_API_KEY", "test-key")
    return load_config(env, load_env_file=False)


def client(**env) -> FlowiseClient:
    cfg = config(**env)
    return FlowiseClient(cfg, session=FakeSession())


class TestLoggingHygiene(unittest.TestCase):
    def tearDown(self):
        setup_logging(debug=False, log_dir=None)

    def test_records_go_to_stderr_never_stdout(self):
        out, err = io.StringIO(), io.StringIO()
        old_out, old_err = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = out, err
        try:
            logger = setup_logging(debug=True, log_dir=None)
            logger.critical("boom")
            logger.error("bad thing")
        finally:
            sys.stdout, sys.stderr = old_out, old_err

        self.assertEqual(out.getvalue(), "", "log records must never be written to stdout")
        self.assertIn("boom", err.getvalue())
        self.assertIn("bad thing", err.getvalue())

    def test_replaced_handlers_are_closed(self):
        first = setup_logging(debug=False, log_dir=None)
        stale = list(first.handlers)
        second = setup_logging(debug=False, log_dir=None)
        self.assertIs(first, second)
        for handler in stale:
            self.assertNotIn(handler, second.handlers)
            if isinstance(handler, logging.FileHandler):
                self.assertIsNone(handler.stream)

    def test_no_root_propagation(self):
        logger = setup_logging(debug=False, log_dir=None)
        self.assertFalse(logger.propagate)

    def test_handlers_are_wrapped_around_stdout(self):
        logger = setup_logging(debug=False, log_dir=None)
        for handler in logger.handlers:
            self.assertIsNot(getattr(handler, "stream", None), sys.stdout)


class TestRedactionHelpers(unittest.TestCase):
    def test_redact_api_key(self):
        self.assertEqual(redact_api_key(""), "<not set>")
        self.assertEqual(redact_api_key("abcd"), "<not set>")
        self.assertTrue(redact_api_key("abcdefgh").startswith("ab"))
        self.assertTrue(redact_api_key("abcdefgh").endswith("gh"))
        self.assertNotIn("cdef", redact_api_key("abcdefgh"))

    def test_safe_url_drops_credentials_and_query(self):
        self.assertEqual(
            safe_url("https://user:pass@host:8443/api/v1/prediction/abc?token=secret"),
            "https://host:8443/api/v1/prediction/abc",
        )

    def test_normalize_tool_name(self):
        self.assertEqual(normalize_tool_name("Tool Name"), "tool_name")
        self.assertEqual(normalize_tool_name("Tool-Name"), "tool_name")
        self.assertEqual(normalize_tool_name("Tool_Name"), "tool_name")
        self.assertEqual(normalize_tool_name("ToolName"), "toolname")
        self.assertEqual(normalize_tool_name(""), "unknown_tool")
        self.assertEqual(normalize_tool_name(None), "unknown_tool")
        self.assertEqual(normalize_tool_name("***"), "___")


class TestClientSuccessPath(unittest.TestCase):
    def test_prediction_returns_all_fields(self):
        payload = {"text": "hi", "json": {"a": 1}, "chatId": "c1"}
        session = FakeSession([FakeResponse(200, json.dumps(payload))])
        result = FlowiseClient(config(), session=session).predict("abc", "question")
        self.assertEqual(result, payload)
        self.assertEqual(session.count("POST"), 1)
        self.assertIn("/api/v1/prediction/abc", session.urls[0])

    def test_authorization_header_is_sent(self):
        session = FakeSession([FakeResponse(200, "{}")])
        FlowiseClient(config(FLOWISE_API_KEY="secret-token"), session=session).predict("abc", "q")
        headers = session.calls[0]["headers"]
        self.assertEqual(headers["Authorization"], "Bearer secret-token")

    def test_chatflow_catalog_is_simplified(self):
        session = FakeSession([FakeResponse(200, json.dumps([{"id": "1", "name": "A", "deployed": True}]))])
        chatflows = FlowiseClient(config(), session=session).list_chatflows()
        self.assertEqual(chatflows, [{"id": "1", "name": "A"}])

    def test_chatflow_id_is_url_encoded(self):
        session = FakeSession([FakeResponse(200, "{}")])
        FlowiseClient(config(), session=session).predict("a b", "q")
        self.assertIn("/api/v1/prediction/a%20b", session.urls[0])


class TestClientErrorTaxonomy(unittest.TestCase):
    def test_timeout(self):
        session = FakeSession([timeout_error()])
        with self.assertRaises(FlowiseTimeoutError) as ctx:
            FlowiseClient(config(), session=session).predict("abc", "q")
        self.assertEqual(ctx.exception.code, "timeout")

    def test_connection_failure(self):
        session = FakeSession([connection_error()])
        with self.assertRaises(FlowiseConnectionError) as ctx:
            FlowiseClient(config(), session=session).predict("abc", "q")
        self.assertEqual(ctx.exception.code, "connection_failed")

    def test_http_status_errors_are_distinct_and_body_is_not_reported(self):
        for status in (401, 403, 404, 500):
            with self.subTest(status=status):
                session = FakeSession([FakeResponse(status, "<html>secret internal detail</html>")])
                with self.assertRaises(FlowiseHttpError) as ctx:
                    FlowiseClient(config(), session=session).predict("abc", "q")
                self.assertEqual(ctx.exception.status_code, status)
                payload = json.dumps(ctx.exception.to_payload())
                self.assertNotIn("secret internal detail", payload)

    def test_malformed_json(self):
        session = FakeSession([FakeResponse(200, "not json at all", {"Content-Type": "text/html"})])
        with self.assertRaises(MalformedResponseError) as ctx:
            FlowiseClient(config(), session=session).predict("abc", "q")
        self.assertEqual(ctx.exception.code, "malformed_response")

    def test_json_array_prediction_is_rejected(self):
        session = FakeSession([FakeResponse(200, "[1,2,3]")])
        with self.assertRaises(MalformedResponseError):
            FlowiseClient(config(), session=session).predict("abc", "q")


class TestChatflowDirectory(unittest.TestCase):
    def test_empty_catalog_is_not_an_error(self):
        session = FakeSession([FakeResponse(200, "[]")])
        directory = ChatflowDirectory(FlowiseClient(config(), session=session), config().policy)
        self.assertEqual(directory.records(), ())

    def test_discovery_failure_raises_with_cause(self):
        for outcome, cause in (
            (timeout_error(), "timeout"),
            (connection_error(), "connection_failed"),
            (FakeResponse(500, "{}"), "http_error"),
            (FakeResponse(200, "<html>", {"Content-Type": "text/html"}), "malformed_response"),
        ):
            with self.subTest(cause=cause):
                session = FakeSession([outcome])
                directory = ChatflowDirectory(FlowiseClient(config(), session=session), config().policy)
                with self.assertRaises(DiscoveryError) as ctx:
                    directory.records()
                self.assertEqual(ctx.exception.cause, cause)

    def test_failed_refresh_keeps_previous_snapshot(self):
        session = FakeSession([FakeResponse(200, json.dumps([{"id": "1", "name": "A"}])), timeout_error()])
        directory = ChatflowDirectory(FlowiseClient(config(), session=session), config().policy)
        self.assertEqual(len(directory.records()), 1)
        with self.assertRaises(DiscoveryError):
            directory.refresh()
        self.assertEqual([r.id for r in directory.records()], ["1"])

    def test_authorize_without_policy_needs_no_network(self):
        session = FakeSession([])
        directory = ChatflowDirectory(FlowiseClient(config(), session=session), config().policy)
        record = directory.authorize("anything")
        self.assertEqual(record.id, "anything")
        self.assertEqual(session.calls, [])

    def test_authorize_denies_unknown_id_with_zero_posts(self):
        session = FakeSession([FakeResponse(200, json.dumps([{"id": "1", "name": "A"}]))])
        cfg = config(FLOWISE_WHITELIST_ID="1")
        directory = ChatflowDirectory(FlowiseClient(cfg, session=session), cfg.policy)
        with self.assertRaises(PolicyDeniedError):
            directory.authorize("2")
        self.assertEqual(session.count("POST"), 0)

    def test_authorize_denies_excluded_id_with_zero_posts(self):
        session = FakeSession([FakeResponse(200, json.dumps([{"id": "1", "name": "A"}, {"id": "2", "name": "B"}]))])
        cfg = config(FLOWISE_BLACKLIST_ID="2")
        directory = ChatflowDirectory(FlowiseClient(cfg, session=session), cfg.policy)
        with self.assertRaises(PolicyDeniedError):
            directory.authorize("2")
        self.assertEqual(session.count("POST"), 0)
        self.assertEqual(directory.authorize("1").id, "1")

    def test_authorize_uses_name_rules(self):
        payload = json.dumps([{"id": "1", "name": "Alpha"}, {"id": "2", "name": "Beta"}])
        session = FakeSession([FakeResponse(200, payload)])
        cfg = config(FLOWISE_WHITELIST_NAME_REGEX="^Alpha$")
        directory = ChatflowDirectory(FlowiseClient(cfg, session=session), cfg.policy)
        self.assertEqual(directory.authorize("1").id, "1")
        with self.assertRaises(PolicyDeniedError):
            directory.authorize("2")

    def test_authorize_rejects_dangerous_ids(self):
        session = FakeSession([])
        directory = ChatflowDirectory(FlowiseClient(config(), session=session), config(FLOWISE_WHITELIST_ID="1").policy)
        with self.assertRaises(PolicyDeniedError):
            directory.authorize("../../secrets")
        self.assertEqual(session.calls, [])


class TestFlowisePredictEntryPoint(unittest.TestCase):
    def test_authorized_target_posts_once(self):
        payload = {"text": "ok"}
        session = FakeSession(
            [
                FakeResponse(200, json.dumps([{"id": "1", "name": "A"}])),
                FakeResponse(200, json.dumps(payload)),
            ]
        )
        cfg = config(FLOWISE_WHITELIST_ID="1")
        from mcp_flowise.utils import get_context, set_context

        set_context(get_context(config=cfg, client=FlowiseClient(cfg, session=session)))
        try:
            self.assertEqual(flowise_predict("1", "hello"), payload)
            self.assertEqual(session.count("POST"), 1)
        finally:
            set_context(None)

    def test_denied_target_makes_no_post(self):
        session = FakeSession([FakeResponse(200, json.dumps([{"id": "1", "name": "A"}]))])
        cfg = config(FLOWISE_WHITELIST_ID="1")
        from mcp_flowise.utils import get_context, set_context

        set_context(get_context(config=cfg, client=FlowiseClient(cfg, session=session)))
        try:
            with self.assertRaises(PolicyDeniedError):
                flowise_predict("2", "hello")
            self.assertEqual(session.count("POST"), 0)
        finally:
            set_context(None)

    def test_invalid_question_is_rejected(self):
        from mcp_flowise.errors import ConfigError

        with self.assertRaises(ConfigError):
            flowise_predict("1", "   ")


class TestToolCatalog(unittest.TestCase):
    def test_replace_swaps_tools_and_mapping_together(self):
        catalog = ToolCatalog()
        catalog.replace(["a"], {"a": "1"})
        self.assertEqual(catalog.resolve("a"), "1")
        catalog.replace(["b"], {"b": "2"})
        self.assertEqual(catalog.resolve("a"), None)
        self.assertEqual(catalog.resolve("b"), "2")
        self.assertEqual(catalog.tools(), ("b",))

    def test_mapping_snapshot_is_a_copy(self):
        catalog = ToolCatalog()
        catalog.replace([], {"a": "1"})
        snapshot = catalog.mapping()
        snapshot["a"] = "hacked"
        self.assertEqual(catalog.resolve("a"), "1")


class TestFilterChatflowsWrapper(unittest.TestCase):
    def test_uses_configured_policy(self):
        chatflows = [{"id": "1", "name": "A"}, {"id": "2", "name": "B"}]
        policy = config(FLOWISE_WHITELIST_ID="2").policy
        self.assertEqual(filter_chatflows(chatflows, policy), [{"id": "2", "name": "B"}])


if __name__ == "__main__":
    unittest.main()
