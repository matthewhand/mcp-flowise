"""End-to-end MCP protocol tests (low-level mode) against the mock Flowise.

Real subprocess, real MCP JSON-RPC over stdio, real HTTP to the mock. The
replaces the previous "integration" test that only asserted against a
hand-written ``mock_client_request`` response and never touched the server.
"""

from __future__ import annotations

import unittest

from mcp_stdio_harness import result_json, run_session, tool_names
from mock_flowise import MockFlowise

CHATFLOWS = [
    {"id": "cf-alpha", "name": "Alpha Chatflow"},
    {"id": "cf-beta", "name": "Beta Chatflow"},
]


class LowLevelModeTestCase(unittest.TestCase):
    def setUp(self):
        self.mock = MockFlowise().start()
        self.addCleanup(self.mock.stop)
        self.mock.chatflows = [dict(cf) for cf in CHATFLOWS]

    def env(self, **overrides):
        from mcp_stdio_harness import server_env

        return server_env(self.mock.base_url, FLOWISE_SIMPLE_MODE="false", **overrides)


class TestLowLevelProtocol(LowLevelModeTestCase):
    def test_initialize_list_and_call(self):
        async def body(session):
            names = tool_names(await session.list_tools())
            self.assertEqual(sorted(names), ["alpha_chatflow", "beta_chatflow"])

            answer = await session.call_tool("alpha_chatflow", {"question": "hello"})
            self.assertFalse(answer.isError)
            payload = result_json(answer)
            self.assertEqual(payload["text"], "mock answer")
            self.assertEqual(payload["json"], {"answer": 42})
            return names

        run_session(self.env(), body)
        self.assertEqual(self.mock.state.prediction_paths(), ["/api/v1/prediction/cf-alpha"])

    def test_descriptions_override_chatflow_names(self):
        async def body(session):
            tools = await session.list_tools()
            return {tool.name: tool.description for tool in tools.tools}

        descriptions = run_session(
            self.env(FLOWISE_CHATFLOW_DESCRIPTIONS="cf-alpha:Custom description"), body
        )
        self.assertEqual(descriptions["alpha_chatflow"], "Custom description")
        self.assertEqual(descriptions["beta_chatflow"], "Beta Chatflow")

    def test_tool_name_conflicts_are_skipped(self):
        self.mock.chatflows = [
            {"id": "cf-1", "name": "Alpha Chatflow"},
            {"id": "cf-2", "name": "alpha-chatflow"},
        ]

        async def body(session):
            return tool_names(await session.list_tools())

        self.assertEqual(run_session(self.env(), body), ["alpha_chatflow"])


class TestLowLevelAuthorization(LowLevelModeTestCase):
    def test_filtered_chatflow_is_not_registered_and_cannot_be_called(self):
        async def body(session):
            names = tool_names(await session.list_tools())
            self.assertEqual(names, ["alpha_chatflow"])
            answer = await session.call_tool("beta_chatflow", {"question": "hi"})
            self.assertTrue(answer.isError)
            return result_json(answer)

        payload = run_session(self.env(FLOWISE_WHITELIST_ID="cf-alpha"), body)
        self.assertEqual(payload["error"], "unknown_tool")
        self.assertEqual(self.mock.prediction_calls, 0)

    def test_blacklisted_chatflow_is_not_registered(self):
        async def body(session):
            return tool_names(await session.list_tools())

        names = run_session(self.env(FLOWISE_BLACKLIST_ID="cf-beta"), body)
        self.assertEqual(names, ["alpha_chatflow"])

    def test_empty_catalog_starts_an_empty_but_working_server(self):
        self.mock.chatflows = []

        async def body(session):
            return tool_names(await session.list_tools())

        self.assertEqual(run_session(self.env(), body), [])
        self.assertEqual(self.mock.prediction_calls, 0)


