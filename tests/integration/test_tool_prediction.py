"""Live prediction check against a real Flowise chatflow.

Skipped unless ``FLOWISE_CHATFLOW_ID`` and the Flowise connection settings are
present; see ``test_flowise_integration.py`` for the wider live gate.
"""

from __future__ import annotations

import os
import unittest

from mcp_flowise.utils import flowise_predict

SKIP_REASON = "FLOWISE_CHATFLOW_ID (plus FLOWISE_API_ENDPOINT/FLOWISE_API_KEY) must be set to run this test."


@unittest.skipUnless(
    os.getenv("FLOWISE_CHATFLOW_ID") and os.getenv("FLOWISE_API_ENDPOINT") and os.getenv("FLOWISE_API_KEY"),
    SKIP_REASON,
)
class TestLivePrediction(unittest.TestCase):
    def test_prediction_returns_a_json_object(self):
        payload = flowise_predict(os.environ["FLOWISE_CHATFLOW_ID"], "What is the weather like today?")
        self.assertIsInstance(payload, dict)
        self.assertTrue(payload, "Prediction result should not be empty.")


if __name__ == "__main__":
    unittest.main()
