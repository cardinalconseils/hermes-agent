"""
A2A (Agent2Agent) Protocol Plugin for Hermes Agent.

Serves an A2A-compliant Agent Card at /.well-known/agent-card.json
and a JSON-RPC endpoint at /a2a for inter-agent communication.

This makes Hermes agents discoverable and callable by any A2A-compliant
client (Google ADK, LangGraph, CrewAI, etc.) without modifying the core
API server adapter.

Spec reference: https://github.com/a2aproject/A2A/blob/main/docs/specification.md
"""

from plugins.a2a.agent_card import build_agent_card
from plugins.a2a.jsonrpc import register_a2a_routes

__all__ = ["register_a2a_routes", "build_agent_card"]
__version__ = "0.1.0"
