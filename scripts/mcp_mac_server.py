#!/usr/bin/env python3
import sys
import json
import subprocess
from typing import Dict, Any, List

def send_imessage(recipient: str, message: str) -> bool:
    """Executes AppleScript to send an iMessage."""
    # Escape quotes in message
    escaped_message = message.replace('"', '\\"')
    applescript = f'''
    tell application "Messages"
        set targetService to 1st service whose service type is iMessage
        set targetBuddy to buddy "{recipient}" of targetService
        send "{escaped_message}" to targetBuddy
    end tell
    '''
    try:
        result = subprocess.run(
            ["osascript", "-e", applescript],
            capture_output=True,
            text=True,
            timeout=10
        )
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        print("Error: AppleScript execution timed out (TCC permission prompt blocked run?).", file=sys.stderr)
        return False
    except Exception as e:
        print(f"Error executing AppleScript: {e}", file=sys.stderr)
        return False

def handle_request(req: Dict[str, Any]) -> Dict[str, Any]:
    """Processes incoming MCP JSON-RPC requests."""
    method = req.get("method")
    req_id = req.get("id")
    
    response = {
        "jsonrpc": "2.0",
        "id": req_id
    }
    
    if method == "initialize":
        response["result"] = {
            "protocolVersion": "2024-11-05",
            "capabilities": {
                "tools": {}
            },
            "serverInfo": {
                "name": "macos-system-notifications",
                "version": "1.0.0"
            }
        }
    elif method == "tools/list":
        response["result"] = {
            "tools": [
                {
                    "name": "send_imessage",
                    "description": "Sends an iMessage to a contact phone number or Apple ID email address.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "recipient": {
                                "type": "string",
                                "description": "The phone number or email address of the iMessage recipient."
                            },
                            "message": {
                                "type": "string",
                                "description": "The message text to send."
                            }
                        },
                        "required": ["recipient", "message"]
                    }
                }
            ]
        }
    elif method == "tools/call":
        params = req.get("params", {})
        name = params.get("name")
        arguments = params.get("arguments", {})
        
        if name == "send_imessage":
            recipient = arguments.get("recipient")
            message = arguments.get("message")
            
            if not recipient or not message:
                response["error"] = {
                    "code": -32602,
                    "message": "Invalid params: recipient and message are required."
                }
            else:
                success = send_imessage(recipient, message)
                if success:
                    response["result"] = {
                        "content": [
                            {
                                "type": "text",
                                "text": f"iMessage sent successfully to {recipient}."
                            }
                        ]
                    }
                else:
                    response["error"] = {
                        "code": -32603,
                        "message": "Failed to send iMessage via AppleScript."
                    }
        else:
            response["error"] = {
                "code": -32601,
                "message": f"Method not found: {name}"
            }
    else:
        # Ignore notifications (which have no ID) like 'initialized'
        if req_id is not None:
            response["error"] = {
                "code": -32601,
                "message": f"Method not found: {method}"
            }
        else:
            return {}
            
    return response

def main() -> None:
    """Standard IO loop for JSON-RPC MCP server."""
    # Force line buffering for standard output
    sys.stdout.reconfigure(line_buffering=True)
    
    while True:
        try:
            line = sys.stdin.readline()
            if not line:
                break
                
            req = json.loads(line)
            res = handle_request(req)
            
            if res:
                sys.stdout.write(json.dumps(res) + "\n")
                
        except json.JSONDecodeError:
            # Send parse error
            sys.stdout.write(json.dumps({
                "jsonrpc": "2.0",
                "error": {
                    "code": -32700,
                    "message": "Parse error"
                },
                "id": None
            }) + "\n")
        except Exception as e:
            # Handle other runtime errors
            print(f"Error in MCP server loop: {e}", file=sys.stderr)
            break

if __name__ == "__main__":
    main()
