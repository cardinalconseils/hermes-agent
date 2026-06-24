"""
Tests for the A2A plugin — Agent Card generation, data models, and JSON-RPC handlers.

Run: python -m pytest tests/plugins/test_a2a.py -v -o 'addopts='
"""

import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch, AsyncMock

# Ensure the source tree is on the path
_src = Path(__file__).resolve().parents[2]
if str(_src) not in sys.path:
    sys.path.insert(0, str(_src))


class TestAgentCard(unittest.TestCase):
    """Test Agent Card generation from live Hermes state."""

    def test_card_has_required_fields(self):
        from plugins.a2a.agent_card import build_agent_card
        card = build_agent_card(host="127.0.0.1", port=8642, api_key_set=True)
        required = ["name", "description", "url", "version", "capabilities",
                     "authentication", "defaultInputModes", "defaultOutputModes", "skills"]
        for field in required:
            self.assertIn(field, card, f"Missing required field: {field}")

    def test_card_url_points_to_a2a_endpoint(self):
        from plugins.a2a.agent_card import build_agent_card
        card = build_agent_card(host="localhost", port=9999, api_key_set=False)
        self.assertEqual(card["url"], "http://localhost:9999/a2a")

    def test_card_0_0_0_0_host_normalized(self):
        from plugins.a2a.agent_card import build_agent_card
        card = build_agent_card(host="0.0.0.0", port=8642, api_key_set=True)
        self.assertIn("localhost", card["url"])

    def test_card_auth_bearer_when_key_set(self):
        from plugins.a2a.agent_card import build_agent_card
        card = build_agent_card(host="127.0.0.1", port=8642, api_key_set=True)
        self.assertIn("Bearer", card["authentication"]["schemes"])

    def test_card_auth_none_when_no_key(self):
        from plugins.a2a.agent_card import build_agent_card
        card = build_agent_card(host="127.0.0.1", port=8642, api_key_set=False)
        self.assertIn("None", card["authentication"]["schemes"])

    def test_card_capabilities(self):
        from plugins.a2a.agent_card import build_agent_card
        card = build_agent_card(host="127.0.0.1", port=8642, api_key_set=True)
        self.assertTrue(card["capabilities"]["streaming"])
        self.assertFalse(card["capabilities"]["pushNotifications"])

    def test_card_provider_info(self):
        from plugins.a2a.agent_card import build_agent_card
        card = build_agent_card()
        self.assertEqual(card["provider"]["organization"], "Nous Research")

    def test_card_no_secrets(self):
        """Agent Card must never contain API keys, tokens, or actual secret values."""
        from plugins.a2a.agent_card import build_agent_card
        card_json = json.dumps(build_agent_card(api_key_set=True))
        # No actual credential values or key assignments
        forbidden_patterns = [
            "sk_live_", "sk_test_", "ghp_", "gho_",
            "AWS_SECRET_ACCESS_KEY=", "OPENAI_API_KEY=",
            "ANTHROPIC_API_KEY=", "STRIPE_SECRET_KEY=",
            "API_SERVER_KEY=", "Bearer sk-",
            "BEGIN PRIVATE KEY", "BEGIN RSA",
        ]
        for pattern in forbidden_patterns:
            self.assertNotIn(pattern, card_json, f"Secret pattern '{pattern}' found in Agent Card JSON")

    def test_card_name_from_soul(self):
        """Agent Card name should be extracted from SOUL.md if present."""
        from plugins.a2a.agent_card import _extract_identity_from_soul
        soul = """# Hermes

## Identity

Murphy is the always-on assistant for PMC. Ships code, manages infra.
"""
        identity = _extract_identity_from_soul(soul)
        self.assertIn("Murphy", identity["name"])

    def test_card_name_fallback_without_soul(self):
        """If no SOUL.md, name defaults to 'Hermes Agent'."""
        from plugins.a2a.agent_card import _extract_identity_from_soul
        identity = _extract_identity_from_soul("")
        self.assertEqual(identity["name"], "Hermes Agent")


