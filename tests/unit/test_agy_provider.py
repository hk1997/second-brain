import os
import unittest
import tempfile
from unittest.mock import patch, MagicMock
from scripts.providers.agy import AgyProvider

class TestAgyProvider(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.TemporaryDirectory()
        self.workspace_path = self.test_dir.name
        self.provider = AgyProvider(sandbox_enabled=True)

    def tearDown(self):
        self.test_dir.cleanup()

    def test_generate_sandbox_profile(self):
        profile = self.provider._generate_sandbox_profile(self.workspace_path)
        
        # Assert crucial sandbox restrictions are present
        self.assertIn(self.workspace_path, profile)
        self.assertIn("network-outbound", profile)
        self.assertIn("system-socket", profile)
        self.assertIn("deny default", profile)

    @patch("subprocess.run")
    def test_execute_without_sandbox(self, mock_run):
        # Setup mock subprocess response
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "Execution stdout"
        mock_run.return_value = mock_result

        # Run provider with sandbox disabled
        provider_no_sandbox = AgyProvider(sandbox_enabled=False)
        output = provider_no_sandbox.execute("Task prompt", self.workspace_path, "gemini-1.5-flash")

        # Assert output
        self.assertEqual(output, "Execution stdout")

        # Assert subprocess was called with correct non-sandboxed arguments
        args, kwargs = mock_run.call_args
        called_cmd = args[0]
        self.assertNotIn("sandbox-exec", called_cmd)
        self.assertEqual(kwargs.get("cwd"), self.workspace_path)

    @patch("subprocess.run")
    def test_execute_with_sandbox(self, mock_run):
        # Setup mock subprocess response
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "Execution stdout"
        mock_run.return_value = mock_result

        # Run provider with sandbox enabled
        output = self.provider.execute("Task prompt", self.workspace_path, "gemini-1.5-flash")

        # Assert output
        self.assertEqual(output, "Execution stdout")

        # Assert subprocess was called with sandboxed arguments
        args, kwargs = mock_run.call_args
        called_cmd = args[0]
        self.assertEqual(called_cmd[0], "sandbox-exec")
        self.assertEqual(called_cmd[1], "-f")
        # Check that the path is absolute and correct
        self.assertTrue(os.path.isabs(called_cmd[2]))
        self.assertIn("sandbox.sb", called_cmd[2])
        self.assertEqual(kwargs.get("cwd"), self.workspace_path)

    @patch("subprocess.run")
    def test_execute_failure_raises_error(self, mock_run):
        # Setup mock subprocess failure
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "Error during execution"
        mock_run.return_value = mock_result

        # Assert that executing raises RuntimeError
        with self.assertRaises(RuntimeError):
            self.provider.execute("Task prompt", self.workspace_path, "gemini-1.5-flash")

if __name__ == "__main__":
    unittest.main()
