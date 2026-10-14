"""Unit tests for mcp_flowise.config (validation + filter policy)."""

from __future__ import annotations

import unittest

from mcp_flowise.config import (
    DEFAULT_MODE,
    MODE_LOWLEVEL,
    MODE_SIMPLE,
    FilterPolicy,
    load_config,
    normalize_id,
    validate_question,
)
from mcp_flowise.errors import ConfigError


def load(env):
    return load_config(env, load_env_file=False)


class TestEndpointValidation(unittest.TestCase):
    def test_default_endpoint(self):
        self.assertEqual(load({}).endpoint, "http://localhost:3000")

    def test_trailing_slash_and_whitespace_are_trimmed(self):
        config = load({"FLOWISE_API_ENDPOINT": "  https://flowise.example.com/  "})
        self.assertEqual(config.endpoint, "https://flowise.example.com")

    def test_base_path_is_preserved(self):
        config = load({"FLOWISE_API_ENDPOINT": "https://example.com/flowise/"})
        self.assertEqual(config.endpoint, "https://example.com/flowise")

    def test_rejects_non_http_scheme(self):
        with self.assertRaises(ConfigError):
            load({"FLOWISE_API_ENDPOINT": "ftp://example.com"})

    def test_rejects_missing_host(self):
        with self.assertRaises(ConfigError):
            load({"FLOWISE_API_ENDPOINT": "http:///chatflows"})

    def test_rejects_query_string(self):
        with self.assertRaises(ConfigError):
            load({"FLOWISE_API_ENDPOINT": "http://example.com?token=abc"})


class TestIdValidation(unittest.TestCase):
    def test_trims_whitespace(self):
        self.assertEqual(normalize_id("  abc123  "), "abc123")

    def test_blank_is_none(self):
        self.assertIsNone(normalize_id("   "))
        self.assertIsNone(normalize_id(""))

    def test_rejects_path_traversal(self):
        with self.assertRaises(ConfigError):
            normalize_id("../../etc/passwd")

    def test_rejects_slash_and_control_characters(self):
        for value in ("a/b", "a\\b", "a b", "a\nb"):
            with self.subTest(value=value), self.assertRaises(ConfigError):
                normalize_id(value)

    def test_allows_uuid_like_ids(self):
        self.assertEqual(normalize_id("f0b7c2e0-1111-2222-3333-444455556666"), "f0b7c2e0-1111-2222-3333-444455556666")


class TestModeSelection(unittest.TestCase):
    def test_default_mode_is_simple(self):
        self.assertEqual(DEFAULT_MODE, MODE_SIMPLE)
        self.assertEqual(load({}).mode, MODE_SIMPLE)

    def test_simple_mode_flag_true(self):
        self.assertEqual(load({"FLOWISE_SIMPLE_MODE": "true"}).mode, MODE_SIMPLE)

    def test_simple_mode_flag_false_selects_lowlevel(self):
        self.assertEqual(load({"FLOWISE_SIMPLE_MODE": "false"}).mode, MODE_LOWLEVEL)

    def test_explicit_flowise_mode_wins(self):
        config = load({"FLOWISE_MODE": "lowlevel", "FLOWISE_SIMPLE_MODE": "true"})
        self.assertEqual(config.mode, MODE_LOWLEVEL)

    def test_flowise_mode_aliases(self):
        self.assertEqual(load({"FLOWISE_MODE": "FastMCP"}).mode, MODE_SIMPLE)
        self.assertEqual(load({"FLOWISE_MODE": "dynamic"}).mode, MODE_LOWLEVEL)

    def test_invalid_values_rejected(self):
        with self.assertRaises(ConfigError):
            load({"FLOWISE_SIMPLE_MODE": "sometimes"})
        with self.assertRaises(ConfigError):
            load({"FLOWISE_MODE": "hybrid"})