class TestA2AModels(unittest.TestCase):
    """Test A2A data model types."""

    def test_task_lifecycle(self):
        from plugins.a2a.models import Task, TASK_STATE_SUBMITTED, TASK_STATE_WORKING, TASK_STATE_COMPLETED
        task = Task(state=TASK_STATE_SUBMITTED)
        task.transition(TASK_STATE_WORKING)
        self.assertEqual(task.state, TASK_STATE_WORKING)
        task.transition(TASK_STATE_COMPLETED, message="Done")
        self.assertEqual(task.state, TASK_STATE_COMPLETED)
        self.assertEqual(task.status_message, "Done")

    def test_task_cannot_leave_terminal_state(self):
        from plugins.a2a.models import Task, TASK_STATE_COMPLETED, TASK_STATE_WORKING
        task = Task(state=TASK_STATE_COMPLETED)
        with self.assertRaises(ValueError):
            task.transition(TASK_STATE_WORKING)

    def test_message_roundtrip(self):
        from plugins.a2a.models import Message, Part
        msg = Message(
            role="user",
            parts=[Part(kind="text", text="Hello agent")],
        )
        d = msg.to_dict()
        self.assertEqual(d["role"], "user")
        self.assertEqual(d["parts"][0]["text"], "Hello agent")

        # Roundtrip
        msg2 = Message.from_dict(d)
        self.assertEqual(msg2.role, "user")
        self.assertEqual(msg2.parts[0].text, "Hello agent")

    def test_artifact_to_dict(self):
        from plugins.a2a.models import Artifact, Part
        art = Artifact(
            name="risk_score",
            description="Fraud risk assessment",
            parts=[Part(kind="text", text="LOW")],
        )
        d = art.to_dict()
        self.assertEqual(d["name"], "risk_score")
        self.assertEqual(d["parts"][0]["text"], "LOW")

    def test_task_to_dict(self):
        from plugins.a2a.models import Task, TASK_STATE_COMPLETED
        task = Task(state=TASK_STATE_COMPLETED, context_id="ctx-1")
        d = task.to_dict()
        self.assertEqual(d["state"], "completed")
        self.assertEqual(d["contextId"], "ctx-1")
        self.assertIn("id", d)
        self.assertIn("createdAt", d)
        self.assertIn("updatedAt", d)


class TestJsonRPC(unittest.TestCase):
    """Test JSON-RPC handler logic (without running a real agent)."""

    def test_jsonrpc_error_response(self):
        from plugins.a2a.jsonrpc import _jsonrpc_error
        # Mock web since aiohttp might not be importable in test env
        with patch("plugins.a2a.jsonrpc.web") as mock_web:
            mock_resp = MagicMock()
            mock_web.json_response.return_value = mock_resp
            result = _jsonrpc_error(-32601, "Not found", "req-1")
            mock_web.json_response.assert_called_once()
            call_args = mock_web.json_response.call_args[0][0]
            self.assertEqual(call_args["error"]["code"], -32601)
            self.assertEqual(call_args["id"], "req-1")

    def test_extract_text_from_message(self):
        from plugins.a2a.jsonrpc import _extract_text_from_message
        from plugins.a2a.models import Message, Part
        msg = Message(
            role="user",
            parts=[
                Part(kind="text", text="Line 1"),
                Part(kind="text", text="Line 2"),
                Part(kind="data", data={"key": "value"}),  # ignored
            ],
        )
        text = _extract_text_from_message(msg)
        self.assertEqual(text, "Line 1\nLine 2")

    def test_text_to_artifact(self):
        from plugins.a2a.jsonrpc import _text_to_artifact
        art = _text_to_artifact("Hello world", name="greeting")
        self.assertEqual(art.name, "greeting")
        self.assertEqual(art.parts[0].text, "Hello world")

    def test_task_store_eviction(self):
        from plugins.a2a.jsonrpc import _TASK_STORE, _evict_old_tasks, _MAX_STORED_TASKS
        from plugins.a2a.models import Task, TASK_STATE_COMPLETED
        import time as _time

        # Clear store
        _TASK_STORE.clear()

        # Add some old completed tasks
        for i in range(5):
            t = Task(state=TASK_STATE_COMPLETED)
            t.updated_at = _time.time() - 7200  # 2 hours ago (expired)
            _TASK_STORE[f"old-{i}"] = t

        # Add a recent task
        recent = Task(state=TASK_STATE_COMPLETED)
        _TASK_STORE["recent"] = recent

        _evict_old_tasks()

        # Old tasks should be evicted
        for i in range(5):
            self.assertNotIn(f"old-{i}", _TASK_STORE)
        # Recent task should survive
        self.assertIn("recent", _TASK_STORE)


class TestAgentCardNoSecretLeak(unittest.TestCase):
    """Security-focused test: ensure no secrets leak in any A2A output."""

    def test_full_card_json_no_secret_patterns(self):
        from plugins.a2a.agent_card import build_agent_card
        card = build_agent_card(host="127.0.0.1", port=8642, api_key_set=True)
        raw = json.dumps(card, indent=2)

        # Common secret patterns that must NEVER appear
        forbidden = [
            "sk_live_", "sk_test_", "ghp_", "gho_",
            "AWS_SECRET", "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
            "STRIPE_SECRET", "API_SERVER_KEY=",
            "BEGIN PRIVATE KEY", "BEGIN RSA",
        ]
        for pattern in forbidden:
            self.assertNotIn(pattern, raw, f"Secret pattern '{pattern}' found in Agent Card JSON")


if __name__ == "__main__":
    unittest.main()
