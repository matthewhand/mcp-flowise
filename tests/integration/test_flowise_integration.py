"""Live-compatibility checks against a real Flowise deployment.

These tests are **skipped unless** ``FLOWISE_API_ENDPOINT`` and
``FLOWISE_API_KEY`` point at a real Flowise instance that the operator has
explicitly provided. They are the remaining "live gate": everything else in the
suite runs against the task-owned mock and proves protocol/dispatch behaviour,
not Flowise compatibility.

Run them with::

    FLOWISE_API_ENDPOINT=http://host:3000 FLOWISE_API_KEY=... pytest tests/integration/test_flowise_integration.py
"""

from __future__ import annotations

import os
import unittest

from mcp_flowise.config import load_config
from mcp_flowise.errors import FlowiseError
from mcp_flowise.utils import fetch_chatflows, flowise_predict

LIVE = bool(os.getenv("FLOWISE_API_ENDPOINT") and os.getenv("FLOWISE_API_KEY"))
SKIP_REASON = "Set FLOWISE_API_ENDPOINT and FLOWISE_API_KEY to run live Flowise compatibility tests."


@unittest.skipUnless(LIVE, SKIP_REASON)
class TestLiveFlowise(unittest.TestCase):
    def test_discovery_returns_a_catalog(self):
        chatflows = fetch_chatflows()
        self.assertIsInstance(chatflows, list)
        for chatflow in chatflows:
            self.assertIn("id", chatflow)
            self.assertIn("name", chatflow)

    def test_prediction_returns_json_fields(self):
        chatflows = fetch_chatflows()
        target = os.getenv("FLOWISE_CHATFLOW_ID")
        if target:
            known = {cf["id"] for cf in chatflows}
            if target not in known:
                self.skipTest(f"FLOWISE_CHATFLOW_ID {target} is not exposed under the current filter policy.")
        elif not chatflows:
            self.skipTest("No chatflow is exposed; nothing to predict against.")
        else:
            target = chatflows[0]["id"]

        payload = flowise_predict(target, "Reply with the single word: ok")
        self.assertIsInstance(payload, dict)
        self.assertTrue(payload, "Flowise returned an empty prediction payload")


@unittest.skipUnless(LIVE, SKIP_REASON)
class TestLiveConfiguration(unittest.TestCase):
    def test_configuration_of_this_deployment_is_valid(self):
        config = load_config()
        self.assertIn(config.mode, {"simple", "lowlevel"})
        self.assertTrue(config.endpoint.startswith(("http://", "https://")))

    def test_api_errors_are_typed(self):
        try:
            fetch_chatflows()
        except FlowiseError as exc:
            self.assertIn(exc.code, {"discovery_failed"})
        else:  # pragma: no cover - only on a healthy deployment
            pass


if __name__ == "__main__":
    unittest.main()