class TestLowLevelStaleTools(LowLevelModeTestCase):
    def test_refreshed_catalog_drops_stale_tools(self):
        """A tool removed from Flowise must stop working after a refresh."""

        async def body(session):
            first = tool_names(await session.list_tools())
            self.assertEqual(sorted(first), ["alpha_chatflow", "beta_chatflow"])

            # Flowise drops the beta chatflow, then the client re-lists.
            self.mock.chatflows = [{"id": "cf-alpha", "name": "Alpha Chatflow"}]
            second = tool_names(await session.list_tools())
            self.assertEqual(second, ["alpha_chatflow"])

            self.mock.state.reset_recordings()
            answer = await session.call_tool("beta_chatflow", {"question": "hi"})
            self.assertTrue(answer.isError)
            return result_json(answer)

        payload = run_session(self.env(), body)
        self.assertEqual(payload["error"], "unknown_tool")
        self.assertEqual(self.mock.prediction_calls, 0, "a stale tool must not reach Flowise")

    def test_stale_tool_is_denied_even_when_mapping_is_stale(self):
        """Defense in depth: a filtered-out target is denied before any POST."""

        async def body(session):
            tool_names(await session.list_tools())
            # Re-add beta to Flowise but keep it filtered out; the mapping still
            # holds the stale name, so dispatch-time authorization must stop it.
            self.mock.chatflows = [dict(cf) for cf in CHATFLOWS]
            self.mock.state.reset_recordings()
            answer = await session.call_tool("beta_chatflow", {"question": "hi"})
            self.assertTrue(answer.isError)
            return result_json(answer)

        payload = run_session(self.env(FLOWISE_WHITELIST_ID="cf-alpha"), body)
        self.assertIn(payload["error"], {"policy_denied", "unknown_tool"})
        self.assertEqual(self.mock.prediction_calls, 0)


class TestLowLevelArgumentValidation(LowLevelModeTestCase):
    def test_missing_question_is_rejected(self):
        async def body(session):
            tool_names(await session.list_tools())
            self.mock.state.reset_recordings()
            answer = await session.call_tool("alpha_chatflow", {})
            self.assertTrue(answer.isError)
            return result_json(answer)

        payload = run_session(self.env(), body)
        self.assertEqual(payload["error"], "config_error")
        self.assertEqual(self.mock.prediction_calls, 0)

    def test_empty_question_is_rejected(self):
        async def body(session):
            tool_names(await session.list_tools())
            self.mock.state.reset_recordings()
            answer = await session.call_tool("alpha_chatflow", {"question": "   "})
            self.assertTrue(answer.isError)
            return result_json(answer)

        run_session(self.env(), body)
        self.assertEqual(self.mock.prediction_calls, 0)

    def test_non_string_question_is_rejected(self):
        async def body(session):
            tool_names(await session.list_tools())
            self.mock.state.reset_recordings()
            answer = await session.call_tool("alpha_chatflow", {"question": {"nested": "object"}})
            self.assertTrue(answer.isError)
            return result_json(answer)

        payload = run_session(self.env(), body)
        self.assertEqual(payload["error"], "config_error")
        self.assertEqual(self.mock.prediction_calls, 0)


class TestLowLevelErrorsAreDistinguishable(LowLevelModeTestCase):
    def _call(self, **env):
        async def body(session):
            tool_names(await session.list_tools())
            answer = await session.call_tool("alpha_chatflow", {"question": "hi"})
            self.assertTrue(answer.isError)
            return result_json(answer)

        return run_session(self.env(**env), body)

    def test_http_500_on_prediction(self):
        self.mock.state.prediction_status = 500
        payload = self._call()
        self.assertEqual(payload["error"], "http_error")
        self.assertEqual(payload["status_code"], 500)

    def test_http_404_on_prediction(self):
        self.mock.state.prediction_status = 404
        self.assertEqual(self._call()["status_code"], 404)

    def test_bad_json_on_prediction(self):
        self.mock.state.prediction_body = "{not json"
        self.mock.state.prediction_content_type = "application/json"
        self.assertEqual(self._call()["error"], "malformed_response")

    def test_timeout_on_prediction(self):
        self.mock.state.prediction_delay = 3.0
        self.assertEqual(self._call(FLOWISE_API_TIMEOUT="0.5")["error"], "timeout")

    def test_discovery_failure_keeps_last_known_catalog(self):
        async def body(session):
            names = tool_names(await session.list_tools())
            self.mock.state.chatflows_status = 500
            self.mock.state.reset_recordings()
            after = tool_names(await session.list_tools())
            self.assertEqual(after, names)
            return names

        self.assertEqual(len(run_session(self.env(), body)), 2)


if __name__ == "__main__":
    unittest.main()
