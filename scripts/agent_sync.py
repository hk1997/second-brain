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

# Match > [[Target]] or >> [[Target]] or > target.md or >> target.md at the end of a string
REDIRECT_PATTERN = re.compile(r"\s*(>>?)\s*(?:\[\[(.*?)\]\]|([^\s\>]+))\s*$")

# Match @hourly, @daily, @weekly, @monthly
SCHEDULE_PATTERN = re.compile(r"\s*@(hourly|daily|weekly|monthly)\b", re.IGNORECASE)

def parse_redirection(prompt: str) -> tuple:
    """Parses redirection syntax from the end of the prompt.
    
    Returns (cleaned_prompt, redirect_mode, redirect_target)
    """
    match = REDIRECT_PATTERN.search(prompt)
    if match:
        mode = match.group(1)
        target = match.group(2) if match.group(2) is not None else match.group(3)
        cleaned_prompt = prompt[:match.start()].strip()
        return cleaned_prompt, mode, target
    return prompt, None, None

def parse_scheduling(prompt: str) -> tuple:
    """Parses scheduling syntax from the prompt.
    
    Returns (cleaned_prompt, schedule_type)
    """
    match = SCHEDULE_PATTERN.search(prompt)
    if match:
        schedule_type = match.group(1).lower()
        cleaned_prompt = SCHEDULE_PATTERN.sub("", prompt).strip()
        cleaned_prompt = re.sub(r"\s+", " ", cleaned_prompt)
        return cleaned_prompt, schedule_type
    return prompt, None

def should_run_scheduled_task(schedule_type: str, last_run_str: str | None) -> bool:
    """Checks if a scheduled task should run based on the last run timestamp."""
    if not last_run_str:
        return True
        
    try:
        last_run_time = time.strptime(last_run_str, "%Y-%m-%d %H:%M:%S")
        last_run_epoch = time.mktime(last_run_time)
    except Exception:
        return True
        
    current_time = time.time()
    elapsed = current_time - last_run_epoch
    
    if schedule_type == "hourly":
        return elapsed >= 3600.0
    elif schedule_type == "daily":
        return elapsed >= 86340.0 # 24 hours minus 1 min buffer
    elif schedule_type == "weekly":
        return elapsed >= 7 * 86400.0 - 60.0
    elif schedule_type == "monthly":
        return elapsed >= 30 * 86400.0 - 60.0
        
    return False

def resolve_output_filepath(vault_path: str, target: str) -> str:
    """Resolves redirect targets (wiki-links or file paths) to an absolute path within the vault."""
    if not target.lower().endswith(".md"):
        filename = target + ".md"
    else:
        filename = target
    
    filename = filename.strip("/\\")
    
    existing_path = find_file_in_vault(vault_path, target)
    if existing_path:
        return existing_path
        
    full_path = os.path.abspath(os.path.join(vault_path, filename))
    if not full_path.startswith(os.path.abspath(vault_path)):
        raise ValueError("Redirect target path must resolve inside the vault")
    return full_path

def get_task_block_and_last_run(content: str, line_start_pos: int, line_end_pos: int) -> tuple:
    """Given a task line's start and end positions, extracts the entire task block
    (including all indented sub-lines) and the last run time if present.
    
    Returns (full_block, last_run_time_str, block_end_pos).
    """
    lines = content[line_end_pos:].splitlines(keepends=True)
    block_lines = [content[line_start_pos:line_end_pos]]
    last_run_time = None
    consumed_chars = 0
    
    last_run_pattern = re.compile(r"^\s*[\*\-]\s*Last run:\s*(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", re.IGNORECASE)
    
    for line in lines:
        if line.strip() == "" or line.startswith(" ") or line.startswith("\t"):
            block_lines.append(line)
            consumed_chars += len(line)
            match = last_run_pattern.match(line)
            if match:
                last_run_time = match.group(1)
        else:
            break
            
    full_block = "".join(block_lines)
    return full_block, last_run_time, line_end_pos + consumed_chars

