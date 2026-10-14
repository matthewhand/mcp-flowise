"""Container handshake: the built Docker image must speak MCP over stdio.

Skipped unless ``MCP_FLOWISE_DOCKER_IMAGE`` names a locally built image, e.g.::

    docker build -t mcp-flowise:test .
    $env:MCP_FLOWISE_DOCKER_IMAGE = "mcp-flowise:test"
    pytest tests/integration/test_container_handshake.py

This proves the *packaged* server works (correct entrypoint, correct PATH, no
start-up git download, clean stdout). It says nothing about compatibility with
a real Flowise deployment - the endpoint is the task-owned mock.
"""

from __future__ import annotations

import json
import os
import unittest

from mcp_stdio_harness import raw_protocol_exchange, run_to_completion

IMAGE = os.getenv("MCP_FLOWISE_DOCKER_IMAGE")
SKIP_REASON = "Set MCP_FLOWISE_DOCKER_IMAGE to a locally built image to run the container handshake."

HOST_BRIDGE = os.getenv("MCP_FLOWISE_DOCKER_HOST_BRIDGE", "host.docker.internal")

LIST_TOOLS = {"method": "tools/list"}
CALL_LIST = {"method": "tools/call", "params": {"name": "list_chatflows", "arguments": {}}}
CALL_PREDICTION = {
    "method": "tools/call",
    "params": {
        "name": "create_prediction",
        "arguments": {"question": "container hello", "chatflow_id": "cf-alpha"},
    },
}
CALL_DYNAMIC_TOOL = {
    "method": "tools/call",
    "params": {"name": "alpha_chatflow", "arguments": {"question": "container hello"}},
}


@unittest.skipUnless(IMAGE, SKIP_REASON)
class TestContainerHandshake(unittest.TestCase):
    def setUp(self):
        # The container reaches the mock through the Docker host bridge, so the
        # mock has to listen on all interfaces.
        from mock_flowise import MockFlowise

        self.mock = MockFlowise(host="0.0.0.0").start()
        self.addCleanup(self.mock.stop)
        self.mock.chatflows = [{"id": "cf-alpha", "name": "Alpha Chatflow"}]

    def run_exchange(self, requests, **extra_env):
        env = {
            "FLOWISE_API_ENDPOINT": f"http://{HOST_BRIDGE}:{self.mock.port}",
            "FLOWISE_API_KEY": "container-sentinel-key",
            "FLOWISE_API_TIMEOUT": "15",
        }
        env.update(extra_env)
        return raw_protocol_exchange(
            env,
            requests,
            timeout=90,
            command=["docker", "run", "--rm", "-i"]
            + [f"-e={key}={value}" for key, value in env.items()]
            + [IMAGE],
        )

    def test_simple_mode_handshake_and_call(self):
        exchange = self.run_exchange([LIST_TOOLS, CALL_LIST, CALL_PREDICTION], FLOWISE_SIMPLE_MODE="true")
        messages = [json.loads(line) for line in exchange.stdout_lines]
        self.assertTrue(messages, "the container produced no MCP output")
        self.assertEqual(messages[0]["result"]["serverInfo"]["name"], "FlowiseMCP-with-EnvAuth")

        tool_names = [tool["name"] for tool in messages[1]["result"]["tools"]]
        self.assertEqual(sorted(tool_names), ["create_prediction", "list_chatflows"])

        catalog = json.loads(messages[2]["result"]["content"][0]["text"])
        self.assertEqual([cf["id"] for cf in catalog], ["cf-alpha"])

        answer = json.loads(messages[3]["result"]["content"][0]["text"])
        self.assertEqual(answer["text"], "mock answer")
        self.assertFalse(messages[3]["result"]["isError"])

        self.assertNotIn("container-sentinel-key", exchange.stdout)
        self.assertNotIn("container-sentinel-key", exchange.stderr)
        self.assertNotIn("git clone", exchange.stderr)
        self.assertEqual(exchange.returncode, 0)

    def test_lowlevel_mode_handshake_and_call(self):
        exchange = self.run_exchange([LIST_TOOLS, CALL_DYNAMIC_TOOL], FLOWISE_SIMPLE_MODE="false")
        messages = [json.loads(line) for line in exchange.stdout_lines]
        tool_names = [tool["name"] for tool in messages[1]["result"]["tools"]]
        self.assertEqual(tool_names, ["alpha_chatflow"])
        answer = json.loads(messages[2]["result"]["content"][0]["text"])
        self.assertEqual(answer["text"], "mock answer")
        self.assertEqual(self.mock.prediction_calls, 1)

    def test_invalid_configuration_exits_nonzero_without_stdout_noise(self):
        env = {
            "FLOWISE_API_ENDPOINT": "not-a-url",
            "FLOWISE_API_KEY": "container-sentinel-key",
        }
        exchange = run_to_completion(
            env,
            timeout=90,
            command=["docker", "run", "--rm", "-i"]
            + [f"-e={key}={value}" for key, value in env.items()]
            + [IMAGE],
        )
        self.assertEqual(exchange.returncode, 1)
        self.assertIn("configuration error", exchange.stderr)
        self.assertEqual(exchange.stdout, "")


if __name__ == "__main__":
    unittest.main()
