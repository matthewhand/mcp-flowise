"""
Integration tests for Flowise MCP.
These tests will run conditionally if the required environment variables are configured.
"""

import os
import unittest
from mcp_flowise.utils import fetch_chatflows, flowise_predict


@unittest.skipUnless(
    os.getenv("FLOWISE_LIVE_TESTS") == "1" and os.getenv("FLOWISE_TEST_CHATFLOW_ID"),
    "Live checks require explicit opt-in and a harmless FLOWISE_TEST_CHATFLOW_ID",
)
class IntegrationTests(unittest.TestCase):
    """
    Integration tests for Flowise MCP.
    """

    @unittest.skipUnless(
        os.getenv("FLOWISE_API_KEY") and os.getenv("FLOWISE_API_ENDPOINT"),
        "FLOWISE_API_KEY and FLOWISE_API_ENDPOINT must be set for integration tests.",
    )
    def test_tool_discovery_in_lowlevel_mode(self):
        """
        Test tool discovery in low-level mode by fetching tools from the Flowise server.
        """
        chatflows = fetch_chatflows()
        self.assertGreater(len(chatflows), 0, "No chatflows discovered. Ensure the Flowise server is configured correctly.")
        print(f"Discovered chatflows: {[cf['name'] for cf in chatflows]}")

    @unittest.skipUnless(
        os.getenv("FLOWISE_API_KEY") and os.getenv("FLOWISE_API_ENDPOINT"),
        "FLOWISE_API_KEY and FLOWISE_API_ENDPOINT must be set for tool tests.",
    )
    def test_call_specific_tool(self):
        """
        Test calling a specific tool if available on the Flowise server.
        """
        chatflows = fetch_chatflows()
        if not chatflows:
            self.skipTest("No chatflows discovered on the server. Skipping tool test.")

        # Exercise only the explicitly selected harmless flow.
        specific_chatflow_id = os.environ["FLOWISE_TEST_CHATFLOW_ID"]
        chatflow = next((cf for cf in chatflows if cf["id"] == specific_chatflow_id), None)
        self.assertIsNotNone(chatflow, "Selected test chatflow was not discovered.")

        tool_name = chatflow.get("name")
        print(f"Testing tool: {tool_name} with ID: {chatflow['id']}")

        # Simulate tool call
        result = self.simulate_tool_call(tool_name, chatflow["id"], "Tell me a fun fact.")
        self.assertTrue(
            result.strip(),
            f"Unexpected empty response from tool {tool_name}: {result}"
        )

    def simulate_tool_call(self, tool_name, chatflow_id, question):
        """
        Simulates a tool call by directly using the flowise_predict function.

        Args:
            tool_name (str): The name of the tool.
            chatflow_id (str): The ID of the chatflow/tool.
            question (str): The question to ask.

        Returns:
            str: The response from the tool.
        """
        return flowise_predict(chatflow_id, question)


if __name__ == "__main__":
    unittest.main()
