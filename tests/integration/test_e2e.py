import os
import re
import time
import unittest
import tempfile
import json
import shutil
from unittest.mock import patch, MagicMock

# Import the core script to execute main E2E runs
from scripts import agent_sync

class TestEndToEnd(unittest.TestCase):

    def setUp(self):
        # Create a temp directory representing the workspace root
        self.workspace_dir = tempfile.TemporaryDirectory()
        self.repo_root = self.workspace_dir.name
        
        # Create folder structure
        self.config_dir = os.path.join(self.repo_root, "config")
        self.vault_path = os.path.join(self.repo_root, "mock_vault")
        self.agent_dir = os.path.join(self.vault_path, "_System", "Agent")
        
        os.makedirs(self.config_dir, exist_ok=True)
        os.makedirs(self.agent_dir, exist_ok=True)

        # Config mock
        self.config = {
            "vault": {
                "path": self.vault_path,
                "agent_dir": "_System/Agent"
            },
            "security": {
                "sandbox_exec_enabled": False
            },
            "notifications": {
                "enabled": False
            },
            "execution": {
                "default_provider": "agy",
                "default_model": "gemini-1.5-flash",
                "pro_model": "gemini-1.5-pro"
            }
        }

    def tearDown(self):
        self.workspace_dir.cleanup()

    @patch("scripts.agent_sync.load_config")
    @patch("scripts.agent_sync.route_task")
    @patch("scripts.providers.agy.AgyProvider.execute")
    def test_e2e_simple_workflow(self, mock_execute, mock_route, mock_load_config):
        # Mock load_config to return our mock config dictionary
        mock_load_config.return_value = self.config
        
        # Setup mock routing and execution returns
        mock_route.return_value = {
            "complexity": "simple",
            "model_recommendation": "gemini-1.5-flash",
            "required_mcp_servers": []
        }
        mock_execute.return_value = "OAB simple execution successful."

        # Create note with inline task in the vault
        note_path = os.path.join(self.vault_path, "daily-note.md")
        note_content = (
            "# Daily Journal\n"
            "Some content before.\n"
            "/second-brain-task\n"
            "Create a Google Calendar entry for team meeting at 2 PM\n"
            "<end-task>\n"
            "Some content after."
        )
        with open(note_path, "w", encoding="utf-8") as f:
            f.write(note_content)

        # Execute main bridge loop once
        with patch("sys.argv", ["agent_sync.py"]):
            agent_sync.main()

        # Read mutated note
        with open(note_path, "r", encoding="utf-8") as f:
            final_content = f.read()

        # Assert correct tag mutation
        self.assertNotIn("/second-brain-task\n", final_content)
        self.assertIn("/second-brain-task-completed", final_content)

        # Assert logs.md created and structured correctly
        logs_path = os.path.join(self.agent_dir, "logs.md")
        self.assertTrue(os.path.exists(logs_path))
        with open(logs_path, "r", encoding="utf-8") as f:
            logs_content = f.read()
        self.assertIn("Create a Google Calendar entry for team meeting at 2 PM", logs_content)
        self.assertIn("OAB simple execution successful.", logs_content)

if __name__ == "__main__":
    unittest.main()
