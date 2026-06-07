import unittest
import json
from unittest.mock import patch
from scripts import mcp_mac_server

class TestMcpMacServer(unittest.TestCase):

    def test_handle_request_initialize(self):
        req = {
            "jsonrpc": "2.0",
            "method": "initialize",
            "id": 1
        }
        res = mcp_mac_server.handle_request(req)
        self.assertEqual(res.get("id"), 1)
        self.assertIn("protocolVersion", res.get("result", {}))
        self.assertEqual(res["result"]["serverInfo"]["name"], "macos-system-notifications")

    def test_handle_request_tools_list(self):
        req = {
            "jsonrpc": "2.0",
            "method": "tools/list",
            "id": 2
        }
        res = mcp_mac_server.handle_request(req)
        self.assertEqual(res.get("id"), 2)
        tools = res.get("result", {}).get("tools", [])
        self.assertEqual(len(tools), 1)
        self.assertEqual(tools[0]["name"], "send_imessage")

    @patch("scripts.mcp_mac_server.send_imessage")
    def test_handle_request_tools_call_success(self, mock_send):
        mock_send.return_value = True
        
        req = {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {
                "name": "send_imessage",
                "arguments": {
                    "recipient": "khandelwal.hardik14@gmail.com",
                    "message": "Hello from test!"
                }
            },
            "id": 3
        }
        
        res = mcp_mac_server.handle_request(req)
        self.assertEqual(res.get("id"), 3)
        self.assertNotIn("error", res)
        self.assertIn("content", res.get("result", {}))
        self.assertEqual(res["result"]["content"][0]["text"], "iMessage sent successfully to khandelwal.hardik14@gmail.com.")

    @patch("scripts.mcp_mac_server.send_imessage")
    def test_handle_request_tools_call_failure(self, mock_send):
        mock_send.return_value = False
        
        req = {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {
                "name": "send_imessage",
                "arguments": {
                    "recipient": "khandelwal.hardik14@gmail.com",
                    "message": "Hello from test!"
                }
            },
            "id": 4
        }
        
        res = mcp_mac_server.handle_request(req)
        self.assertEqual(res.get("id"), 4)
        self.assertIn("error", res)
        self.assertEqual(res["error"]["code"], -32603)

if __name__ == "__main__":
    unittest.main()
