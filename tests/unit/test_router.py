import unittest
from unittest.mock import patch, MagicMock
from scripts import router

class TestRouter(unittest.TestCase):

    def setUp(self):
        self.config = {
            "execution": {
                "default_model": "gemini-1.5-flash",
                "pro_model": "gemini-1.5-pro"
            },
            "security": {
                "sandbox_exec_enabled": False
            }
        }

    def test_clean_json_output(self):
        # Test clean JSON
        raw_json = '{"a": 1}'
        self.assertEqual(router.clean_json_output(raw_json), '{"a": 1}')

        # Test wrapped in markdown code block
        raw_block = "```json\n{\"a\": 1}\n```"
        self.assertEqual(router.clean_json_output(raw_block), '{"a": 1}')

        # Test wrapped in code block without json syntax tag
        raw_block_no_tag = "```\n{\"a\": 1}\n```"
        self.assertEqual(router.clean_json_output(raw_block_no_tag), '{"a": 1}')

    @patch("scripts.providers.agy.AgyProvider.execute")
    def test_route_task_success(self, mock_execute):
        # Mock Router Subagent JSON output
        mock_execute.return_value = """```json
{
  "complexity": "complex",
  "model_recommendation": "gemini-1.5-pro",
  "required_mcp_servers": ["google-calendar"]
}
```"""
        metadata = router.route_task("Create complex UI", "/dummy/path", self.config)

        # Assert correct mapping and normalization
        self.assertEqual(metadata["complexity"], "complex")
        self.assertEqual(metadata["model_recommendation"], "gemini-1.5-pro")
        self.assertEqual(metadata["required_mcp_servers"], ["google-calendar"])

    @patch("scripts.providers.agy.AgyProvider.execute")
    def test_route_task_fallback_on_json_error(self, mock_execute):
        # Mock malformed response
        mock_execute.return_value = "This is not JSON at all!"
        metadata = router.route_task("Create calendar event", "/dummy/path", self.config)

        # Assert fallback defaults
        self.assertEqual(metadata["complexity"], "simple")
        self.assertEqual(metadata["model_recommendation"], "gemini-1.5-flash")
        self.assertEqual(metadata["required_mcp_servers"], [])

    @patch("scripts.providers.agy.AgyProvider.execute")
    def test_route_task_fallback_on_missing_keys(self, mock_execute):
        # Mock JSON response with missing keys
        mock_execute.return_value = '{"complexity": "simple"}'
        metadata = router.route_task("Create calendar event", "/dummy/path", self.config)

        # Assert fallback defaults
        self.assertEqual(metadata["model_recommendation"], "gemini-1.5-flash")
        self.assertEqual(metadata["required_mcp_servers"], [])

if __name__ == "__main__":
    unittest.main()
