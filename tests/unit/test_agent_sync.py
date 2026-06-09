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
            "- [ ] #agent Create a Google Calendar entry for dentist tomorrow at 10 AM\n"
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
        self.assertNotIn("- [ ] #agent Create a Google Calendar", final_content)
        self.assertNotIn("- [/] #agent Create a Google Calendar", final_content)
        self.assertIn("- [x] #agent Create a Google Calendar", final_content)

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

        # Create note with a task
        note_path = os.path.join(self.vault_path, "note.md")
        note_content = (
            "- [ ] #agent Line 1 of task instructions"
        )
        with open(note_path, "w", encoding="utf-8") as f:
            f.write(note_content)

        # Process note
        agent_sync.process_file(note_path, self.config)

        # Read back content
        with open(note_path, "r", encoding="utf-8") as f:
            final_content = f.read()

        # Assert mutability
        self.assertIn("- [x] #agent Line 1 of task instructions", final_content)

    @patch("scripts.agent_sync.process_file")
    def test_scan_vault_finds_modified_files(self, mock_process_file):
        # Create note
        note_path = os.path.join(self.vault_path, "task.md")
        with open(note_path, "w", encoding="utf-8") as f:
            f.write("- [ ] #agent Test")

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
            "- [ ] #agent Modify the main title"
        )
        with open(note_path, "w", encoding="utf-8") as f:
            f.write(note_content)

        # Run process
        agent_sync.process_file(note_path, self.config)

        # Read back
        with open(note_path, "r", encoding="utf-8") as f:
            final_content = f.read()

        # Assert task is pending approval and UI is appended
        self.assertIn("- [ ] #agent-pending-approval Modify the main title", final_content)
        self.assertIn("[Approval Required]", final_content)
        self.assertIn("- [ ] Approve Task", final_content)
        mock_alert.assert_called_once()

    @patch("scripts.agent_sync.send_notification_alert")
    def test_process_file_requires_approval_due_to_dangerous_keywords(self, mock_alert):
        # Create note with dangerous keyword in prompt
        note_path = os.path.join(self.vault_path, "dangerous-task.md")
        note_content = (
            "- [ ] #agent delete all file under Projects/tmp"
        )
        with open(note_path, "w", encoding="utf-8") as f:
            f.write(note_content)

        # Run process
        agent_sync.process_file(note_path, self.config)

        # Read back
        with open(note_path, "r", encoding="utf-8") as f:
            final_content = f.read()

        # Assert task is pending approval
        self.assertIn("- [ ] #agent-pending-approval delete all file under Projects/tmp", final_content)
        self.assertIn("- [ ] Approve Task", final_content)

    @patch("scripts.agent_sync.send_notification_alert")
    @patch("scripts.agent_sync.execute_task_pipeline")
    def test_process_file_approved_resumes(self, mock_pipeline, mock_alert):
        # Create note already in pending approval state and checked Approved
        note_path = os.path.join(self.vault_path, "approved-task.md")
        note_content = (
            "- [ ] #agent-pending-approval Create a calendar event\n\n"
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
        
        # Assert pipeline was executed with running state tag and progress logs
        mock_pipeline.assert_called_once_with(
            note_path, 
            "- [/] #agent Create a calendar event\n  * 🟢 Routing task...", 
            "Create a calendar event", 
            self.config,
            schedule_type=None,
            redirect_mode=None,
            redirect_target=None
        )

    @patch("scripts.agent_sync.send_notification_alert")
    @patch("scripts.agent_sync.execute_task_pipeline")
    def test_process_file_rejected_fails(self, mock_pipeline, mock_alert):
        # Create note already in pending approval state and checked Rejected
        note_path = os.path.join(self.vault_path, "rejected-task.md")
        note_content = (
            "- [ ] #agent-pending-approval Create a calendar event\n\n"
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
        self.assertIn("- [-] #agent Create a calendar event", final_content)
        self.assertIn("Task execution rejected by user.", final_content)
        self.assertNotIn("### [Approval Required]", final_content)
        
        # Assert pipeline was NEVER executed
        mock_pipeline.assert_not_called()

    def test_extract_wiki_links(self):
        prompt = "Use [[Summarize Note]] on [[2026-06-08#Journal]] with [[Target|Display Name]]"
        links = agent_sync.extract_wiki_links(prompt)
        self.assertEqual(links, ["Summarize Note", "2026-06-08", "Target"])

    def test_resolve_link_template(self):
        templates_dir = os.path.join(self.vault_path, "_System", "Agent", "Templates")
        os.makedirs(templates_dir, exist_ok=True)
        template_path = os.path.join(templates_dir, "Summarize Note.md")
        with open(template_path, "w", encoding="utf-8") as f:
            f.write("Template content for: {{title}}")

        res = agent_sync.resolve_link(self.vault_path, "_System/Agent", "Summarize Note")
        self.assertEqual(res["type"], "template")
        self.assertEqual(res["content"], "Template content for: {{title}}")

    def test_resolve_link_context(self):
        note_path = os.path.join(self.vault_path, "Subfolder", "Meeting Note.md")
        os.makedirs(os.path.dirname(note_path), exist_ok=True)
        with open(note_path, "w", encoding="utf-8") as f:
            f.write("Meeting notes details")

        res = agent_sync.resolve_link(self.vault_path, "_System/Agent", "Meeting Note")
        self.assertEqual(res["type"], "context")
        self.assertEqual(res["content"], "Meeting notes details")

    def test_resolve_and_interpolate_prompt(self):
        # Setup template and context
        templates_dir = os.path.join(self.vault_path, "_System", "Agent", "Templates")
        os.makedirs(templates_dir, exist_ok=True)
        template_path = os.path.join(templates_dir, "Summarize.md")
        with open(template_path, "w", encoding="utf-8") as f:
            f.write("Please summarize: {{title}}\nContent:\n{{content}}\nDate: {{date}}")

        context_path = os.path.join(self.vault_path, "Work", "Project Details.md")
        os.makedirs(os.path.dirname(context_path), exist_ok=True)
        with open(context_path, "w", encoding="utf-8") as f:
            f.write("Project description text.")

        prompt = "[[Summarize]] using [[Project Details]]"
        resolved = agent_sync.resolve_and_interpolate_prompt(prompt, self.vault_path, "_System/Agent")

        # Verify title, content, date interpolation
        self.assertIn("Please summarize: Project Details", resolved)
        self.assertIn("Content:\nProject description text.", resolved)
        current_date = time.strftime("%Y-%m-%d")
        self.assertIn(f"Date: {current_date}", resolved)

    def test_resolve_and_interpolate_prompt_no_template(self):
        # Setup context note only
        context_path = os.path.join(self.vault_path, "Daily", "Today.md")
        os.makedirs(os.path.dirname(context_path), exist_ok=True)
        with open(context_path, "w", encoding="utf-8") as f:
            f.write("Today I coded python.")

        prompt = "Review [[Today]] and note accomplishments on {{date}}"
        resolved = agent_sync.resolve_and_interpolate_prompt(prompt, self.vault_path, "_System/Agent")

        current_date = time.strftime("%Y-%m-%d")
        self.assertTrue(resolved.startswith(f"Review Today and note accomplishments on {current_date}"))
        self.assertIn("--- Context: Today ---", resolved)
        self.assertIn("Today I coded python.", resolved)

    @patch("scripts.agent_sync.send_notification_alert")
    @patch("scripts.agent_sync.route_task")
    @patch("scripts.providers.agy.AgyProvider.execute")
    def test_progress_logging_transitions(self, mock_execute, mock_route, mock_alert):
        mock_route.return_value = {
            "complexity": "simple",
            "model_recommendation": "gemini-1.5-flash",
            "required_mcp_servers": []
        }
        mock_execute.return_value = "Success"

        note_path = os.path.join(self.vault_path, "progress-test.md")
        with open(note_path, "w", encoding="utf-8") as f:
            f.write("- [ ] #agent Simple task")

        # Run watcher scan once
        agent_sync.process_file(note_path, self.config)

        # Read final note content
        with open(note_path, "r", encoding="utf-8") as f:
            final_content = f.read()

        # On success, progress log lines should be removed, and task set to [x] with the details block output
        expected_content = (
            "- [x] #agent Simple task\n"
            "  <details>\n"
            "  <summary>🤖 View Output</summary>\n\n"
            "  Success\n"
            "  </details>"
        )
        self.assertEqual(final_content.strip(), expected_content)

    @patch("scripts.agent_sync.send_notification_alert")
    @patch("scripts.agent_sync.route_task")
    @patch("scripts.providers.agy.AgyProvider.execute")
    def test_progress_logging_failure_retains_error(self, mock_execute, mock_route, mock_alert):
        mock_route.return_value = {
            "complexity": "simple",
            "model_recommendation": "gemini-1.5-flash",
            "required_mcp_servers": []
        }
        mock_execute.side_effect = Exception("Sandbox permission denied")

        note_path = os.path.join(self.vault_path, "progress-fail-test.md")
        with open(note_path, "w", encoding="utf-8") as f:
            f.write("- [ ] #agent Simple task")

        # Run watcher scan once
        agent_sync.process_file(note_path, self.config)

        # Read final note content
        with open(note_path, "r", encoding="utf-8") as f:
            final_content = f.read()

        # On failure, error bullet should be appended
        self.assertIn("- [-] #agent Simple task", final_content)
        self.assertIn("  * ❌ Error: Sandbox permission denied", final_content)

    @patch("scripts.agent_sync.send_notification_alert")
    @patch("scripts.agent_sync.route_task")
    @patch("scripts.providers.agy.AgyProvider.execute")
    def test_tag_at_end_of_line(self, mock_execute, mock_route, mock_alert):
        mock_route.return_value = {
            "complexity": "simple",
            "model_recommendation": "gemini-1.5-flash",
            "required_mcp_servers": []
        }
        mock_execute.return_value = "Success"

        note_path = os.path.join(self.vault_path, "tag-end-test.md")
        with open(note_path, "w", encoding="utf-8") as f:
            f.write("- [ ] Simple task at end #agent")

        # Run watcher scan once
        agent_sync.process_file(note_path, self.config)

        # Read final note content
        with open(note_path, "r", encoding="utf-8") as f:
            final_content = f.read()

        # Check prompt resolution (which strips #agent) and tag preservation (replaces - [ ] with - [x] and keeps #agent at end)
        self.assertIn("- [x] Simple task at end #agent", final_content)
        mock_execute.assert_called_once_with("Simple task at end", self.vault_path, "gemini-1.5-flash")

    def test_parse_redirection(self):
        # Test overwrite wiki-link
        p, mode, target = agent_sync.parse_redirection("Do something > [[Output Note]]")
        self.assertEqual(p, "Do something")
        self.assertEqual(mode, ">")
        self.assertEqual(target, "Output Note")

        # Test append file path
        p, mode, target = agent_sync.parse_redirection("Do something >> logs/output.md")
        self.assertEqual(p, "Do something")
        self.assertEqual(mode, ">>")
        self.assertEqual(target, "logs/output.md")

        # Test no redirection
        p, mode, target = agent_sync.parse_redirection("Do something without redirection")
        self.assertEqual(p, "Do something without redirection")
        self.assertIsNone(mode)
        self.assertIsNone(target)

    def test_parse_scheduling(self):
        # Test daily tag
        p, stype = agent_sync.parse_scheduling("Do something @daily")
        self.assertEqual(p, "Do something")
        self.assertEqual(stype, "daily")

        # Test hourly tag in middle
        p, stype = agent_sync.parse_scheduling("Do @hourly task now")
        self.assertEqual(p, "Do task now")
        self.assertEqual(stype, "hourly")

        # Test no scheduling
        p, stype = agent_sync.parse_scheduling("Do task normally")
        self.assertEqual(p, "Do task normally")
        self.assertIsNone(stype)

    def test_should_run_scheduled_task(self):
        # Case 1: No last run
        self.assertTrue(agent_sync.should_run_scheduled_task("daily", None))

        # Case 2: Elapsed time is small (skipped)
        last_run = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() - 3600))
        self.assertFalse(agent_sync.should_run_scheduled_task("daily", last_run))

        # Case 3: Elapsed time is large (should run)
        last_run_old = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() - 90000))
        self.assertTrue(agent_sync.should_run_scheduled_task("daily", last_run_old))

    @patch("scripts.agent_sync.send_notification_alert")
    @patch("scripts.agent_sync.route_task")
    @patch("scripts.providers.agy.AgyProvider.execute")
    def test_execute_task_pipeline_redirection(self, mock_execute, mock_route, mock_alert):
        mock_route.return_value = {"complexity": "simple", "model_recommendation": "gemini-1.5-flash", "required_mcp_servers": []}
        mock_execute.return_value = "Custom redirected output text"

        # Overwrite test
        note_path = os.path.join(self.vault_path, "task.md")
        running_block = "- [/] #agent Do task > [[TargetDoc]]\n  * 🟢 Routing task..."
        with open(note_path, "w", encoding="utf-8") as f:
            f.write(running_block)
        
        success = agent_sync.execute_task_pipeline(
            note_path,
            running_block,
            "Do task",
            self.config,
            redirect_mode=">",
            redirect_target="TargetDoc"
        )
        self.assertTrue(success)

        # Check redirected file content
        redirect_path = os.path.join(self.vault_path, "TargetDoc.md")
        self.assertTrue(os.path.exists(redirect_path))
        with open(redirect_path, "r", encoding="utf-8") as f:
            self.assertEqual(f.read(), "Custom redirected output text")

        # Check original file status mutation
        with open(note_path, "r", encoding="utf-8") as f:
            note_content = f.read()
        self.assertIn("- [x] #agent Do task > [[TargetDoc]]", note_content)
        self.assertIn("* 🟢 Output written to [[TargetDoc]]", note_content)
        self.assertNotIn("<details>", note_content)

    @patch("scripts.agent_sync.send_notification_alert")
    @patch("scripts.agent_sync.route_task")
    @patch("scripts.providers.agy.AgyProvider.execute")
    def test_process_file_trusted_bypass(self, mock_execute, mock_route, mock_alert):
        mock_route.return_value = {"complexity": "simple", "model_recommendation": "gemini-1.5-flash", "required_mcp_servers": []}
        mock_execute.return_value = "Git commit successful"

        # Note with a dangerous keyword but marked #trusted
        note_path = os.path.join(self.vault_path, "trusted-task.md")
        note_content = "- [ ] #agent git commit our changes #trusted"
        with open(note_path, "w", encoding="utf-8") as f:
            f.write(note_content)

        # Run process - should execute immediately without entering approval state
        agent_sync.process_file(note_path, self.config)

        with open(note_path, "r", encoding="utf-8") as f:
            final_content = f.read()

        self.assertIn("- [x] #agent git commit our changes #trusted", final_content)
        self.assertNotIn("### [Approval Required]", final_content)

    @patch("scripts.agent_sync.send_notification_alert")
    @patch("scripts.agent_sync.route_task")
    @patch("scripts.providers.agy.AgyProvider.execute")
    def test_process_file_recurring_scheduling(self, mock_execute, mock_route, mock_alert):
        mock_route.return_value = {"complexity": "simple", "model_recommendation": "gemini-1.5-flash", "required_mcp_servers": []}
        mock_execute.return_value = "Hourly task executed output"

        note_path = os.path.join(self.vault_path, "scheduled-task.md")
        
        # Scenario A: No last run bullet - executes
        note_content = "- [ ] #agent @hourly Do task"
        with open(note_path, "w", encoding="utf-8") as f:
            f.write(note_content)

        agent_sync.process_file(note_path, self.config)

        with open(note_path, "r", encoding="utf-8") as f:
            content_a = f.read()

        # Should remain unchecked (- [ ]) but have Last run line
        self.assertIn("- [ ] #agent @hourly Do task", content_a)
        self.assertIn("* Last run: ", content_a)
        self.assertIn("Hourly task executed output", content_a)

        # Scenario B: Last run was recent (skipped)
        mock_execute.reset_mock()
        agent_sync.process_file(note_path, self.config)
        mock_execute.assert_not_called()

if __name__ == "__main__":
    unittest.main()
