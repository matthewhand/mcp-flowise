import unittest
from unittest.mock import patch, Mock
import requests
from mcp_flowise.utils import flowise_predict, fetch_chatflows, filter_chatflows, normalize_tool_name
from mcp_flowise.utils import FlowiseAPIError

class TestUtils(unittest.TestCase):

    def setUp(self):
        self.discovery = patch('mcp_flowise.utils.fetch_chatflows', return_value=[{'id': 'valid_chatflow_id', 'name': 'Test'}])
        self.discovery.start()
        self.addCleanup(self.discovery.stop)

    @patch("requests.post")
    def test_flowise_predict_success(self, mock_post: Mock) -> None:
        """
        Test successful prediction response.
        """
        mock_post.return_value = Mock(
            status_code=200,
            text='{"text": "Mock Prediction"}',
        )
        response = flowise_predict("valid_chatflow_id", "What's AI?")
        self.assertEqual(response, '{"text": "Mock Prediction"}')  # Success case
        mock_post.assert_called_once()

    @patch("requests.post", side_effect=requests.Timeout)
    def test_flowise_predict_timeout(self, mock_post: Mock) -> None:
        """
        Test prediction handling of timeout.
        """
        with self.assertRaises(FlowiseAPIError):
            flowise_predict("valid_chatflow_id", "What's AI?")
        # self.assertIn("Timeout", response)  # Timeout-specific assertion

    @patch("requests.post")
    def test_flowise_predict_http_error(self, mock_post: Mock) -> None:
        """
        Test prediction handling of HTTP errors.
        """
        mock_post.return_value = Mock(
            status_code=500,
            raise_for_status=Mock(side_effect=requests.HTTPError("500 Error")),
            text='{"error": "500 Error"}',
        )
        with self.assertRaises(FlowiseAPIError):
            flowise_predict("valid_chatflow_id", "What's AI?")

    @patch("requests.get")
    def test_fetch_chatflows_success(self, mock_get: Mock) -> None:
        """
        Test successful fetching of chatflows.
        """
        mock_get.return_value = Mock(
            status_code=200,
            json=Mock(return_value=[{"id": "1", "name": "Chatflow 1"}, {"id": "2", "name": "Chatflow 2"}]),
        )
        chatflows = fetch_chatflows()
        self.assertEqual(len(chatflows), 2)
        self.assertEqual(chatflows[0]["id"], "1")
        self.assertEqual(chatflows[0]["name"], "Chatflow 1")
        mock_get.assert_called_once()

    @patch("requests.get", side_effect=requests.Timeout)
    def test_fetch_chatflows_timeout(self, mock_get: Mock) -> None:
        """
        Test handling of timeout when fetching chatflows.
        """
        with self.assertRaises(FlowiseAPIError):
            fetch_chatflows()

    @patch("requests.get")
    def test_fetch_chatflows_http_error(self, mock_get: Mock) -> None:
        """
        Test handling of HTTP errors when fetching chatflows.
        """
        mock_get.return_value = Mock(
            status_code=500,
            raise_for_status=Mock(side_effect=requests.HTTPError("500 Error")),
        )
        with self.assertRaises(FlowiseAPIError):
            fetch_chatflows()

    def test_filter_chatflows(self) -> None:
        """
        Test filtering of chatflows based on whitelist and blacklist criteria.
        """
        chatflows = [
            {"id": "1", "name": "Chatflow 1"},
            {"id": "2", "name": "Chatflow 2"},
            {"id": "3", "name": "Chatflow 3"},
        ]

        # Mock environment variables
        with patch.dict("os.environ", {
            "FLOWISE_WHITELIST_ID": "1,2",
            "FLOWISE_BLACKLIST_ID": "3",
            "FLOWISE_WHITELIST_NAME_REGEX": "",
            "FLOWISE_BLACKLIST_NAME_REGEX": "",
        }):
            filtered = filter_chatflows(chatflows)
            self.assertEqual(len(filtered), 2)
            self.assertEqual(filtered[0]["id"], "1")
            self.assertEqual(filtered[1]["id"], "2")

        # Mock environment variables for blacklist only
        with patch.dict("os.environ", {
            "FLOWISE_WHITELIST_ID": "",
            "FLOWISE_BLACKLIST_ID": "2",
            "FLOWISE_WHITELIST_NAME_REGEX": "",
            "FLOWISE_BLACKLIST_NAME_REGEX": "",
        }):
            filtered = filter_chatflows(chatflows)
            self.assertEqual(len(filtered), 2)
            self.assertEqual(filtered[0]["id"], "1")
            self.assertEqual(filtered[1]["id"], "3")

    def test_normalize_tool_name(self) -> None:
        """
        Test normalization of tool names.
        """
        self.assertEqual(normalize_tool_name("Tool Name"), "tool_name")
        self.assertEqual(normalize_tool_name("Tool-Name"), "tool_name")
        self.assertEqual(normalize_tool_name("Tool_Name"), "tool_name")
        self.assertEqual(normalize_tool_name("ToolName"), "toolname")
        self.assertEqual(normalize_tool_name(""), "unknown_tool")
        self.assertEqual(normalize_tool_name(None), "unknown_tool")

if __name__ == "__main__":
    unittest.main()
