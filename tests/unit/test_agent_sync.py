import os
import re
import time
import unittest
import tempfile
import json
import shutil
from unittest.mock import patch, MagicMock

# Import the code to test
from scripts import agent_sync

class TestAgentSync(unittest.TestCase):

    def setUp(self):
        # Create a temporary directory for file system mocks
        self.test_dir = tempfile.TemporaryDirectory()
        self.vault_path = self.test_dir.name
        self.agent_dir = os.path.join(self.vault_path, "_System", "Agent")
        os.makedirs(self.agent_dir, exist_ok=True)

        # Mock configuration
        self.config = {
            "vault": {
                "path": self.vault_path,
                "agent_dir": "_System/Agent"
            },
            "security": {
                "sandbox_exec_enabled": False
            },
            "execution": {
                "default_model": "gemini-1.5-flash"
            }
        }

    def tearDown(self):
        # Cleanup temporary files
        self.test_dir.cleanup()

    def test_get_set_last_scan_time(self):
        # Initially should return 0.0
        self.assertEqual(agent_sync.get_last_scan_time(self.agent_dir), 0.0)

        # Set scan time
        test_time = 123456789.0
        agent_sync.set_last_scan_time(self.agent_dir, test_time)

        # Read back
        self.assertEqual(agent_sync.get_last_scan_time(self.agent_dir), test_time)

    def test_process_file_no_tasks(self):
        # Create note without tasks
        note_path = os.path.join(self.vault_path, "note.md")
        note_content = "This is a simple note with no agent tasks."
        with open(note_path, "w", encoding="utf-8") as f:
            f.write(note_content)

        # Process note
        agent_sync.process_file(note_path, self.config)

        # Assert no modifications
        with open(note_path, "r", encoding="utf-8") as f:
            self.assertEqual(f.read(), note_content)

    @patch("scripts.agent_sync.send_notification_alert")
    @patch("scripts.agent_sync.route_task")
    @patch("scripts.providers.agy.AgyProvider.execute")
    def test_process_file_with_task_mutation(self, mock_execute, mock_route, mock_alert):
        # Mock successful provider and route runs
        mock_route.return_value = {"complexity": "simple", "model_recommendation": "gemini-1.5-flash", "required_mcp_servers": []}
        mock_execute.return_value = "Mocked execution output"

        # Create note with a single-line task
        note_path = os.path.join(self.vault_path, "note.md")
        note_content = (
            "Start of note\n"
            "/second-brain-task\n"
            "Create a Google Calendar entry for dentist tomorrow at 10 AM\n"
            "<end-task>\n"
            "End of note"
        )
        with open(note_path, "w", encoding="utf-8") as f:
            f.write(note_content)

        # Process note
        agent_sync.process_file(note_path, self.config)

        # Read back content
        with open(note_path, "r", encoding="utf-8") as f:
            final_content = f.read()

        # Assert correct state transitions
        self.assertIsNone(re.search(r"/second-brain-task(?![a-zA-Z0-9_-])", final_content))
        self.assertNotIn("/second-brain-task-running", final_content)
        self.assertIn("/second-brain-task-completed", final_content)
        self.assertIn("Create a Google Calendar entry for dentist tomorrow at 10 AM", final_content)

        # Assert logs.md was created and populated
        logs_path = os.path.join(self.agent_dir, "logs.md")
        self.assertTrue(os.path.exists(logs_path))
        with open(logs_path, "r", encoding="utf-8") as f:
            logs_content = f.read()
        self.assertIn("Create a Google Calendar entry for dentist tomorrow at 10 AM", logs_content)
        self.assertIn("Mocked execution output", logs_content)

        # Assert notification alerts were triggered
        self.assertTrue(mock_alert.called)

    @patch("scripts.agent_sync.send_notification_alert")
    @patch("scripts.agent_sync.route_task")
    @patch("scripts.providers.agy.AgyProvider.execute")
    def test_process_file_multiline_task(self, mock_execute, mock_route, mock_alert):
        # Mock successful provider and route runs
        mock_route.return_value = {"complexity": "simple", "model_recommendation": "gemini-1.5-flash", "required_mcp_servers": []}
        mock_execute.return_value = "Mocked execution output"

        # Create note with a multi-line task
        note_path = os.path.join(self.vault_path, "note.md")
        note_content = (
            "/second-brain-task\n"
            "Line 1 of task instructions\n"
            "Line 2 of task instructions\n"
            "<end-task>"
        )
        with open(note_path, "w", encoding="utf-8") as f:
            f.write(note_content)

        # Process note
        agent_sync.process_file(note_path, self.config)

        # Read back content
        with open(note_path, "r", encoding="utf-8") as f:
            final_content = f.read()

        # Assert mutability
        self.assertIn("/second-brain-task-completed", final_content)
        self.assertIn("Line 1 of task instructions", final_content)
        self.assertIn("Line 2 of task instructions", final_content)

    @patch("scripts.agent_sync.process_file")
    def test_scan_vault_finds_modified_files(self, mock_process_file):
        # Create note
        note_path = os.path.join(self.vault_path, "task.md")
        with open(note_path, "w", encoding="utf-8") as f:
            f.write("/second-brain-task\nTest\n<end-task>")

        # Force state to be older than file modification time
        last_scan = time.time() - 10.0
        agent_sync.set_last_scan_time(self.agent_dir, last_scan)
        
        # Touch file to current time
        os.utime(note_path, (time.time(), time.time()))

        # Run scan
        agent_sync.scan_vault(self.vault_path, "_System/Agent", self.config)

        # Assert process_file was called for the modified note
        mock_process_file.assert_called_once_with(note_path, self.config)

    @patch("scripts.agent_sync.send_notification_alert")
    def test_process_file_requires_approval_due_to_write_files(self, mock_alert):
        # Create note with write_files in YAML frontmatter
        note_path = os.path.join(self.vault_path, "write-task.md")
        note_content = (
            "---\n"
            "write_files:\n"
            "  - Projects/website.md\n"
            "---\n"
            "/second-brain-task\n"
            "Modify the main title\n"
            "<end-task>"
        )
        with open(note_path, "w", encoding="utf-8") as f:
            f.write(note_content)

        # Run process
        agent_sync.process_file(note_path, self.config)

        # Read back
        with open(note_path, "r", encoding="utf-8") as f:
            final_content = f.read()

        # Assert task is pending approval and UI is appended
        self.assertIn("/second-brain-task-pending-approval", final_content)
        self.assertIn("[Approval Required]", final_content)
        self.assertIn("- [ ] Approve Task", final_content)
        mock_alert.assert_called_once()

    @patch("scripts.agent_sync.send_notification_alert")
    def test_process_file_requires_approval_due_to_dangerous_keywords(self, mock_alert):
        # Create note with dangerous keyword in prompt
        note_path = os.path.join(self.vault_path, "dangerous-task.md")
        note_content = (
            "/second-brain-task\n"
            "delete all file under Projects/tmp\n"
            "<end-task>"
        )
        with open(note_path, "w", encoding="utf-8") as f:
            f.write(note_content)

        # Run process
        agent_sync.process_file(note_path, self.config)

        # Read back
        with open(note_path, "r", encoding="utf-8") as f:
            final_content = f.read()

        # Assert task is pending approval
        self.assertIn("/second-brain-task-pending-approval", final_content)
        self.assertIn("- [ ] Approve Task", final_content)

    @patch("scripts.agent_sync.send_notification_alert")
    @patch("scripts.agent_sync.execute_task_pipeline")
    def test_process_file_approved_resumes(self, mock_pipeline, mock_alert):
        # Create note already in pending approval state and checked Approved
        note_path = os.path.join(self.vault_path, "approved-task.md")
        note_content = (
            "/second-brain-task-pending-approval\n"
            "Create a calendar event\n"
            "<end-task>\n\n"
            "### [Approval Required] Run Task\n"
            "- [x] Approve Task\n"
            "- [ ] Reject Task\n"
        )
        with open(note_path, "w", encoding="utf-8") as f:
            f.write(note_content)

        # Run process
        agent_sync.process_file(note_path, self.config)

        # Read back
        with open(note_path, "r", encoding="utf-8") as f:
            final_content = f.read()

        # Assert approval UI is stripped
        self.assertNotIn("### [Approval Required]", final_content)
        self.assertNotIn("- [x] Approve Task", final_content)
        
        # Assert pipeline was executed with running state tag
        mock_pipeline.assert_called_once_with(note_path, "/second-brain-task-running\nCreate a calendar event\n<end-task>", "Create a calendar event", self.config)

    @patch("scripts.agent_sync.send_notification_alert")
    @patch("scripts.agent_sync.execute_task_pipeline")
    def test_process_file_rejected_fails(self, mock_pipeline, mock_alert):
        # Create note already in pending approval state and checked Rejected
        note_path = os.path.join(self.vault_path, "rejected-task.md")
        note_content = (
            "/second-brain-task-pending-approval\n"
            "Create a calendar event\n"
            "<end-task>\n\n"
            "### [Approval Required] Run Task\n"
            "- [ ] Approve Task\n"
            "- [x] Reject Task\n"
        )
        with open(note_path, "w", encoding="utf-8") as f:
            f.write(note_content)

        # Run process
        agent_sync.process_file(note_path, self.config)

        # Read back
        with open(note_path, "r", encoding="utf-8") as f:
            final_content = f.read()

        # Assert tag mutated to failed and error log appended
        self.assertIn("/second-brain-task-failed", final_content)
        self.assertIn("Task execution rejected by user.", final_content)
        self.assertNotIn("### [Approval Required]", final_content)
        
        # Assert pipeline was NEVER executed
        mock_pipeline.assert_not_called()

if __name__ == "__main__":
    unittest.main()