class TestTargetValidation(unittest.TestCase):
    def test_single_chatflow_is_allowed(self):
        config = load({"FLOWISE_CHATFLOW_ID": " abc123 "})
        self.assertEqual(config.chatflow_id, "abc123")

    def test_conflicting_targets_rejected(self):
        with self.assertRaises(ConfigError) as ctx:
            load({"FLOWISE_CHATFLOW_ID": "abc", "FLOWISE_ASSISTANT_ID": "def"})
        self.assertIn("conflicting targets", ctx.exception.message)

    def test_assistant_support_is_rejected_as_unimplemented(self):
        with self.assertRaises(ConfigError) as ctx:
            load({"FLOWISE_ASSISTANT_ID": "def"})
        self.assertIn("not implemented", ctx.exception.message)

    def test_blank_targets_are_ignored(self):
        config = load({"FLOWISE_CHATFLOW_ID": "  ", "FLOWISE_ASSISTANT_ID": ""})
        self.assertIsNone(config.chatflow_id)


class TestTimeoutAndDescriptions(unittest.TestCase):
    def test_default_timeout(self):
        self.assertEqual(load({}).timeout, 30.0)

    def test_timeout_parsed(self):
        self.assertEqual(load({"FLOWISE_API_TIMEOUT": "2.5"}).timeout, 2.5)

    def test_invalid_timeouts_rejected(self):
        for value in ("0", "-1", "abc", "9999"):
            with self.subTest(value=value), self.assertRaises(ConfigError):
                load({"FLOWISE_API_TIMEOUT": value})

    def test_descriptions_parsed(self):
        config = load({"FLOWISE_CHATFLOW_DESCRIPTIONS": "abc:First, def : Second "})
        self.assertEqual(config.descriptions, {"abc": "First", "def": "Second"})

    def test_malformed_descriptions_are_skipped(self):
        config = load({"FLOWISE_CHATFLOW_DESCRIPTIONS": "abc:First,broken,:::"})
        self.assertEqual(config.descriptions, {"abc": "First"})


class TestRegexValidation(unittest.TestCase):
    def test_invalid_regex_rejected_before_network(self):
        with self.assertRaises(ConfigError):
            load({"FLOWISE_BLACKLIST_NAME_REGEX": "(unclosed"})


class TestFilterPolicyParsing(unittest.TestCase):
    def test_ids_are_trimmed_and_blank_entries_dropped(self):
        policy = load({"FLOWISE_WHITELIST_ID": " a , ,b "}).policy
        self.assertEqual(policy.whitelist_ids, frozenset({"a", "b"}))

    def test_legacy_names_are_used_when_canonical_is_absent(self):
        policy = load({"FLOWISE_CHATFLOW_WHITELIST": "b", "FLOWISE_CHATFLOW_BLACKLIST": "y"}).policy
        self.assertEqual(policy.whitelist_ids, frozenset({"b"}))
        self.assertEqual(policy.blacklist_ids, frozenset({"y"}))
        self.assertEqual(
            policy.legacy_env_names, ("FLOWISE_CHATFLOW_WHITELIST", "FLOWISE_CHATFLOW_BLACKLIST")
        )
        self.assertEqual(policy.ignored_legacy_env_names, ())

    def test_canonical_name_wins_over_legacy_name(self):
        policy = load(
            {
                "FLOWISE_WHITELIST_ID": "a",
                "FLOWISE_CHATFLOW_WHITELIST": "b",
                "FLOWISE_BLACKLIST_ID": "x",
                "FLOWISE_CHATFLOW_BLACKLIST": "y",
            }
        ).policy
        self.assertEqual(policy.whitelist_ids, frozenset({"a"}))
        self.assertEqual(policy.blacklist_ids, frozenset({"x"}))
        self.assertEqual(
            policy.ignored_legacy_env_names, ("FLOWISE_CHATFLOW_WHITELIST", "FLOWISE_CHATFLOW_BLACKLIST")
        )

    def test_blank_canonical_falls_back_to_legacy(self):
        policy = load({"FLOWISE_WHITELIST_ID": "  ", "FLOWISE_CHATFLOW_WHITELIST": "b"}).policy
        self.assertEqual(policy.whitelist_ids, frozenset({"b"}))

    def test_legacy_whitelist_cannot_widen_an_explicit_denial(self):
        """A leftover legacy whitelist cannot re-admit an ID the canonical blacklist denies."""
        policy = load(
            {
                "FLOWISE_WHITELIST_ID": "a",
                "FLOWISE_BLACKLIST_ID": "b",
                "FLOWISE_CHATFLOW_WHITELIST": "b",
            }
        ).policy
        self.assertTrue(policy.allows("a", "A"))
        self.assertFalse(policy.allows("b", "B"))
        self.assertFalse(policy.allows("c", "C"))

    def test_disabled_when_nothing_configured(self):
        self.assertFalse(load({}).policy.enabled)

    def test_summary_exposes_no_secrets(self):
        config = load({"FLOWISE_API_KEY": "super-secret", "FLOWISE_WHITELIST_ID": "a"})
        described = config.describe()
        self.assertNotIn("super-secret", str(described))
        self.assertTrue(described["policy"]["enabled"])


