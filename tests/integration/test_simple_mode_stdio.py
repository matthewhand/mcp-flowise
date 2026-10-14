"""End-to-end MCP protocol tests (simple mode) against the mock Flowise.

Every test starts a real ``python -m mcp_flowise`` subprocess and speaks real
MCP JSON-RPC to it with the official ``mcp`` client. The Flowise endpoint is the
task-owned mock, which records requests so "zero prediction POSTs" can be
asserted.
"""

from __future__ import annotations

import json
import unittest

from mcp_stdio_harness import result_json, run_session, tool_names
from mock_flowise import MockFlowise

CHATFLOWS = [
    {"id": "cf-alpha", "name": "Alpha Chatflow"},
    {"id": "cf-beta", "name": "Beta Chatflow"},
]


class SimpleModeTestCase(unittest.TestCase):
    def setUp(self):
        self.mock = MockFlowise().start()
        self.addCleanup(self.mock.stop)
        self.mock.chatflows = [dict(cf) for cf in CHATFLOWS]

    def env(self, **overrides):
        from mcp_stdio_harness import server_env

        return server_env(self.mock.base_url, FLOWISE_SIMPLE_MODE="true", **overrides)


class TestSimpleModeProtocol(SimpleModeTestCase):
    def test_initialize_list_and_call(self):
        async def body(session):
            tools = await session.list_tools()
            names = tool_names(tools)
            self.assertIn("list_chatflows", names)
            self.assertIn("create_prediction", names)

            listed = result_json(await session.call_tool("list_chatflows", {}))
            self.assertEqual([cf["id"] for cf in listed], ["cf-alpha", "cf-beta"])

            answer = await session.call_tool("create_prediction", {"question": "hello", "chatflow_id": "cf-alpha"})
            self.assertFalse(answer.isError)
            payload = result_json(answer)
            self.assertEqual(payload["text"], "mock answer")
            self.assertEqual(payload["json"], {"answer": 42})
            self.assertEqual(payload["chatId"], "chat-mock")
            self.assertEqual(payload["sessionId"], "session-mock")
            return names

        names = run_session(self.env(), body)
        self.assertEqual(self.mock.prediction_calls, 1)
        self.assertEqual(self.mock.state.prediction_paths(), ["/api/v1/prediction/cf-alpha"])

    def test_simple_mode_does_not_discover_at_startup(self):
        """Neither startup nor tools/list contacts Flowise in simple mode."""
        env = self.env()
        run_session(env, _list_only)
        self.assertEqual(self.mock.discovery_calls, 0)
        self.assertEqual(self.mock.prediction_calls, 0)

    def test_configured_default_target(self):
        async def body(session):
            answer = await session.call_tool("create_prediction", {"question": "hi"})
            self.assertFalse(answer.isError)
            return result_json(answer)

        run_session(self.env(FLOWISE_CHATFLOW_ID="cf-beta"), body)
        self.assertEqual(self.mock.state.prediction_paths(), ["/api/v1/prediction/cf-beta"])

    def test_missing_target_is_an_error(self):
        async def body(session):
            answer = await session.call_tool("create_prediction", {"question": "hi"})
            self.assertTrue(answer.isError)
            return result_json(answer)

        payload = run_session(self.env(), body)
        self.assertEqual(payload["error"], "config_error")
        self.assertEqual(self.mock.prediction_calls, 0)


