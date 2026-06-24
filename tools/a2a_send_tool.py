"""
A2A Client Tool — discover and call remote A2A agents.

Lets Hermes act as an A2A client: fetch Agent Cards from remote agents,
send tasks, poll status, and receive structured artifacts.

Operations:
  - discover: Fetch and parse an Agent Card from a remote A2A server
  - send:     Send a message/task to a remote agent (blocking by default)
  - status:   Get the status of a previously submitted task
  - list:     List recent tasks on a remote agent
  - cancel:   Cancel a running task on a remote agent

Uses urllib (stdlib) — no external dependencies required.
"""

import json
import logging
import os
import time
import urllib.error
import urllib.request
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

from tools.registry import registry, tool_error

# Default timeout for HTTP requests (seconds)
_DEFAULT_TIMEOUT = 120

# Max response body size (bytes) — 1 MB
_MAX_RESPONSE_SIZE = 1_048_576


def _check_requirements() -> bool:
    """A2A client is always available — it only uses stdlib."""
    return True


def _make_request(
    url: str,
    method: str = "GET",
    body: Optional[Dict[str, Any]] = None,
    api_key: Optional[str] = None,
    timeout: int = _DEFAULT_TIMEOUT,
) -> Dict[str, Any]:
    """Make an HTTP request and return parsed JSON response.

    Raises urllib.error.HTTPError on non-2xx, urllib.error.URLError on
    connection failures.
    """
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=data,
        headers=headers,
        method=method,
    )

    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read(_MAX_RESPONSE_SIZE)
        return json.loads(raw.decode("utf-8"))


def _resolve_agent_url(agent_url: str) -> str:
    """Normalize an agent URL to its base URL (strip trailing slashes)."""
    return agent_url.rstrip("/")


def _get_agent_card_url(base_url: str) -> str:
    """Build the Agent Card URL from a base URL."""
    base = _resolve_agent_url(base_url)
    return f"{base}/.well-known/agent-card.json"


def _get_jsonrpc_url(base_url: str) -> str:
    """Build the JSON-RPC endpoint URL from a base URL."""
    base = _resolve_agent_url(base_url)
    # If the URL already ends with /a2a, use it as-is
    if base.endswith("/a2a"):
        return base
    return f"{base}/a2a"


def _discover_agent(
    agent_url: str,
    api_key: Optional[str] = None,
    timeout: int = 30,
) -> str:
    """Fetch and parse an Agent Card from a remote A2A server.

    Returns a JSON string with the agent card for the LLM to read.
    """
    card_url = _get_agent_card_url(agent_url)
    try:
        card = _make_request(card_url, method="GET", timeout=timeout)
        return json.dumps({
            "success": True,
            "agent_card": card,
            "card_url": card_url,
        }, indent=2)
    except urllib.error.HTTPError as e:
        return tool_error(
            f"Agent Card fetch failed: HTTP {e.code} from {card_url}",
            details=f"Response: {e.read().decode('utf-8', errors='replace')[:500]}",
        )
    except urllib.error.URLError as e:
        return tool_error(
            f"Cannot reach agent at {card_url}: {e.reason}",
        )
    except Exception as e:
        return tool_error(f"Discovery error: {e}")


def _send_message(
    agent_url: str,
    message: str,
    api_key: Optional[str] = None,
    context_id: Optional[str] = None,
    return_immediately: bool = False,
    timeout: int = _DEFAULT_TIMEOUT,
) -> str:
    """Send a message/task to a remote A2A agent.

    Returns a JSON string with the Task object (including artifacts if blocking).
    """
    rpc_url = _get_jsonrpc_url(agent_url)

    # Build A2A Send Message request
    message_obj: Dict[str, Any] = {
        "role": "user",
        "parts": [{"kind": "text", "text": message}],
    }
    if context_id:
        message_obj["contextId"] = context_id

    rpc_body = {
        "jsonrpc": "2.0",
        "id": f"hermes-{int(time.time())}",
        "method": "message/send",
        "params": {
            "message": message_obj,
            "return_immediately": return_immediately,
        },
    }

    try:
        result = _make_request(
            rpc_url,
            method="POST",
            body=rpc_body,
            api_key=api_key,
            timeout=timeout,
        )

        # Check for JSON-RPC error
        if "error" in result:
            err = result["error"]
            return tool_error(
                f"Remote agent error: {err.get('message', 'Unknown error')} "
                f"(code: {err.get('code', 'unknown')})",
            )

        task = result.get("result", {})
        return json.dumps({
            "success": True,
            "task": task,
            "task_id": task.get("id"),
            "state": task.get("state"),
            "artifacts": task.get("artifacts", []),
        }, indent=2)

    except urllib.error.HTTPError as e:
        body_text = ""
        try:
            body_text = e.read().decode("utf-8", errors="replace")[:500]
        except Exception:
            pass
        if e.code == 401:
            return tool_error(
                f"Authentication failed (401). The remote agent requires a valid API key. "
                f"Set it via the api_key parameter.",
            )
        return tool_error(
            f"Send failed: HTTP {e.code} from {rpc_url}",
            details=body_text,
        )
    except urllib.error.URLError as e:
        return tool_error(
            f"Cannot reach agent at {rpc_url}: {e.reason}",
        )
    except Exception as e:
        return tool_error(f"Send error: {e}")


