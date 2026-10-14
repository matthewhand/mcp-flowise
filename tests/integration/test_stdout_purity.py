"""Protocol purity, secret hygiene and startup-failure tests.

The MCP stdio transport owns stdout: every non-empty stdout line must be a
valid JSON-RPC message, including on error paths. Diagnostics belong on stderr
and must never contain the API key, the user's question, or upstream bodies.
"""

from __future__ import annotations

import json
import unittest

from mcp_stdio_harness import raw_protocol_exchange, run_to_completion, server_env
from mock_flowise import MockFlowise

SECRET = "SENTINEL_API_KEY_d0notl3ak"
QUESTION = "SENTINEL_QUESTION_9f3a2b"
BODY = "SENTINEL_RESPONSE_BODY_77c1de"

SENTINELS = (SECRET, QUESTION, BODY)

LIST_TOOLS = {"method": "tools/list"}
CALL_PREDICTION = {
    "method": "tools/call",
    "params": {"name": "create_prediction", "arguments": {"question": QUESTION, "chatflow_id": "cf-alpha"}},
}
CALL_DENIED = {
    "method": "tools/call",
    "params": {"name": "create_prediction", "arguments": {"question": QUESTION, "chatflow_id": "cf-beta"}},
}
CALL_LIST = {"method": "tools/call", "params": {"name": "list_chatflows", "arguments": {}}}


class StdoutPurityTestCase(unittest.TestCase):
    def setUp(self):
        self.mock = MockFlowise().start()
        self.addCleanup(self.mock.stop)
        self.mock.chatflows = [{"id": "cf-alpha", "name": "Alpha Chatflow"}, {"id": "cf-beta", "name": "Beta Chatflow"}]
        self.mock.state.prediction_body = json.dumps({"text": BODY, "json": {"leak": BODY}})

    def exchange(self, **overrides):
        env = server_env(self.mock.base_url, FLOWISE_API_KEY=SECRET, DEBUG="true", **overrides)
        return raw_protocol_exchange(env, [LIST_TOOLS, CALL_LIST, CALL_PREDICTION, CALL_DENIED])

    def assert_clean(self, exchange):
        self.assertTrue(exchange.stdout_lines, "the server produced no protocol output at all")
        for index, line in enumerate(exchange.stdout_lines):
            with self.subTest(index=index, line=line):
                message = json.loads(line)  # raises if stdout carries non-protocol output
                self.assertEqual(message.get("jsonrpc"), "2.0")
        # The API key and the question are never echoed back to the client.
        for sentinel in (SECRET, QUESTION):
            with self.subTest(sentinel=sentinel, stream="stdout"):
                self.assertNotIn(sentinel, exchange.stdout)
        # Upstream response bodies may be *returned* to the caller (that is the
        # tool's job) but must never be written to the diagnostics stream.
        for sentinel in SENTINELS:
            with self.subTest(sentinel=sentinel, stream="stderr"):
                self.assertNotIn(sentinel, exchange.stderr)


class TestStdoutPuritySimpleMode(StdoutPurityTestCase):
    def test_every_stdout_line_is_jsonrpc(self):
        exchange = self.exchange(FLOWISE_SIMPLE_MODE="true")
        self.assert_clean(exchange)

    def test_debug_logging_never_reaches_stdout_or_leaks(self):
        exchange = self.exchange(FLOWISE_SIMPLE_MODE="true")
        # DEBUG=true really is active (proof: the client logs its HTTP calls)...
        self.assertIn("mcp_flowise.client - POST", exchange.stderr)
        # ...and the sensitive values are in neither stream.
        self.assert_clean(exchange)

    def test_error_path_keeps_stdout_valid(self):
        self.mock.state.prediction_status = 500
        exchange = self.exchange(FLOWISE_SIMPLE_MODE="true")
        self.assert_clean(exchange)
        responses = [json.loads(line) for line in exchange.stdout_lines if json.loads(line).get("id") in (4, 5)]
        errors = [r["result"] for r in responses if r.get("result", {}).get("isError")]
        self.assertTrue(errors)
        self.assertTrue(all("error" in json.dumps(r) for r in errors))


class TestStdoutPurityLowLevelMode(StdoutPurityTestCase):
    def test_every_stdout_line_is_jsonrpc(self):
        exchange = self.exchange(FLOWISE_SIMPLE_MODE="false")
        self.assert_clean(exchange)
        self.assertEqual(exchange.returncode, 0)

    def test_error_path_keeps_stdout_valid(self):
        self.mock.state.prediction_status = 500
        exchange = self.exchange(FLOWISE_SIMPLE_MODE="false")
        self.assert_clean(exchange)


class TestStartupFailures(unittest.TestCase):
    def test_invalid_endpoint_fails_fast_without_network(self):
        env = server_env("not-a-url", FLOWISE_SIMPLE_MODE="true")
        result = run_to_completion(env)
        self.assertEqual(result.returncode, 1)
        self.assertIn("configuration error", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_conflicting_targets_fail_fast(self):
        env = server_env("http://127.0.0.1:9", FLOWISE_CHATFLOW_ID="a", FLOWISE_ASSISTANT_ID="b")
        result = run_to_completion(env)
        self.assertEqual(result.returncode, 1)
        self.assertIn("conflicting targets", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_assistant_id_is_rejected_as_unimplemented(self):
        env = server_env("http://127.0.0.1:9", FLOWISE_ASSISTANT_ID="a")
        result = run_to_completion(env)
        self.assertEqual(result.returncode, 1)
        self.assertIn("not implemented", result.stderr)

    def test_lowlevel_discovery_failure_exits_with_a_clear_error(self):
        mock = MockFlowise().start()
        self.addCleanup(mock.stop)
        mock.state.chatflows_status = 503
        env = server_env(mock.base_url, FLOWISE_SIMPLE_MODE="false")
        result = run_to_completion(env)
        self.assertEqual(result.returncode, 1)
        self.assertIn("Cannot start", result.stderr)
        self.assertIn("http_error", result.stderr)
        self.assertEqual(result.stdout, "", "a failed startup must not write protocol garbage to stdout")


if __name__ == "__main__":
    unittest.main()