class TestSimpleModeAuthorization(SimpleModeTestCase):
    def test_filtered_target_is_denied_with_zero_posts(self):
        async def body(session):
            answer = await session.call_tool("create_prediction", {"question": "hi", "chatflow_id": "cf-beta"})
            self.assertTrue(answer.isError)
            return result_json(answer)

        payload = run_session(self.env(FLOWISE_WHITELIST_ID="cf-alpha"), body)
        self.assertEqual(payload["error"], "policy_denied")
        self.assertEqual(self.mock.prediction_calls, 0, "a denied target must not trigger any prediction POST")

    def test_configured_target_is_authorized_too(self):
        """FLOWISE_CHATFLOW_ID is filtered just like an explicit argument."""

        async def body(session):
            answer = await session.call_tool("create_prediction", {"question": "hi"})
            self.assertTrue(answer.isError)
            return result_json(answer)

        payload = run_session(
            self.env(FLOWISE_CHATFLOW_ID="cf-beta", FLOWISE_BLACKLIST_ID="cf-beta"), body
        )
        self.assertEqual(payload["error"], "policy_denied")
        self.assertEqual(self.mock.prediction_calls, 0)

    def test_unknown_target_is_denied_with_zero_posts(self):
        async def body(session):
            answer = await session.call_tool("create_prediction", {"question": "hi", "chatflow_id": "cf-ghost"})
            self.assertTrue(answer.isError)
            return result_json(answer)

        payload = run_session(self.env(FLOWISE_WHITELIST_ID="cf-alpha"), body)
        self.assertEqual(payload["error"], "policy_denied")
        self.assertEqual(self.mock.prediction_calls, 0)

    def test_catalog_respects_the_policy(self):
        async def body(session):
            return result_json(await session.call_tool("list_chatflows", {}))

        listed = run_session(self.env(FLOWISE_WHITELIST_ID="cf-beta"), body)
        self.assertEqual([cf["id"] for cf in listed], ["cf-beta"])

    def test_legacy_filter_name_is_still_honoured(self):
        async def body(session):
            return result_json(await session.call_tool("list_chatflows", {}))

        listed = run_session(self.env(FLOWISE_CHATFLOW_WHITELIST="cf-alpha"), body)
        self.assertEqual([cf["id"] for cf in listed], ["cf-alpha"])


class TestSimpleModeErrorsAreDistinguishable(SimpleModeTestCase):
    def _call(self):
        async def body(session):
            answer = await session.call_tool("create_prediction", {"question": "hi", "chatflow_id": "cf-alpha"})
            self.assertTrue(answer.isError)
            return result_json(answer)

        return run_session(self.env(), body)

    def test_http_401(self):
        self.mock.state.expected_api_key = "a-different-key"
        self.assertEqual(self._call()["error"], "http_error")

    def test_http_403(self):
        self.mock.state.prediction_status = 403
        payload = self._call()
        self.assertEqual(payload["error"], "http_error")
        self.assertEqual(payload["status_code"], 403)
        self.assertNotIn("flowise error page", json.dumps(payload))

    def test_http_404(self):
        self.mock.state.prediction_status = 404
        self.assertEqual(self._call()["status_code"], 404)

    def test_http_500(self):
        self.mock.state.prediction_status = 500
        self.assertEqual(self._call()["status_code"], 500)

    def test_malformed_json(self):
        self.mock.state.prediction_body = "<html>not json</html>"
        self.mock.state.prediction_content_type = "text/html"
        self.assertEqual(self._call()["error"], "malformed_response")

    def test_timeout(self):
        self.mock.state.prediction_delay = 3.0
        payload = run_session(self.env(FLOWISE_API_TIMEOUT="0.5"), self._body())
        self.assertEqual(payload["error"], "timeout")

    def _body(self):
        async def body(session):
            answer = await session.call_tool("create_prediction", {"question": "hi", "chatflow_id": "cf-alpha"})
            self.assertTrue(answer.isError)
            return result_json(answer)

        return body

    def test_empty_catalog_is_not_an_error(self):
        self.mock.chatflows = []
        env = self.env()

        async def body(session):
            listed = await session.call_tool("list_chatflows", {})
            self.assertFalse(listed.isError)
            return result_json(listed)

        self.assertEqual(run_session(env, body), [])

    def test_discovery_failure_is_distinct_from_empty_catalog(self):
        self.mock.state.chatflows_status = 500
        env = self.env()

        async def body(session):
            answer = await session.call_tool("list_chatflows", {})
            self.assertTrue(answer.isError)
            return result_json(answer)

        payload = run_session(env, body)
        self.assertEqual(payload["error"], "discovery_failed")
        self.assertEqual(payload["cause"], "http_error")


async def _list_only(session):
    return tool_names(await session.list_tools())


if __name__ == "__main__":
    unittest.main()