def _get_task_status(
    agent_url: str,
    task_id: str,
    api_key: Optional[str] = None,
    timeout: int = 30,
) -> str:
    """Get the status of a task on a remote A2A agent."""
    rpc_url = _get_jsonrpc_url(agent_url)

    rpc_body = {
        "jsonrpc": "2.0",
        "id": f"hermes-status-{int(time.time())}",
        "method": "task/get",
        "params": {"id": task_id},
    }

    try:
        result = _make_request(
            rpc_url, method="POST", body=rpc_body, api_key=api_key, timeout=timeout,
        )
        if "error" in result:
            err = result["error"]
            return tool_error(f"Remote agent error: {err.get('message', 'Unknown')}")
        task = result.get("result", {})
        return json.dumps({
            "success": True,
            "task": task,
            "state": task.get("state"),
            "artifacts": task.get("artifacts", []),
        }, indent=2)
    except Exception as e:
        return tool_error(f"Status check failed: {e}")


def _list_tasks(
    agent_url: str,
    api_key: Optional[str] = None,
    limit: int = 20,
    timeout: int = 30,
) -> str:
    """List recent tasks on a remote A2A agent."""
    rpc_url = _get_jsonrpc_url(agent_url)

    rpc_body = {
        "jsonrpc": "2.0",
        "id": f"hermes-list-{int(time.time())}",
        "method": "task/list",
        "params": {"limit": limit},
    }

    try:
        result = _make_request(
            rpc_url, method="POST", body=rpc_body, api_key=api_key, timeout=timeout,
        )
        if "error" in result:
            err = result["error"]
            return tool_error(f"Remote agent error: {err.get('message', 'Unknown')}")
        return json.dumps({
            "success": True,
            **result.get("result", {}),
        }, indent=2)
    except Exception as e:
        return tool_error(f"List failed: {e}")


def _cancel_task(
    agent_url: str,
    task_id: str,
    api_key: Optional[str] = None,
    timeout: int = 30,
) -> str:
    """Cancel a running task on a remote A2A agent."""
    rpc_url = _get_jsonrpc_url(agent_url)

    rpc_body = {
        "jsonrpc": "2.0",
        "id": f"hermes-cancel-{int(time.time())}",
        "method": "task/cancel",
        "params": {"id": task_id},
    }

    try:
        result = _make_request(
            rpc_url, method="POST", body=rpc_body, api_key=api_key, timeout=timeout,
        )
        if "error" in result:
            err = result["error"]
            return tool_error(f"Remote agent error: {err.get('message', 'Unknown')}")
        task = result.get("result", {})
        return json.dumps({
            "success": True,
            "task": task,
            "state": task.get("state"),
        }, indent=2)
    except Exception as e:
        return tool_error(f"Cancel failed: {e}")


