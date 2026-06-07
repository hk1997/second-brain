#!/usr/bin/env python3
import os
import re
import sys
import json
import time
from typing import Dict, Any, List

from scripts.providers.agy import AgyProvider

# Resolve paths relative to script location
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CONFIG_PATH = os.path.join(SCRIPT_DIR, "..", "config", "config.json")

def load_config(config_path: str = DEFAULT_CONFIG_PATH) -> Dict[str, Any]:
    """Loads configuration from config.json."""
    if not os.path.exists(config_path):
        print(f"Error: Config file not found at {config_path}", file=sys.stderr)
        sys.exit(1)
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
            # Expand tilde in paths
            if "vault" in config and "path" in config["vault"]:
                config["vault"]["path"] = os.path.expanduser(config["vault"]["path"])
            return config
    except Exception as e:
        print(f"Error reading config: {e}", file=sys.stderr)
        sys.exit(1)

def get_last_scan_time(agent_dir: str) -> float:
    """Reads the last scan timestamp from state file."""
    state_file = os.path.join(agent_dir, ".last_scan")
    if not os.path.exists(state_file):
        return 0.0
    try:
        with open(state_file, "r", encoding="utf-8") as f:
            data = json.load(f)
            return float(data.get("last_scan_time", 0.0))
    except (Exception, ValueError):
        return 0.0

def set_last_scan_time(agent_dir: str, scan_time: float) -> None:
    """Writes the current scan timestamp to state file."""
    state_file = os.path.join(agent_dir, ".last_scan")
    try:
        os.makedirs(agent_dir, exist_ok=True)
        with open(state_file, "w", encoding="utf-8") as f:
            json.dump({"last_scan_time": scan_time}, f)
    except Exception as e:
        print(f"Warning: Failed to save scan state: {e}", file=sys.stderr)

def replicate_log(vault_path: str, agent_dir_rel: str, prompt: str, model: str, success: bool, output: str, error: str = None) -> None:
    """Appends execution log entry to _System/Agent/logs.md."""
    agent_dir = os.path.join(vault_path, agent_dir_rel)
    os.makedirs(agent_dir, exist_ok=True)
    logs_file = os.path.join(agent_dir, "logs.md")
    
    timestamp = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())
    status_str = "Completed" if success else "Failed"
    
    log_entry = (
        f"## [{timestamp}] Task Execution\n"
        f"* **Prompt:** {prompt}\n"
        f"* **Model:** {model}\n"
        f"* **Status:** {status_str}\n"
    )
    if error:
        log_entry += f"* **Error:** {error}\n"
        
    log_entry += f"\n### Output\n```\n{output.strip()}\n```\n\n---\n\n"
    
    try:
        # Check if logs.md exists, if not, write a header
        if not os.path.exists(logs_file):
            with open(logs_file, "w", encoding="utf-8") as f:
                f.write("# OAB Execution Audit Logs\n\n")
                
        with open(logs_file, "a", encoding="utf-8") as f:
            f.write(log_entry)
    except Exception as e:
        print(f"Warning: Failed to write to log file: {e}", file=sys.stderr)

def process_file(filepath: str, config: Dict[str, Any]) -> None:
    """Parses, mutates and executes tasks in a specific markdown note."""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception as e:
        print(f"Error reading {filepath}: {e}", file=sys.stderr)
        return

    # Match /second-brain-task followed by prompt and <end-task> (multi-line)
    # Using negative lookahead to prevent matching -running, -completed, or -failed tags
    task_pattern = re.compile(r"/second-brain-task(?![a-zA-Z0-9_-])\s*\n?(.*?)\n?<end-task>", re.DOTALL)
    
    matches = list(task_pattern.finditer(content))
    if not matches:
        return

    print(f"Scanning: {os.path.basename(filepath)} - Found {len(matches)} task(s)")
    
    # We load current content as we will mutate it incrementally
    current_content = content

    for match in matches:
        original_block = match.group(0)
        prompt = match.group(1).strip()
        
        print(f"Task detected: '{prompt}'")
        
        # 1. Mutate state to Running
        running_block = original_block.replace("/second-brain-task", "/second-brain-task-running", 1)
        current_content = current_content.replace(original_block, running_block, 1)
        
        try:
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(current_content)
        except Exception as e:
            print(f"Failed to write running status to file: {e}", file=sys.stderr)
            return

        # 2. Execute under sandbox
        print(f"Executing: '{prompt}'...")
        
        # Determine model complexity routing
        # (Router subagent will be implemented in Phase 3; for now we use default model from config)
        model = config["execution"]["default_model"]
        sandbox_enabled = config["security"]["sandbox_exec_enabled"]
        
        provider = AgyProvider(sandbox_enabled=sandbox_enabled)
        
        execution_success = False
        output_text = ""
        error_details = None
        
        try:
            output_text = provider.execute(prompt, config["vault"]["path"], model)
            execution_success = True
            print(f"Task completed successfully: '{prompt}'")
        except Exception as e:
            execution_success = False
            error_details = str(e)
            output_text = error_details
            print(f"Task failed: '{prompt}' - Error: {e}", file=sys.stderr)
        
        # Replicate log back to logs.md
        replicate_log(config["vault"]["path"], config["vault"]["agent_dir"], prompt, model, execution_success, output_text, error_details)

        # 3. Mutate state to Completed or Failed
        final_tag = "/second-brain-task-completed" if execution_success else "/second-brain-task-failed"
        completed_block = running_block.replace("/second-brain-task-running", final_tag, 1)
        current_content = current_content.replace(running_block, completed_block, 1)
        
        try:
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(current_content)
        except Exception as e:
            print(f"Failed to write final status to file: {e}", file=sys.stderr)
            return

def scan_vault(vault_path: str, agent_dir_rel: str, config: Dict[str, Any]) -> None:
    """Scans Obsidian vault for modified markdown files containing tasks."""
    if not os.path.exists(vault_path):
        print(f"Error: Vault path does not exist: {vault_path}", file=sys.stderr)
        return

    agent_dir = os.path.join(vault_path, agent_dir_rel)
    last_scan_time = get_last_scan_time(agent_dir)
    current_scan_time = time.time()
    
    print(f"Running watcher scan (last scan: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(last_scan_time)) if last_scan_time > 0 else 'Never'})")

    # Walk vault to locate modified markdown files
    for root, dirs, files in os.walk(vault_path):
        # Exclude hidden directories (like .obsidian, .git)
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        
        for file in files:
            if file.endswith(".md"):
                filepath = os.path.join(root, file)
                try:
                    mtime = os.path.getmtime(filepath)
                    # Scan files modified since last scan
                    if mtime > last_scan_time:
                        process_file(filepath, config)
                except Exception as e:
                    print(f"Error inspecting file modification time for {file}: {e}", file=sys.stderr)

    set_last_scan_time(agent_dir, current_scan_time)

def main() -> None:
    config = load_config()
    vault_path = config["vault"]["path"]
    agent_dir_rel = config["vault"]["agent_dir"]
    
    scan_vault(vault_path, agent_dir_rel, config)

if __name__ == "__main__":
    main()
