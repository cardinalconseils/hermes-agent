"""
Tests for the a2a_send tool — A2A client operations.

Run: python -m pytest tests/tools/test_a2a_send.py -v -o 'addopts='
or:  python3 -m unittest tests.tools.test_a2a_send -v
"""

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock
import urllib.error

# Ensure the source tree is on the path
_src = Path(__file__).resolve().parents[2]
if str(_src) not in sys.path:
    sys.path.insert(0, str(_src))


class TestA2ASendTool(unittest.TestCase):
    """Test the a2a_send tool's logic (without real network calls)."""

    def test_discover_builds_correct_card_url(self):
        from tools.a2a_send_tool import _get_agent_card_url
        self.assertEqual(
            _get_agent_card_url("http://localhost:8642"),
            "http://localhost:8642/.well-known/agent-card.json",
        )
        # Trailing slash normalization
        self.assertEqual(
            _get_agent_card_url("http://localhost:8642/"),
            "http://localhost:8642/.well-known/agent-card.json",
        )

    def test_jsonrpc_url_resolution(self):
        from tools.a2a_send_tool import _get_jsonrpc_url
        self.assertEqual(
            _get_jsonrpc_url("http://localhost:8642"),
            "http://localhost:8642/a2a",
        )
        # If URL already ends with /a2a, don't double it
        self.assertEqual(
            _get_jsonrpc_url("http://localhost:8642/a2a"),
            "http://localhost:8642/a2a",
        )

    def test_discover_success(self):
        from tools.a2a_send_tool import _discover_agent
        mock_card = {
            "name": "Test Agent",
            "description": "A test agent",
            "url": "http://localhost:8642/a2a",
            "version": "1.0.0",
            "skills": [{"id": "s1", "name": "Skill 1", "description": "Test"}],
            "authentication": {"schemes": ["Bearer"]},
            "capabilities": {"streaming": True, "pushNotifications": False},
        }
        with patch("tools.a2a_send_tool._make_request", return_value=mock_card):
            result = json.loads(_discover_agent("http://localhost:8642"))
        self.assertTrue(result["success"])
        self.assertEqual(result["agent_card"]["name"], "Test Agent")

    def test_discover_connection_error(self):
        from tools.a2a_send_tool import _discover_agent
        with patch("tools.a2a_send_tool._make_request",
                   side_effect=urllib.error.URLError("Connection refused")):
            result = json.loads(_discover_agent("http://localhost:9999"))
        self.assertFalse(result.get("success", False))

    def test_discover_http_error(self):
        from tools.a2a_send_tool import _discover_agent
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"error": "not found"}'
        http_err = urllib.error.HTTPError(
            "http://localhost:8642/.well-known/agent-card.json",
            404, "Not Found", {}, mock_resp,
        )
        with patch("tools.a2a_send_tool._make_request", side_effect=http_err):
            result = json.loads(_discover_agent("http://localhost:8642"))
        self.assertFalse(result.get("success", False))

    def test_send_success_blocking(self):
        from tools.a2a_send_tool import _send_message
        mock_response = {
            "jsonrpc": "2.0",
            "id": "hermes-123",
            "result": {
                "id": "task-abc",
                "state": "completed",
                "artifacts": [{
                    "artifactId": "art-1",
                    "name": "response",
                    "parts": [{"kind": "text", "text": "Risk score: LOW"}],
                }],
            },
        }
        with patch("tools.a2a_send_tool._make_request", return_value=mock_response):
            result = json.loads(_send_message(
                "http://localhost:8642",
                "Check fraud risk for transaction #12345",
                api_key="test-key",
            ))
        self.assertTrue(result["success"])
        self.assertEqual(result["task_id"], "task-abc")
        self.assertEqual(result["state"], "completed")
        self.assertEqual(len(result["artifacts"]), 1)
        self.assertEqual(result["artifacts"][0]["parts"][0]["text"], "Risk score: LOW")

    def test_send_jsonrpc_error(self):
        from tools.a2a_send_tool import _send_message
        mock_response = {
            "jsonrpc": "2.0",
            "id": "hermes-123",
            "error": {"code": -32602, "message": "Missing message"},
        }
        with patch("tools.a2a_send_tool._make_request", return_value=mock_response):
            result = json.loads(_send_message(
                "http://localhost:8642",
                "test",
            ))
        self.assertFalse(result.get("success", False))

    def test_send_auth_error(self):
        from tools.a2a_send_tool import _send_message
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"error": "unauthorized"}'
        http_err = urllib.error.HTTPError(
            "http://localhost:8642/a2a",
            401, "Unauthorized", {}, mock_resp,
        )
        with patch("tools.a2a_send_tool._make_request", side_effect=http_err):
            result = json.loads(_send_message(
                "http://localhost:8642",
                "test",
                api_key="wrong-key",
            ))
        self.assertFalse(result.get("success", False))
        self.assertIn("401", result.get("error", ""))

    def test_status_success(self):
        from tools.a2a_send_tool import _get_task_status
        mock_response = {
            "jsonrpc": "2.0",
            "id": "hermes-status-123",
            "result": {
                "id": "task-abc",
                "state": "working",
            },
        }
        with patch("tools.a2a_send_tool._make_request", return_value=mock_response):
            result = json.loads(_get_task_status(
                "http://localhost:8642",
                "task-abc",
            ))
        self.assertTrue(result["success"])
        self.assertEqual(result["state"], "working")

    def test_list_success(self):
        from tools.a2a_send_tool import _list_tasks
        mock_response = {
            "jsonrpc": "2.0",
            "id": "hermes-list-123",
            "result": {"tasks": [], "count": 0},
        }
        with patch("tools.a2a_send_tool._make_request", return_value=mock_response):
            result = json.loads(_list_tasks("http://localhost:8642"))
        self.assertTrue(result["success"])

    def test_cancel_success(self):
        from tools.a2a_send_tool import _cancel_task
        mock_response = {
            "jsonrpc": "2.0",
            "id": "hermes-cancel-123",
            "result": {"id": "task-abc", "state": "canceled"},
        }
        with patch("tools.a2a_send_tool._make_request", return_value=mock_response):
            result = json.loads(_cancel_task(
                "http://localhost:8642",
                "task-abc",
            ))
        self.assertTrue(result["success"])
        self.assertEqual(result["state"], "canceled")

    def test_unknown_operation(self):
        from tools.a2a_send_tool import a2a_send
        result = json.loads(a2a_send(
            operation="bogus",
            agent_url="http://localhost:8642",
        ))
        self.assertFalse(result.get("success", False))
        self.assertIn("Unknown operation", result.get("error", ""))

    def test_missing_agent_url(self):
        from tools.a2a_send_tool import a2a_send
        result = json.loads(a2a_send(operation="discover", agent_url=""))
        self.assertFalse(result.get("success", False))

    def test_send_missing_message(self):
        from tools.a2a_send_tool import a2a_send
        result = json.loads(a2a_send(
            operation="send",
            agent_url="http://localhost:8642",
            message=None,
        ))
        self.assertFalse(result.get("success", False))

    def test_status_missing_task_id(self):
        from tools.a2a_send_tool import a2a_send
        result = json.loads(a2a_send(
            operation="status",
            agent_url="http://localhost:8642",
            task_id=None,
        ))
        self.assertFalse(result.get("success", False))

    def test_tool_registration(self):
        """Verify the tool is registered in the registry."""
        from tools.registry import registry
        entry = registry.get_entry("a2a_send")
        self.assertIsNotNone(entry, "a2a_send not registered in tool registry")
        self.assertEqual(entry.toolset, "delegation")
        schema = entry.schema
        self.assertEqual(schema["name"], "a2a_send")
        self.assertIn("operation", schema["parameters"]["properties"])

    def test_tool_in_delegation_toolset(self):
        """Verify a2a_send is in the delegation toolset."""
        from toolsets import resolve_toolset
        tools = resolve_toolset("delegation")
        self.assertIn("a2a_send", tools)

    def test_no_secrets_in_tool_output(self):
        """Tool output must never contain the api_key passed in."""
        from tools.a2a_send_tool import _send_message
        mock_response = {
            "jsonrpc": "2.0",
            "id": "hermes-123",
            "result": {"id": "t1", "state": "completed", "artifacts": []},
        }
        secret_key = "sk-test-super-secret-key-12345"
        with patch("tools.a2a_send_tool._make_request", return_value=mock_response):
            result_str = _send_message(
                "http://localhost:8642",
                "test message",
                api_key=secret_key,
            )
        self.assertNotIn(secret_key, result_str)


if __name__ == "__main__":
    unittest.main()