def parse_task_line_and_extract_metadata(rest_of_line: str) -> tuple:
    """Cleans tags from the task line and extracts metadata.
    
    Returns (cleaned_prompt, schedule_type, redirect_mode, redirect_target, is_trusted)
    """
    is_trusted = "#trusted" in rest_of_line.lower() or "#force" in rest_of_line.lower()
    
    # Remove all core tags
    clean_line = rest_of_line
    for tag in ["#agent-pending-approval", "#agent", "#trusted", "#force"]:
        clean_line = clean_line.replace(tag, "")
    clean_line = clean_line.strip()
    
    # Parse scheduling
    cleaned_prompt, schedule_type = parse_scheduling(clean_line)
    
    # Parse redirection
    cleaned_prompt, redirect_mode, redirect_target = parse_redirection(cleaned_prompt)
    
    return cleaned_prompt, schedule_type, redirect_mode, redirect_target, is_trusted

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

def execute_task_pipeline(
    filepath: str, 
    running_block: str, 
    prompt: str, 
    config: Dict[str, Any],
    schedule_type: str = None,
    redirect_mode: str = None,
    redirect_target: str = None
) -> bool:
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
    
    # Process output redirection if requested and successful
    if execution_success and redirect_mode and redirect_target:
        try:
            target_path = resolve_output_filepath(config["vault"]["path"], redirect_target)
            os.makedirs(os.path.dirname(target_path), exist_ok=True)
            if redirect_mode == ">":
                with open(target_path, "w", encoding="utf-8") as f:
                    f.write(output_text)
            else: # ">>"
                file_exists = os.path.exists(target_path)
                with open(target_path, "a", encoding="utf-8") as f:
                    if file_exists and os.path.getsize(target_path) > 0:
                        f.write("\n")
                    f.write(output_text)
        except Exception as e:
            print(f"Error redirecting output to {redirect_target}: {e}", file=sys.stderr)
            error_details = f"Redirection failed: {e}"
            execution_success = False

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
            
        timestamp_now = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())
        
        if execution_success:
            final_tag = "- [ ]" if schedule_type else "- [x]"
            completed_block = running_line.replace("- [/]", final_tag, 1)
            
            if schedule_type:
                completed_block += f"\n  * Last run: {timestamp_now}"
                
            if redirect_target:
                is_wiki_link = f"[[{redirect_target}]]" in running_line or f"[[{redirect_target}" in running_line
                link_str = f"[[{redirect_target}]]" if is_wiki_link else redirect_target
                completed_block += f"\n  * 🟢 Output written to {link_str}"
            else:
                output_indented = "\n".join("  " + l for l in output_text.splitlines())
                completed_block += f"\n  <details>\n  <summary>🤖 View Output</summary>\n\n{output_indented}\n  </details>"
        else:
            final_tag = "- [ ]" if schedule_type else "- [-]"
            completed_block = running_line.replace("- [/]", final_tag, 1)
            
            if schedule_type:
                completed_block += f"\n  * Last run: {timestamp_now}"
                
            error_line = error_details.splitlines()[0] if error_details else "Unknown error"
            completed_block += f"\n  * ❌ Error: {error_line}"
            
            output_indented = "\n".join("  " + l for l in output_text.splitlines())
            completed_block += f"\n  <details>\n  <summary>❌ View Error Log</summary>\n\n{output_indented}\n  </details>"
            
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
    pending_pattern = re.compile(r"^([ \t]*-[ \t]*\[[ \t]*[ ][ \t]*\][ \t]*)(.*#agent-pending-approval.*)$", re.MULTILINE)
    pending_matches = list(pending_pattern.finditer(content))
    
    current_content = content
    
    if pending_matches:
        for match in pending_matches:
            original_line = match.group(0)
            rest_of_line = match.group(2).strip()
            
            # Extract prompt and metadata using helper
            cleaned_prompt, schedule_type, redirect_mode, redirect_target, is_trusted = parse_task_line_and_extract_metadata(rest_of_line)
            
            # Check note for checkboxes
            is_approved = "- [x] Approve Task" in content
            is_rejected = "- [x] Reject Task" in content
            
            if is_approved:
                print(f"Task approved by user: '{cleaned_prompt}'")
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
                    send_notification_alert(config, f"OAB Task Approved: '{cleaned_prompt}'")
                except Exception as e:
                    print(f"Failed to write approved status: {e}", file=sys.stderr)
                    return
                
                # Execute pipeline
                resolved_prompt = resolve_and_interpolate_prompt(cleaned_prompt, config["vault"]["path"], config["vault"]["agent_dir"])
                execute_task_pipeline(
                    filepath, 
                    running_block, 
                    resolved_prompt, 
                    config,
                    schedule_type=schedule_type,
                    redirect_mode=redirect_mode,
                    redirect_target=redirect_target
                )
                return
                
            elif is_rejected:
                print(f"Task rejected by user: '{cleaned_prompt}'")
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
                    send_notification_alert(config, f"OAB Task Rejected: '{cleaned_prompt}'")
                except Exception as e:
                    print(f"Failed to write rejected status: {e}", file=sys.stderr)
                return
            else:
                # Still waiting for approval
                print(f"Task is pending user approval: '{cleaned_prompt}'")
                return

    # Check for new tasks
    task_pattern = re.compile(r"^([ \t]*-[ \t]*\[[ \t]*[ ][ \t]*\][ \t]*)(.*#agent(?!-pending-approval).*)$", re.MULTILINE)
    matches = list(task_pattern.finditer(current_content))
    if not matches:
        return

    print(f"Scanning: {os.path.basename(filepath)} - Found {len(matches)} task(s)")
    
    for match in matches:
        original_line = match.group(0)
        rest_of_line = match.group(2).strip()
        
        # Get task block and last run metadata
        full_block, last_run_str, block_end_pos = get_task_block_and_last_run(current_content, match.start(), match.end())
        
        # Extract prompt and metadata using helper
        cleaned_prompt, schedule_type, redirect_mode, redirect_target, is_trusted = parse_task_line_and_extract_metadata(rest_of_line)
        
        # Check if scheduled task should be skipped
        if schedule_type and not should_run_scheduled_task(schedule_type, last_run_str):
            print(f"Scheduled task '{cleaned_prompt}' (@{schedule_type}) skipped - last run was {last_run_str}")
            continue
            
        print(f"Task detected: '{cleaned_prompt}'")
        
        # Resolve prompt here
        resolved_prompt = resolve_and_interpolate_prompt(cleaned_prompt, config["vault"]["path"], config["vault"]["agent_dir"])
        
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
        
        if (has_write_files or has_dangerous_keywords) and not is_trusted:
            print(f"Task '{cleaned_prompt}' requires manual approval. Inserting approval checklist...")
            pending_line = original_line.replace("#agent", "#agent-pending-approval")
            
            # Construct interactive checklist block
            approval_ui = (
                "\n\n### [Approval Required] Run Task\n"
                "This task contains write files or execution actions. Please approve to run:\n"
                "- [ ] Approve Task\n"
                "- [ ] Reject Task\n"
            )
            
            new_block = pending_line + approval_ui
            if last_run_str:
                new_block += f"\n  * Last run: {last_run_str}"
                
            current_content = current_content.replace(full_block, new_block, 1)
                
            try:
                with open(filepath, "w", encoding="utf-8") as f:
                    f.write(current_content)
                send_notification_alert(config, f"OAB Task Approval Required: '{cleaned_prompt}'")
            except Exception as e:
                print(f"Failed to write pending approval state: {e}", file=sys.stderr)
            return
        else:
            # Run task immediately
            running_line = original_line.replace("- [ ]", "- [/]")
            running_block = running_line + "\n  * 🟢 Routing task..."
            
            current_content = current_content.replace(full_block, running_block, 1)
            
            try:
                with open(filepath, "w", encoding="utf-8") as f:
                    f.write(current_content)
                send_notification_alert(config, f"OAB Task Running: '{cleaned_prompt}'")
            except Exception as e:
                print(f"Failed to write running status to file: {e}", file=sys.stderr)
                return
                
            execute_task_pipeline(
                filepath, 
                running_block, 
                resolved_prompt, 
                config,
                schedule_type=schedule_type,
                redirect_mode=redirect_mode,
                redirect_target=redirect_target
            )
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
                    # We subtract a 30-second buffer to handle clock drift and filesystem write sync latency.
                    # As OAB tasks transition states upon match, duplicate triggers are naturally prevented.
                    if mtime > (last_scan_time - 30.0 if last_scan_time > 0.0 else 0.0):
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
