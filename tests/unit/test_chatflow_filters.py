"""Unit tests for the chatflow filter policy exposed by ``mcp_flowise.utils``."""

from __future__ import annotations

import unittest

from mcp_flowise.config import FilterPolicy
from mcp_flowise.utils import filter_chatflows


class TestFilterChatflows(unittest.TestCase):
    """``filter_chatflows`` applies the configured policy to a catalog."""

    chatflows = [
        {"id": "chatflow1", "name": "First Chatflow"},
        {"id": "chatflow2", "name": "Second Chatflow"},
        {"id": "chatflow3", "name": "Third Chatflow"},
    ]

    def policy(self, **env) -> FilterPolicy:
        from mcp_flowise.config import load_config

        return load_config(env, load_env_file=False).policy

    def test_no_filters(self):
        self.assertEqual(filter_chatflows(self.chatflows, self.policy()), self.chatflows)

    def test_whitelist_id_filter(self):
        filtered = filter_chatflows(self.chatflows, self.policy(FLOWISE_WHITELIST_ID="chatflow1,chatflow3"))
        self.assertEqual([cf["id"] for cf in filtered], ["chatflow1", "chatflow3"])

    def test_blacklist_id_filter(self):
        filtered = filter_chatflows(self.chatflows, self.policy(FLOWISE_BLACKLIST_ID="chatflow2"))
        self.assertEqual([cf["id"] for cf in filtered], ["chatflow1", "chatflow3"])

    def test_whitelist_name_regex_filter(self):
        filtered = filter_chatflows(self.chatflows, self.policy(FLOWISE_WHITELIST_NAME_REGEX=".*First.*"))
        self.assertEqual([cf["id"] for cf in filtered], ["chatflow1"])

    def test_blacklist_name_regex_filter(self):
        filtered = filter_chatflows(self.chatflows, self.policy(FLOWISE_BLACKLIST_NAME_REGEX=".*Second.*"))
        self.assertEqual([cf["id"] for cf in filtered], ["chatflow1", "chatflow3"])

    def test_whitelist_takes_precedence_over_blacklist(self):
        filtered = filter_chatflows(
            self.chatflows,
            self.policy(FLOWISE_WHITELIST_ID="chatflow1", FLOWISE_BLACKLIST_NAME_REGEX=".*First.*"),
        )
        self.assertEqual([cf["id"] for cf in filtered], ["chatflow1"])

    def test_legacy_env_names_are_honoured(self):
        filtered = filter_chatflows(self.chatflows, self.policy(FLOWISE_CHATFLOW_WHITELIST="chatflow2"))
        self.assertEqual([cf["id"] for cf in filtered], ["chatflow2"])


if __name__ == "__main__":
    unittest.main()
