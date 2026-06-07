import os
import re
import time
import unittest
import tempfile
import json
import shutil
from unittest.mock import patch

# Import the code to test
from scripts import agent_sync

class TestAgentSync(unittest.TestCase):

    def setUp(self):
        # Create a temporary directory for file system mocks
        self.test_dir = tempfile.TemporaryDirectory()
        self.vault_path = self.test_dir.name
        self.agent_dir = os.path.join(self.vault_path, "_System", "Agent")
        os.makedirs(self.agent_dir, exist_ok=True)

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
        agent_sync.process_file(note_path)

        # Assert no modifications
        with open(note_path, "r", encoding="utf-8") as f:
            self.assertEqual(f.read(), note_content)

    def test_process_file_with_task_mutation(self):
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
        agent_sync.process_file(note_path)

        # Read back content
        with open(note_path, "r", encoding="utf-8") as f:
            final_content = f.read()

        # Assert correct state transitions
        self.assertIsNone(re.search(r"/second-brain-task(?![a-zA-Z0-9_-])", final_content))
        self.assertNotIn("/second-brain-task-running", final_content)
        self.assertIn("/second-brain-task-completed", final_content)
        self.assertIn("Create a Google Calendar entry for dentist tomorrow at 10 AM", final_content)

    def test_process_file_multiline_task(self):
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
        agent_sync.process_file(note_path)

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
        # Let's set last scan to 10 seconds ago, and touch file to now
        last_scan = time.time() - 10.0
        agent_sync.set_last_scan_time(self.agent_dir, last_scan)
        
        # Touch file to current time
        os.utime(note_path, (time.time(), time.time()))

        # Run scan
        agent_sync.scan_vault(self.vault_path, "_System/Agent")

        # Assert process_file was called for the modified note
        mock_process_file.assert_called_once_with(note_path)

if __name__ == "__main__":
    unittest.main()
