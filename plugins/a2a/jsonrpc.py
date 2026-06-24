"""
A2A JSON-RPC endpoint handlers.

Registers routes on the existing API server's aiohttp app:
  - GET  /.well-known/agent-card.json  → public Agent Card (no auth)
  - POST /a2a                           → JSON-RPC 2.0 endpoint (auth required)

Supported JSON-RPC methods:
  - message/send   → create task, run agent, return Task
  - task/get       → get task status by id
  - task/list      → list recent tasks
  - task/cancel    → cancel a running task

The agent dispatch reuses APIServerAdapter._run_agent(), the same path
as /v1/chat/completions. The response is wrapped as an A2A Task with
an Artifact containing the agent's text output.
"""

import asyncio
import hmac
import json
import logging
import time
import uuid
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

try:
    from aiohttp import web
    AIOHTTP_AVAILABLE = True
except ImportError:
    AIOHTTP_AVAILABLE = False
    web = None  # type: ignore[assignment]

from plugins.a2a.agent_card import build_agent_card
from plugins.a2a.models import (
    Task,
    Message,
    Artifact,
    Part,
    TASK_STATE_SUBMITTED,
    TASK_STATE_WORKING,
    TASK_STATE_COMPLETED,
    TASK_STATE_FAILED,
    TASK_STATE_CANCELED,
    TERMINAL_STATES,
)

# In-memory task store (MVP — sufficient for single-instance deployments).
# Keyed by task_id. Tasks expire after _TASK_TTL_SECONDS.
_TASK_STORE: Dict[str, Task] = {}
_TASK_TTL_SECONDS = 3600  # 1 hour

# Max tasks to retain in memory (FIFO eviction)
_MAX_STORED_TASKS = 100


def _evict_old_tasks() -> None:
    """Evict expired or excess tasks from the store."""
    now = time.time()
    # Remove expired terminal tasks
    expired = [
        tid for tid, t in _TASK_STORE.items()
        if t.state in TERMINAL_STATES and (now - t.updated_at) > _TASK_TTL_SECONDS
    ]
    for tid in expired:
        del _TASK_STORE[tid]

    # FIFO eviction if still over limit
    if len(_TASK_STORE) > _MAX_STORED_TASKS:
        sorted_ids = sorted(_TASK_STORE.keys(), key=lambda i: _TASK_STORE[i].updated_at)
        while len(_TASK_STORE) > _MAX_STORED_TASKS:
            del _TASK_STORE[sorted_ids.pop(0)]


def _jsonrpc_response(result: Any, request_id: Any = None) -> "web.Response":
    """Build a JSON-RPC 2.0 success response."""
    return web.json_response({
        "jsonrpc": "2.0",
        "id": request_id,
        "result": result,
    })


def _jsonrpc_error(code: int, message: str, request_id: Any = None) -> "web.Response":
    """Build a JSON-RPC 2.0 error response."""
    return web.json_response({
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    })


def _extract_text_from_message(msg: Message) -> str:
    """Extract all text parts from an A2A Message into a single string."""
    texts = []
    for part in msg.parts:
        if part.kind == "text" and part.text:
            texts.append(part.text)
    return "\n".join(texts)


def _text_to_artifact(text: str, name: str = "response") -> Artifact:
    """Wrap agent response text into an A2A Artifact."""
    return Artifact(
        name=name,
        description="Agent response",
        parts=[Part(kind="text", text=text)],
    )


def register_a2a_routes(app: "web.Application", adapter: Any) -> None:
    """Register A2A routes on an existing aiohttp app.

    Called by APIServerAdapter after its native routes are set up.
    The adapter must be stored in app["api_server_adapter"].

    Args:
        app: The aiohttp web.Application
        adapter: The APIServerAdapter instance
    """
    if not AIOHTTP_AVAILABLE:
        logger.warning("aiohttp not available — A2A routes not registered")
        return

    # Public Agent Card — no auth required (standard discovery path)
    app.router.add_get(
        "/.well-known/agent-card.json",
        lambda req: _handle_agent_card(req, adapter),
    )

    # JSON-RPC endpoint — auth required
    app.router.add_post("/a2a", lambda req: _handle_jsonrpc(req, adapter))

    logger.info("A2A routes registered: /.well-known/agent-card.json, /a2a")


