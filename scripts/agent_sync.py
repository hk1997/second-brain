#!/usr/bin/env python3
import os
import re
import sys
import json
import time
import subprocess
from typing import Dict, Any, List

# Resolve paths relative to script location
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CONFIG_PATH = os.path.join(SCRIPT_DIR, "..", "config", "config.json")

# Add repository root to python path to resolve scripts imports
sys.path.insert(0, os.path.abspath(os.path.join(SCRIPT_DIR, "..")))

from scripts.providers.agy import AgyProvider
from scripts.router import route_task

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

def send_notification_alert(config: Dict[str, Any], message: str) -> None:
    """Sends an iMessage notification alert if notifications are enabled."""
    if not config.get("notifications", {}).get("enabled", True):
        return
        
    target = config["notifications"].get("imessage_target")
    if not target:
        return
        
    escaped_message = message.replace('"', '\\"')
    applescript = f'''
    tell application "Messages"
        set targetService to 1st service whose service type is iMessage
        set targetBuddy to buddy "{target}" of targetService
        send "{escaped_message}" to targetBuddy
    end tell
    '''
    try:
        # Spawn asynchronously so it doesn't block the watcher loop
        subprocess.Popen(["osascript", "-e", applescript], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        print(f"Warning: Failed to trigger notification alert: {e}", file=sys.stderr)

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

def extract_wiki_links(text: str) -> List[str]:
    """Extracts all wiki-link targets (without display text or anchors) from a string."""
    raw_links = re.findall(r"\[\[(.*?)\]\]", text)
    targets = []
    for link in raw_links:
        # Split display name (separated by |)
        target = link.split("|")[0].strip()
        # Split anchor (separated by #)
        target = target.split("#")[0].strip()
        if target:
            targets.append(target)
    return targets

def find_file_in_vault(vault_path: str, filename: str) -> str:
    """Searches the vault (case-insensitive) for a filename (with or without .md extension)."""
    if not filename.lower().endswith(".md"):
        filename_md = filename + ".md"
        filename_no_md = filename
    else:
        filename_md = filename
        filename_no_md = filename[:-3]
        
    for root, dirs, files in os.walk(vault_path):
        # Skip hidden folders and System folder to avoid templates
        dirs[:] = [d for d in dirs if not d.startswith(".") and d != "_System"]
        for file in files:
            if file.lower() in (filename_md.lower(), filename_no_md.lower()):
                return os.path.join(root, file)
    return None

def resolve_link(vault_path: str, agent_dir_rel: str, link_name: str) -> Dict[str, Any]:
    """Resolves a wiki-link target to either a template or a context note.
    
    Returns a dict containing:
      - "type": "template" | "context" | None
      - "filepath": str | None
      - "content": str | None
    """
    templates_dir = os.path.join(vault_path, agent_dir_rel, "Templates")
    filename = link_name if link_name.lower().endswith(".md") else f"{link_name}.md"
    
    # 1. Check in templates first
    if os.path.exists(templates_dir):
        # Case-sensitive check
        template_path = os.path.join(templates_dir, filename)
        if os.path.exists(template_path):
            try:
                with open(template_path, "r", encoding="utf-8") as f:
                    content = f.read()
                return {"type": "template", "filepath": template_path, "content": content}
            except Exception as e:
                print(f"Error reading template {template_path}: {e}", file=sys.stderr)
                
        # Case-insensitive check
        for f_name in os.listdir(templates_dir):
            if f_name.lower() == filename.lower():
                path = os.path.join(templates_dir, f_name)
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        content = f.read()
                    return {"type": "template", "filepath": path, "content": content}
                except Exception as e:
                    print(f"Error reading template {path}: {e}", file=sys.stderr)

    # 2. Check in general vault (context note)
    context_path = find_file_in_vault(vault_path, link_name)
    if context_path:
        try:
            with open(context_path, "r", encoding="utf-8") as f:
                content = f.read()
            return {"type": "context", "filepath": context_path, "content": content}
        except Exception as e:
            print(f"Error reading context note {context_path}: {e}", file=sys.stderr)
            
    return {"type": None, "filepath": None, "content": None}

def resolve_and_interpolate_prompt(prompt: str, vault_path: str, agent_dir_rel: str) -> str:
    """Parses wiki-links, resolves templates/context notes, and interpolates variables."""
    links = extract_wiki_links(prompt)
    
    templates = []
    contexts = []
    
    for link in links:
        res = resolve_link(vault_path, agent_dir_rel, link)
        if res["type"] == "template":
            templates.append((link, res["content"]))
        elif res["type"] == "context":
            contexts.append((link, res["content"]))
            
    if templates:
        base_prompt = templates[0][1]
    else:
        base_prompt = prompt
        
    current_time_struct = time.localtime()
    date_str = time.strftime("%Y-%m-%d", current_time_struct)
    time_str = time.strftime("%H:%M:%S", current_time_struct)
    
    variables = {
        "date": date_str,
        "time": time_str,
    }
    
    first_context_title = ""
    first_context_content = ""
    if contexts:
        first_context_title = contexts[0][0]
        first_context_content = contexts[0][1]
        
    variables["title"] = first_context_title
    variables["content"] = first_context_content
    
    resolved_prompt = base_prompt
    for var_name, var_value in variables.items():
        pattern = re.compile(r"\{\{\s*" + re.escape(var_name) + r"\s*\}\}", re.IGNORECASE)
        resolved_prompt = pattern.sub(var_value, resolved_prompt)
        
    has_content_placeholder = bool(re.search(r"\{\{\s*content\s*\}\}", base_prompt, re.IGNORECASE))
    
    if contexts and not has_content_placeholder:
        context_sections = []
        for title, content in contexts:
            context_sections.append(f"\n\n--- Context: {title} ---\n{content}")
        resolved_prompt += "".join(context_sections)
        
    if not templates:
        for link in links:
            resolved_prompt = re.sub(
                r"\[\[" + re.escape(link) + r"(?:\|.*?)?\]\]",
                link,
                resolved_prompt
            )
            
    return resolved_prompt.strip()

def execute_task_pipeline(filepath: str, running_block: str, prompt: str, config: Dict[str, Any]) -> bool:
    """Runs the subagent router, sandboxed provider and logs the results."""
    # 2. Execute under sandbox
    print(f"Executing: '{prompt}'...")
    
    # Determine model complexity routing via the Router Subagent
    print("Routing task via Router Subagent...")
    metadata = route_task(prompt, config["vault"]["path"], config)
    model = metadata["model_recommendation"]
    print(f"Task routed to {model} (Complexity: {metadata['complexity']}, MCP tools: {metadata['required_mcp_servers']})")
    
    # Update progress in the file to "Executing"
    running_line = ""
    for line in running_block.splitlines():
        if "- [/]" in line:
            running_line = line
            break
    if not running_line:
        running_line = running_block.splitlines()[0]
        
    next_running_block = running_line + f"\n  * 🟢 Routed to {model}\n  * 🟢 Executing task..."
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
        new_content = content.replace(running_block, next_running_block, 1)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(new_content)
        running_block = next_running_block
    except Exception as e:
        print(f"Failed to update progress: {e}", file=sys.stderr)
        
    sandbox_enabled = config["security"]["sandbox_exec_enabled"]
    provider = AgyProvider(sandbox_enabled=sandbox_enabled)
    
    execution_success = False
    output_text = ""
    error_details = None
    
    try:
        output_text = provider.execute(prompt, config["vault"]["path"], model)
        execution_success = True
        print(f"Task completed successfully: '{prompt}'")
        send_notification_alert(config, f"OAB Task Completed: '{prompt}'")
    except Exception as e:
        execution_success = False
        error_details = str(e)
        output_text = error_details
        print(f"Task failed: '{prompt}' - Error: {e}", file=sys.stderr)
        send_notification_alert(config, f"OAB Task Failed: '{prompt}' - Error: {e}")
    
    # Replicate log back to logs.md
    replicate_log(config["vault"]["path"], config["vault"]["agent_dir"], prompt, model, execution_success, output_text, error_details)

    # 3. Mutate state to Completed or Failed
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            current_content = f.read()
            
        running_line = ""
        for line in running_block.splitlines():
            if "- [/]" in line:
                running_line = line
                break
        if not running_line:
            running_line = running_block.splitlines()[0]
            
        if execution_success:
            final_tag = "- [x] #agent"
            completed_block = running_line.replace("- [/] #agent", final_tag, 1)
        else:
            final_tag = "- [-] #agent"
            error_line = error_details.splitlines()[0] if error_details else "Unknown error"
            completed_block = running_line.replace("- [/] #agent", final_tag, 1) + f"\n  * ❌ Error: {error_line}"
            
        new_content = current_content.replace(running_block, completed_block, 1)
        
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(new_content)
    except Exception as e:
        print(f"Failed to write final status to file: {e}", file=sys.stderr)
        return False
        
    return execution_success

def process_file(filepath: str, config: Dict[str, Any]) -> None:
    """Parses, mutates and executes tasks in a specific markdown note."""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception as e:
        print(f"Error reading {filepath}: {e}", file=sys.stderr)
        return

    # Check for pending approvals first!
    pending_pattern = re.compile(r"^([ \t]*-[ \t]*\[[ \t]*[ ][ \t]*\][ \t]*#agent-pending-approval[ \t]+)(.*)$", re.MULTILINE)
    pending_matches = list(pending_pattern.finditer(content))
    
    current_content = content
    
    if pending_matches:
        for match in pending_matches:
            original_line = match.group(0)
            prompt = match.group(2).strip()
            
            # Check note for checkboxes
            is_approved = "- [x] Approve Task" in content
            is_rejected = "- [x] Reject Task" in content
            
            if is_approved:
                print(f"Task approved by user: '{prompt}'")
                # Clean up approval block and update state to Running
                clean_line = original_line.replace("#agent-pending-approval", "#agent").replace("- [ ]", "- [/]")
                running_block = clean_line + "\n  * 🟢 Routing task..."
                current_content = current_content.replace(original_line, running_block)
                # Strip out approval checklist block
                current_content = re.sub(
                    r"\n*### \[Approval Required\].*?- \[[x ]\] Approve Task\s*\n?- \[[x ]\] Reject Task\s*\n?", 
                    "", 
                    current_content, 
                    flags=re.DOTALL
                )
                
                try:
                    with open(filepath, "w", encoding="utf-8") as f:
                        f.write(current_content)
                    send_notification_alert(config, f"OAB Task Approved: '{prompt}'")
                except Exception as e:
                    print(f"Failed to write approved status: {e}", file=sys.stderr)
                    return
                
                # Execute pipeline
                resolved_prompt = resolve_and_interpolate_prompt(prompt, config["vault"]["path"], config["vault"]["agent_dir"])
                execute_task_pipeline(filepath, running_block, resolved_prompt, config)
                return
                
            elif is_rejected:
                print(f"Task rejected by user: '{prompt}'")
                # Update state to Failed (Cancelled)
                failed_line = original_line.replace("#agent-pending-approval", "#agent").replace("- [ ]", "- [-]")
                current_content = current_content.replace(original_line, failed_line)
                # Strip out approval checklist block
                current_content = re.sub(
                    r"\n*### \[Approval Required\].*?- \[[x ]\] Approve Task\s*\n?- \[[x ]\] Reject Task\s*\n?", 
                    "", 
                    current_content, 
                    flags=re.DOTALL
                )
                # Append rejection log
                current_content += "\n\n# Error\nTask execution rejected by user."
                
                try:
                    with open(filepath, "w", encoding="utf-8") as f:
                        f.write(current_content)
                    send_notification_alert(config, f"OAB Task Rejected: '{prompt}'")
                except Exception as e:
                    print(f"Failed to write rejected status: {e}", file=sys.stderr)
                return
            else:
                # Still waiting for approval
                print(f"Task is pending user approval: '{prompt}'")
                return

    # Check for new tasks
    task_pattern = re.compile(r"^([ \t]*-[ \t]*\[[ \t]*[ ][ \t]*\][ \t]*#agent[ \t]+)(.*)$", re.MULTILINE)
    matches = list(task_pattern.finditer(current_content))
    if not matches:
        return

    print(f"Scanning: {os.path.basename(filepath)} - Found {len(matches)} task(s)")
    
    for match in matches:
        original_line = match.group(0)
        prompt = match.group(2).strip()
        
        print(f"Task detected: '{prompt}'")
        
        # Resolve prompt here
        resolved_prompt = resolve_and_interpolate_prompt(prompt, config["vault"]["path"], config["vault"]["agent_dir"])
        
        # Check if task requires manual approval
        frontmatter = {}
        if current_content.startswith("---"):
            parts = current_content.split("---", 2)
            if len(parts) >= 3:
                yaml_part = parts[1]
                for line in yaml_part.splitlines():
                    if ":" in line:
                        k, v = line.split(":", 1)
                        frontmatter[k.strip().lower()] = v.strip().strip('"').strip("'")
                        
        has_write_files = "write_files" in frontmatter
        dangerous_keywords = ["rm ", "delete", "git push", "git commit", "deploy", "run command"]
        has_dangerous_keywords = any(kw in resolved_prompt.lower() for kw in dangerous_keywords)
        
        if has_write_files or has_dangerous_keywords:
            print(f"Task '{prompt}' requires manual approval. Inserting approval checklist...")
            pending_line = original_line.replace("#agent", "#agent-pending-approval")
            current_content = current_content.replace(original_line, pending_line)
            
            # Construct interactive checklist block
            approval_ui = (
                "\n\n### [Approval Required] Run Task\n"
                "This task contains write files or execution actions. Please approve to run:\n"
                "- [ ] Approve Task\n"
                "- [ ] Reject Task\n"
            )
            # Find the position right after the pending line
            line_index = current_content.find(pending_line)
            if line_index != -1:
                insert_pos = line_index + len(pending_line)
                current_content = current_content[:insert_pos] + approval_ui + current_content[insert_pos:]
            else:
                current_content += approval_ui
                
            try:
                with open(filepath, "w", encoding="utf-8") as f:
                    f.write(current_content)
                send_notification_alert(config, f"OAB Task Approval Required: '{prompt}'")
            except Exception as e:
                print(f"Failed to write pending approval state: {e}", file=sys.stderr)
            return
        else:
            # Run task immediately
            running_line = original_line.replace("- [ ]", "- [/]")
            running_block = running_line + "\n  * 🟢 Routing task..."
            current_content = current_content.replace(original_line, running_block, 1)
            
            try:
                with open(filepath, "w", encoding="utf-8") as f:
                    f.write(current_content)
                send_notification_alert(config, f"OAB Task Running: '{prompt}'")
            except Exception as e:
                print(f"Failed to write running status to file: {e}", file=sys.stderr)
                return
                
            execute_task_pipeline(filepath, running_block, resolved_prompt, config)

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
    
    if "--loop" in sys.argv:
        print("Starting Obsidian Agent Bridge loop mode (press Ctrl+C to exit)...")
        try:
            while True:
                scan_vault(vault_path, agent_dir_rel, config)
                time.sleep(5)
        except KeyboardInterrupt:
            print("\nExiting watcher loop gracefully.")
    else:
        scan_vault(vault_path, agent_dir_rel, config)

if __name__ == "__main__":
    main()
