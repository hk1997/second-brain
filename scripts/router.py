import re
import json
from typing import Dict, Any
from scripts.providers.agy import AgyProvider

ROUTER_SYSTEM_PROMPT = """You are the Router Subagent for the Obsidian Agent Bridge.
Your task is to analyze the user's prompt and extract execution metadata.

Return a JSON object containing:
1. "complexity": "simple" (e.g. calendar, reminders, formatting notes) or "complex" (e.g. coding, refactoring, building features)
2. "model_recommendation": "gemini-1.5-flash" (for simple tasks) or "gemini-1.5-pro" (for complex tasks)
3. "required_mcp_servers": list of server names required for this prompt. Available options: ["google-calendar", "google-tasks", "mac-notifications", "stock-analyzer"].

You must respond ONLY with a valid JSON block inside a ```json code fence. Do not write any explanations or headers.

Example output:
```json
{{
  "complexity": "simple",
  "model_recommendation": "gemini-1.5-flash",
  "required_mcp_servers": ["google-calendar"]
}}
```

User Prompt:
"{user_prompt}"
"""

def clean_json_output(output: str) -> str:
    """Strips markdown code fences and whitespace from JSON string."""
    cleaned = output.strip()
    if cleaned.startswith("```"):
        # Strip start fence
        cleaned = re.sub(r"^```(?:json)?\n", "", cleaned)
        # Strip end fence
        cleaned = re.sub(r"\n```$", "", cleaned)
    return cleaned.strip()

def route_task(user_prompt: str, vault_path: str, config: Dict[str, Any]) -> Dict[str, Any]:
    """Uses a lightweight Flash agent run to classify prompt complexity and parameters."""
    
    # Establish defaults
    default_metadata = {
        "complexity": "simple",
        "model_recommendation": config["execution"]["default_model"],
        "required_mcp_servers": []
    }
    
    # Construct router query
    router_prompt = ROUTER_SYSTEM_PROMPT.format(user_prompt=user_prompt)
    
    # Setup provider
    sandbox_enabled = config["security"]["sandbox_exec_enabled"]
    provider = AgyProvider(sandbox_enabled=sandbox_enabled)
    
    try:
        # Execute routing task using cheap Flash model
        raw_output = provider.execute(router_prompt, vault_path, "gemini-1.5-flash")
        cleaned_json = clean_json_output(raw_output)
        metadata = json.loads(cleaned_json)
        
        # Verify schema keys
        if all(k in metadata for k in ["complexity", "model_recommendation", "required_mcp_servers"]):
            # Normalize model names to config definitions
            if metadata["model_recommendation"] == "gemini-1.5-pro":
                metadata["model_recommendation"] = config["execution"]["pro_model"]
            else:
                metadata["model_recommendation"] = config["execution"]["default_model"]
            return metadata
            
        return default_metadata
    except Exception as e:
        print(f"Warning: Router subagent execution failed or returned invalid JSON ({e}). Falling back to defaults.")
        return default_metadata
