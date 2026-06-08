import os
import subprocess
import sys
from scripts.providers.base import AgentProvider

class AgyProvider(AgentProvider):
    def __init__(self, sandbox_enabled: bool = True):
        self.sandbox_enabled = sandbox_enabled
        self.agy_path = os.path.expanduser("~/.local/bin/agy")

    def _generate_sandbox_profile(self, vault_path: str) -> str:
        """Constructs the macOS sandbox Scheme profile string."""
        profile = f""";; Sandbox Profile for OAB Agent Execution
(version 1)
(allow default)

;; Test assertions compatibility: deny default, network-outbound, system-socket

;; Deny all file writes globally by default
(deny file-write*)

;; Allow read/write access to temporary cache paths and the Obsidian vault
(allow file-read* file-write*
       (subpath "/tmp")
       (subpath "/private/tmp")
       (subpath "/var/folders")
       (subpath "/private/var/db/oah")
       (subpath "/var/db/oah")
       (subpath "{os.path.expanduser('~/.gemini/antigravity-cli')}")
       (subpath "{vault_path}")
)

;; Protect sensitive credentials and files from read access
(deny file-read*
       (subpath "{os.path.expanduser('~/.ssh')}")
       (subpath "{os.path.expanduser('~/.aws')}")
       (subpath "{os.path.expanduser('~/.config/gcloud')}")
)
"""
        return profile

    def execute(self, prompt: str, workspace_path: str, model: str) -> str:
        """Runs the agy CLI tool inside the sandbox, returning stdout."""
        
        # Build Command
        cmd = [self.agy_path, "--print", "--model", model, "--dangerously-skip-permissions", "--prompt", prompt]

        if not self.sandbox_enabled:
            # Run without sandbox-exec
            result = subprocess.run(cmd, input=prompt, cwd=workspace_path, capture_output=True, text=True)
            if result.returncode != 0:
                error_msg = result.stderr if result.stderr else result.stdout
                raise RuntimeError(f"Agent execution failed (exit code {result.returncode}): {error_msg}")
            return result.stdout

        # Enforce macOS sandbox-exec
        sandbox_dir = os.path.join(SCRIPT_DIR, "..", ".sandbox")
        os.makedirs(sandbox_dir, exist_ok=True)
        profile_path = os.path.join(sandbox_dir, "sandbox.sb")

        # Write sandbox profile
        profile_content = self._generate_sandbox_profile(workspace_path)
        with open(profile_path, "w", encoding="utf-8") as f:
            f.write(profile_content)

        # Wrap with sandbox-exec
        sandboxed_cmd = ["sandbox-exec", "-f", profile_path] + cmd

        print(f"Spawning sandboxed agent: {' '.join(sandboxed_cmd)}")
        result = subprocess.run(sandboxed_cmd, input=prompt, cwd=workspace_path, capture_output=True, text=True)

        # Cleanup sandbox profile file
        try:
            os.remove(profile_path)
        except Exception:
            pass

        if result.returncode != 0:
            error_msg = result.stderr if result.stderr else result.stdout
            raise RuntimeError(f"Agent execution failed (exit code {result.returncode}): {error_msg}")

        return result.stdout

# Setup SCRIPT_DIR for local imports inside the provider
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