async def _handle_agent_card(request: "web.Request", adapter: Any) -> "web.Response":
    """GET /.well-known/agent-card.json — serve the public Agent Card.

    No authentication required. This follows the A2A spec convention
    that Agent Cards are public discovery documents (like robots.txt).
    """
    try:
        card = build_agent_card(
            host=adapter._host,
            port=adapter._port,
            api_key_set=bool(adapter._api_key),
        )
        return web.json_response(
            card,
            headers={"Cache-Control": "public, max-age=300"},
        )
    except Exception as e:
        logger.exception("Failed to build agent card: %s", e)
        return web.json_response(
            {"error": "Failed to build agent card"},
            status=500,
        )


async def _handle_jsonrpc(request: "web.Request", adapter: Any) -> "web.Response":
    """POST /a2a — JSON-RPC 2.0 endpoint for A2A operations.

    Auth: requires Bearer token matching API_SERVER_KEY.
    """
    # Auth check — reuse the adapter's existing method
    auth_err = adapter._check_auth(request)
    if auth_err:
        return auth_err

    # Parse JSON-RPC request
    try:
        body = await request.json()
    except Exception:
        return _jsonrpc_error(-32700, "Parse error")

    # Support both single request and batch
    if isinstance(body, list):
        # Batch request — process each (MVP: process first only)
        if not body:
            return _jsonrpc_error(-32600, "Invalid Request")
        body = body[0]

    if not isinstance(body, dict):
        return _jsonrpc_error(-32600, "Invalid Request")

    method = body.get("method", "")
    params = body.get("params", {})
    request_id = body.get("id")

    # Route to handler
    try:
        if method == "message/send":
            return await _handle_message_send(params, request_id, adapter)
        elif method == "task/get":
            return await _handle_task_get(params, request_id)
        elif method == "task/list":
            return await _handle_task_list(params, request_id)
        elif method == "task/cancel":
            return await _handle_task_cancel(params, request_id)
        else:
            return _jsonrpc_error(-32601, f"Method not found: {method}", request_id)
    except Exception as e:
        logger.exception("A2A JSON-RPC error in method '%s': %s", method, e)
        return _jsonrpc_error(-32603, f"Internal error: {e}", request_id)


async def _handle_message_send(
    params: Dict[str, Any],
    request_id: Any,
    adapter: Any,
) -> "web.Response":
    """Handle message/send — create a task, run the agent, return Task.

    Params:
        message: A2A Message object with role and parts
        return_immediately: bool — if true, return task in "working" state
                          without blocking for agent completion
    """
    message_data = params.get("message")
    if not message_data or not isinstance(message_data, dict):
        return _jsonrpc_error(-32602, "Missing or invalid 'message' parameter", request_id)

    return_immediately = params.get("return_immediately", False)

    # Parse the A2A message
    try:
        msg = Message.from_dict(message_data)
    except Exception as e:
        return _jsonrpc_error(-32602, f"Invalid message format: {e}", request_id)

    user_text = _extract_text_from_message(msg)
    if not user_text.strip():
        return _jsonrpc_error(-32602, "Message contains no text content", request_id)

    # Create the task
    task = Task(
        state=TASK_STATE_SUBMITTED,
        context_id=msg.context_id,
        messages=[msg],
    )
    _TASK_STORE[task.task_id] = task
    _evict_old_tasks()

    if return_immediately:
        # Non-blocking: return task in working state, run agent in background
        task.transition(TASK_STATE_WORKING)
        asyncio.create_task(_run_agent_for_task(task, user_text, adapter))
        return _jsonrpc_response(task.to_dict(), request_id)

    # Blocking: run agent and return completed task
    task.transition(TASK_STATE_WORKING)
    try:
        result, usage = await adapter._run_agent(
            user_message=user_text,
            conversation_history=[],
            session_id=task.task_id,
        )

        # Extract agent response text
        response_text = ""
        if isinstance(result, dict):
            response_text = result.get("response", "") or result.get("content", "")
            if not response_text:
                # Try OpenAI-style choices
                choices = result.get("choices", [])
                if choices and isinstance(choices, list):
                    response_text = choices[0].get("message", {}).get("content", "")

        if not response_text:
            response_text = json.dumps(result) if result else "(no response)"

        # Create artifact from response
        artifact = _text_to_artifact(response_text)
        task.artifacts.append(artifact)

        # Add agent response message
        agent_msg = Message(
            role="agent",
            parts=[Part(kind="text", text=response_text)],
            task_id=task.task_id,
            context_id=task.context_id,
        )
        task.messages.append(agent_msg)

        task.transition(TASK_STATE_COMPLETED, message="Task completed successfully")

    except asyncio.CancelledError:
        task.transition(TASK_STATE_CANCELED, message="Task was canceled")
        raise
    except Exception as e:
        logger.exception("A2A agent run failed: %s", e)
        task.transition(TASK_STATE_FAILED, message=f"Agent error: {e}")

    _evict_old_tasks()
    return _jsonrpc_response(task.to_dict(), request_id)


