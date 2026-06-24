"""
A2A Agent Card generation from live Hermes state.

The Agent Card is a JSON document describing the agent's identity,
capabilities, skills, and endpoint URL. It follows the A2A spec:
https://agent2agent.info/docs/concepts/agentcard/

Served at /.well-known/agent-card.json — public, no auth required.
"""

import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _get_hermes_home() -> Path:
    """Resolve HERMES_HOME, same pattern as hermes_constants.get_hermes_home."""
    override = os.environ.get("HERMES_HOME")
    if override:
        return Path(override)
    return Path.home() / ".hermes"


def _extract_identity_from_soul(soul_content: str) -> Dict[str, str]:
    """Parse name and description from SOUL.md content.

    Looks for ## Identity section. Falls back to first non-empty line.
    Never raises — returns defaults if parsing fails.
    """
    result = {"name": "Hermes Agent", "description": ""}
    if not soul_content:
        return result

    try:
        # Try to extract the Identity section
        match = re.search(
            r"##\s*Identity\s*\n(.+?)(?=\n##\s|\Z)",
            soul_content,
            re.DOTALL | re.IGNORECASE,
        )
        if match:
            identity_text = match.group(1).strip()
            # First sentence/line as name, rest as description
            lines = [l.strip() for l in identity_text.split("\n") if l.strip()]
            if lines:
                result["name"] = lines[0][:120]
                if len(lines) > 1:
                    result["description"] = " ".join(lines[1:])[:500]
                else:
                    result["description"] = lines[0][:500]
        else:
            # Fallback: use first non-empty, non-header line
            for line in soul_content.split("\n"):
                line = line.strip()
                if line and not line.startswith("#") and not line.startswith("---"):
                    result["name"] = line[:120]
                    result["description"] = line[:500]
                    break
    except Exception as e:
        logger.debug("Failed to parse SOUL.md for agent card: %s", e)

    return result


def _load_soul_content() -> str:
    """Load SOUL.md from HERMES_HOME. Returns empty string if not found."""
    try:
        soul_path = _get_hermes_home() / "SOUL.md"
        if soul_path.exists():
            return soul_path.read_text(encoding="utf-8").strip()
    except Exception as e:
        logger.debug("Could not read SOUL.md: %s", e)
    return ""


def _get_hermes_version() -> str:
    """Return hermes-agent version, or 'dev' if unresolved."""
    try:
        from importlib.metadata import version
        return version("hermes-agent")
    except Exception:
        pass
    try:
        from hermes_cli import __version__
        return __version__
    except Exception:
        return "dev"


def _get_skills_list() -> List[Dict[str, Any]]:
    """Return installed skills as A2A skill objects.

    Each skill: { id, name, description, tags, examples }
    Never raises — returns empty list on failure.
    """
    skills: List[Dict[str, Any]] = []
    try:
        from tools.skills_tool import _find_all_skills, _sort_skills
        raw_skills = _sort_skills(_find_all_skills(skip_disabled=True))
        for s in raw_skills:
            name = s.get("name", "")
            if not name:
                continue
            skills.append({
                "id": name,
                "name": s.get("name", name),
                "description": s.get("description", "")[:500],
                "tags": s.get("tags", []) if isinstance(s.get("tags"), list) else [],
                "examples": [],
            })
    except Exception as e:
        logger.debug("Could not enumerate skills for agent card: %s", e)
    return skills


def _get_base_url(host: str, port: int) -> str:
    """Build the base URL for the agent endpoint."""
    # If host is 0.0.0.0, use localhost for the card (clients resolve their own address)
    display_host = "localhost" if host in ("0.0.0.0", "::", "") else host
    return f"http://{display_host}:{port}"


def build_agent_card(
    host: str = "127.0.0.1",
    port: int = 8642,
    api_key_set: bool = False,
) -> Dict[str, Any]:
    """Build an A2A Agent Card from live Hermes state.

    Args:
        host: API server bind host
        port: API server bind port
        api_key_set: whether API_SERVER_KEY is configured

    Returns:
        Dict matching the A2A AgentCard schema.
    """
    soul_content = _load_soul_content()
    identity = _extract_identity_from_soul(soul_content)
    base_url = _get_base_url(host, port)
    version = _get_hermes_version()
    skills = _get_skills_list()

    # Authentication: Bearer if key is set, otherwise None (public)
    if api_key_set:
        auth = {"schemes": ["Bearer"]}
    else:
        auth = {"schemes": ["None"]}

    # Capabilities: Hermes supports SSE streaming via /v1/runs
    capabilities = {
        "streaming": True,
        "pushNotifications": False,
        "stateTransitionHistory": False,
    }

    # Default interaction modes
    default_input_modes = ["text/plain"]
    default_output_modes = ["text/plain"]

    # Build the service URL — points to the JSON-RPC endpoint
    service_url = f"{base_url}/a2a"

    # Documentation URL
    doc_url = "https://hermes-agent.nousresearch.com/docs/"

    # Provider info
    provider = {
        "organization": "Nous Research",
        "url": "https://nousresearch.com",
    }

    card: Dict[str, Any] = {
        "name": identity["name"],
        "description": identity["description"] or (
            "Hermes Agent — an autonomous AI agent framework by Nous Research. "
            "Capable of software development, research, system administration, "
            "and multi-platform task execution."
        ),
        "url": service_url,
        "provider": provider,
        "version": version,
        "documentationUrl": doc_url,
        "capabilities": capabilities,
        "authentication": auth,
        "defaultInputModes": default_input_modes,
        "defaultOutputModes": default_output_modes,
        "skills": skills,
    }

    return card