class TestQuestionValidation(unittest.TestCase):
    def test_accepts_normal_question(self):
        self.assertEqual(validate_question(" hello "), " hello ")

    def test_rejects_empty_and_non_string(self):
        for value in ("", "   ", None, 42, ["a"]):
            with self.subTest(value=value), self.assertRaises(ConfigError):
                validate_question(value)


class TestFilterPolicySemantics(unittest.TestCase):
    chatflows = [
        {"id": "1", "name": "Alpha"},
        {"id": "2", "name": "Beta"},
        {"id": "3", "name": "Gamma"},
    ]

    def policy(self, **env) -> FilterPolicy:
        return load(env).policy

    def test_no_filters_allows_all(self):
        policy = self.policy()
        self.assertEqual(len(policy.filter_chatflows(self.chatflows)), 3)

    def test_whitelist_by_id(self):
        policy = self.policy(FLOWISE_WHITELIST_ID="1,3")
        self.assertEqual([cf["id"] for cf in policy.filter_chatflows(self.chatflows)], ["1", "3"])

    def test_whitelist_by_name_regex(self):
        policy = self.policy(FLOWISE_WHITELIST_NAME_REGEX=".*Bet.*")
        self.assertEqual([cf["id"] for cf in policy.filter_chatflows(self.chatflows)], ["2"])

    def test_blacklist_by_id(self):
        policy = self.policy(FLOWISE_BLACKLIST_ID="2")
        self.assertEqual([cf["id"] for cf in policy.filter_chatflows(self.chatflows)], ["1", "3"])

    def test_blacklist_by_name_regex(self):
        policy = self.policy(FLOWISE_BLACKLIST_NAME_REGEX=".*amm.*")
        self.assertEqual([cf["id"] for cf in policy.filter_chatflows(self.chatflows)], ["1", "2"])

    def test_whitelist_takes_precedence_over_blacklist(self):
        policy = self.policy(FLOWISE_WHITELIST_ID="1", FLOWISE_BLACKLIST_NAME_REGEX=".*Alpha.*")
        self.assertEqual([cf["id"] for cf in policy.filter_chatflows(self.chatflows)], ["1"])
        self.assertTrue(policy.allows("1", "Alpha"))

    def test_whitelist_present_drops_everything_else(self):
        policy = self.policy(FLOWISE_WHITELIST_ID="1")
        self.assertFalse(policy.allows("2", "Beta"))

    def test_missing_name_is_handled(self):
        policy = self.policy(FLOWISE_WHITELIST_NAME_REGEX=".*")
        self.assertEqual(policy.filter_chatflows([{"id": "9"}]), [{"id": "9", "name": ""}])
        policy = self.policy(FLOWISE_WHITELIST_NAME_REGEX="Alpha")
        self.assertEqual(policy.filter_chatflows([{"id": "9"}]), [])

    def test_needs_name_flag(self):
        self.assertTrue(self.policy(FLOWISE_WHITELIST_NAME_REGEX="x").needs_name)
        self.assertFalse(self.policy(FLOWISE_WHITELIST_ID="1").needs_name)


if __name__ == "__main__":
    unittest.main()
