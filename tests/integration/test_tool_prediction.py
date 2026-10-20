import unittest
import os
from mcp_flowise.utils import flowise_predict

@unittest.skipUnless(
    os.getenv("FLOWISE_LIVE_TESTS") == "1" and os.getenv("FLOWISE_TEST_CHATFLOW_ID"),
    "Live checks require explicit opt-in and a harmless FLOWISE_TEST_CHATFLOW_ID",
)
class TestToolPrediction(unittest.TestCase):
    """
    Integration test for predicting results from a Flowise chatflow.
    """

    def test_tool_prediction(self):
        """
        Test the prediction function for a Flowise chatflow.
        """
        chatflow_id = os.environ["FLOWISE_TEST_CHATFLOW_ID"]
        
        question = "What is the weather like today?"
        print(f"Using chatflow_id: {chatflow_id}")
        
        # Make a prediction
        result = flowise_predict(chatflow_id, question)
        
        # Validate the response
        self.assertIsInstance(result, str)
        self.assertNotEqual(result.strip(), "", "Prediction result should not be empty.")
        print(f"Prediction result: {result}")

if __name__ == "__main__":
    unittest.main()