def a2a_send(
    operation: str,
    agent_url: str,
    message: Optional[str] = None,
    task_id: Optional[str] = None,
    api_key: Optional[str] = None,
    context_id: Optional[str] = None,
    return_immediately: bool = False,
    limit: int = 20,
    timeout: int = _DEFAULT_TIMEOUT,
) -> str:
    """A2A (Agent2Agent) client tool.

    Discover remote A2A agents, send tasks, and receive structured artifacts.
    This lets Hermes collaborate with specialist agents running on other
    A2A-compliant systems (Google ADK, LangGraph, CrewAI, other Hermes instances).

    Operations:
      - "discover": Fetch Agent Card from agent_url (e.g. http://host:8642)
      - "send":     Send message to agent, get back Task with artifacts
      - "status":   Get status of a task by task_id
      - "list":     List recent tasks on the agent
      - "cancel":   Cancel a running task by task_id

    Args:
        operation: One of "discover", "send", "status", "list", "cancel"
        agent_url: Base URL of the remote agent (e.g. http://localhost:8642)
        message:   Text message to send (required for "send" operation)
        task_id:   Task ID (required for "status" and "cancel")
        api_key:   Bearer token if the remote agent requires auth
        context_id: Optional context ID for conversation continuity
        return_immediately: If true, return task in working state without waiting
        limit:     Max tasks to return for "list" operation (default 20)
        timeout:   Request timeout in seconds (default 120)

    Returns:
        JSON string with the operation result.
    """
    if not agent_url:
        return tool_error("agent_url is required")

    if operation == "discover":
        return _discover_agent(agent_url, api_key=api_key, timeout=min(timeout, 30))

    elif operation == "send":
        if not message:
            return tool_error("message is required for 'send' operation")
        return _send_message(
            agent_url, message,
            api_key=api_key,
            context_id=context_id,
            return_immediately=return_immediately,
            timeout=timeout,
        )

    elif operation == "status":
        if not task_id:
            return tool_error("task_id is required for 'status' operation")
        return _get_task_status(agent_url, task_id, api_key=api_key, timeout=timeout)

    elif operation == "list":
        return _list_tasks(agent_url, api_key=api_key, limit=limit, timeout=timeout)

    elif operation == "cancel":
        if not task_id:
            return tool_error("task_id is required for 'cancel' operation")
        return _cancel_task(agent_url, task_id, api_key=api_key, timeout=timeout)

    else:
        return tool_error(
            f"Unknown operation '{operation}'. Use: discover, send, status, list, cancel",
        )


# --- Schema ---

A2A_SEND_SCHEMA = {
    "name": "a2a_send",
    "description": (
        "A2A (Agent2Agent) client — discover and collaborate with remote AI agents. "
        "Fetch Agent Cards to understand what an agent can do, send messages/tasks to "
        "delegate work, poll task status, and receive structured artifacts back. "
        "Works with any A2A-compliant agent (Hermes, Google ADK, LangGraph, CrewAI). "
        "MCP connects agents to tools; A2A connects agents to other agents."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "description": (
                    "Operation to perform: "
                    "'discover' (fetch Agent Card), "
                    "'send' (send message/task to agent), "
                    "'status' (check task status), "
                    "'list' (list recent tasks), "
                    "'cancel' (cancel a task)"
                ),
                "enum": ["discover", "send", "status", "list", "cancel"],
            },
            "agent_url": {
                "type": "string",
                "description": (
                    "Base URL of the remote A2A agent (e.g. http://localhost:8642). "
                    "For 'discover', the Agent Card is fetched from "
                    "{agent_url}/.well-known/agent-card.json"
                ),
            },
            "message": {
                "type": "string",
                "description": (
                    "Text message to send to the remote agent. "
                    "Required for 'send' operation."
                ),
            },
            "task_id": {
                "type": "string",
                "description": (
                    "Task ID returned from a previous 'send' operation. "
                    "Required for 'status' and 'cancel' operations."
                ),
            },
            "api_key": {
                "type": "string",
                "description": (
                    "Bearer token for authenticating with the remote agent. "
                    "Required if the remote agent's Agent Card lists 'Bearer' auth. "
                    "Optional for agents with 'None' auth."
                ),
            },
            "context_id": {
                "type": "string",
                "description": (
                    "Optional context ID for conversation continuity across "
                    "multiple tasks with the same remote agent."
                ),
            },
            "return_immediately": {
                "type": "boolean",
                "description": (
                    "If true, return the task in 'working' state immediately "
                    "without waiting for the agent to finish. Use 'status' to "
                    "poll for completion. Default: false (blocking)."
                ),
            },
            "limit": {
                "type": "integer",
                "description": "Max tasks to return for 'list' operation. Default: 20.",
                "default": 20,
            },
            "timeout": {
                "type": "integer",
                "description": "Request timeout in seconds. Default: 120.",
                "default": 120,
            },
        },
        "required": ["operation", "agent_url"],
    },
}


# --- Registry ---

registry.register(
    name="a2a_send",
    toolset="delegation",
    schema=A2A_SEND_SCHEMA,
    handler=lambda args, **kw: a2a_send(
        operation=args.get("operation", ""),
        agent_url=args.get("agent_url", ""),
        message=args.get("message"),
        task_id=args.get("task_id"),
        api_key=args.get("api_key"),
        context_id=args.get("context_id"),
        return_immediately=args.get("return_immediately", False),
        limit=args.get("limit", 20),
        timeout=args.get("timeout", _DEFAULT_TIMEOUT),
    ),
    check_fn=_check_requirements,
    emoji="🤝",
)