async def _run_agent_for_task(
    task: Task,
    user_text: str,
    adapter: Any,
) -> None:
    """Background task runner for non-blocking message/send.

    Runs the agent and updates the task in-place. Errors are logged
    and reflected in the task state.
    """
    try:
        result, usage = await adapter._run_agent(
            user_message=user_text,
            conversation_history=[],
            session_id=task.task_id,
        )

        response_text = ""
        if isinstance(result, dict):
            response_text = result.get("response", "") or result.get("content", "")
            if not response_text:
                choices = result.get("choices", [])
                if choices and isinstance(choices, list):
                    response_text = choices[0].get("message", {}).get("content", "")

        if not response_text:
            response_text = json.dumps(result) if result else "(no response)"

        artifact = _text_to_artifact(response_text)
        task.artifacts.append(artifact)

        agent_msg = Message(
            role="agent",
            parts=[Part(kind="text", text=response_text)],
            task_id=task.task_id,
            context_id=task.context_id,
        )
        task.messages.append(agent_msg)

        task.transition(TASK_STATE_COMPLETED, message="Task completed successfully")
    except asyncio.CancelledError:
        task.transition(TASK_STATE_CANCELED, message="Task was canceled")
    except Exception as e:
        logger.exception("A2A background agent run failed: %s", e)
        task.transition(TASK_STATE_FAILED, message=f"Agent error: {e}")


async def _handle_task_get(
    params: Dict[str, Any],
    request_id: Any,
) -> "web.Response":
    """Handle task/get — return current task state by id.

    Params:
        id: task ID string
    """
    task_id = params.get("id")
    if not task_id:
        return _jsonrpc_error(-32602, "Missing 'id' parameter", request_id)

    task = _TASK_STORE.get(task_id)
    if not task:
        return _jsonrpc_error(-32001, f"Task not found: {task_id}", request_id)

    return _jsonrpc_response(task.to_dict(), request_id)


async def _handle_task_list(
    params: Dict[str, Any],
    request_id: Any,
) -> "web.Response":
    """Handle task/list — list recent tasks.

    Params:
        limit: int (optional, default 20, max 100)
        state: str (optional, filter by state)
    """
    limit = min(int(params.get("limit", 20)), 100)
    state_filter = params.get("state")

    tasks = list(_TASK_STORE.values())
    # Sort by updated_at descending
    tasks.sort(key=lambda t: t.updated_at, reverse=True)

    if state_filter:
        tasks = [t for t in tasks if t.state == state_filter]

    tasks = tasks[:limit]
    return _jsonrpc_response(
        {"tasks": [t.to_dict() for t in tasks], "count": len(tasks)},
        request_id,
    )


async def _handle_task_cancel(
    params: Dict[str, Any],
    request_id: Any,
) -> "web.Response":
    """Handle task/cancel — cancel a running task.

    Params:
        id: task ID string
    """
    task_id = params.get("id")
    if not task_id:
        return _jsonrpc_error(-32602, "Missing 'id' parameter", request_id)

    task = _TASK_STORE.get(task_id)
    if not task:
        return _jsonrpc_error(-32001, f"Task not found: {task_id}", request_id)

    if task.state in TERMINAL_STATES:
        return _jsonrpc_error(
            -32002,
            f"Cannot cancel task in terminal state: {task.state}",
            request_id,
        )

    # For MVP, we mark as canceled. Full cancel of in-flight agent
    # would require interrupting the AIAgent via agent_ref.
    task.transition(TASK_STATE_CANCELED, message="Task canceled by client")
    return _jsonrpc_response(task.to_dict(), request_id)
