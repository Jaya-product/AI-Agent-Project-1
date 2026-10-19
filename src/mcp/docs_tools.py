"""
src/mcp/docs_tools.py
─────────────────────
Phase 6B — Google Docs MCP Tool Adapter.

Publishes the weekly pulse to Google Docs via the Model Context Protocol
tool ``google_docs_append_text`` exposed by the Google Workspace MCP Server.

Non-Destructive Guarantee:
The MCP server uses Google Docs API ``endOfSegmentLocation`` to append
new weekly pulses to the end of the document without modifying, replacing,
or deleting any existing journal entries.

Usage:
    from src.mcp.docs_tools import append_pulse_to_doc

    result = append_pulse_to_doc(doc_id="1abc...", pulse=pulse)
    print(result["doc_url"])
"""

from __future__ import annotations

import uuid
from typing import Any

from src.intelligence.pulse_generator import Pulse
from src.mcp.client import MCPClient
from src.utils.config import settings
from src.utils.logger import get_logger

logger = get_logger(__name__)


def format_pulse_for_docs(pulse: Pulse) -> str:
    """
    Format a Pulse object into a clean, dated journal section suitable
    for non-destructively appending to an ongoing Google Doc log.

    Args:
        pulse: The validated Pulse object to format.

    Returns:
        A formatted string with section dividers and pulse body.
    """
    separator = "=" * 80
    header = f"GROWW WEEKLY REVIEW PULSE — WEEK OF {pulse.week_of}"
    entry = f"\n{separator}\n{header}\n{separator}\n\n{pulse.body.strip()}\n"
    return entry


def append_pulse_to_doc(
    doc_id: str | None = None,
    pulse: Pulse | None = None,
    client: MCPClient | None = None,
) -> dict[str, Any]:
    """
    Append a weekly pulse note to the specified Google Doc via MCP.

    Args:
        doc_id: Google Doc ID or docs.google.com URL. Falls back to settings.pulse_doc_id.
        pulse: The validated Pulse object containing the weekly insights.
        client: Optional custom MCPClient instance (useful for dependency injection / testing).

    Returns:
        dict containing:
            - success (bool)
            - document_id (str)
            - characters_appended (int)
            - status (str)
            - doc_url (str)
            - request_id (str)

    Raises:
        ValueError: If doc_id is missing or pulse is not provided.
        MCPClientError: If the MCP server fails or returns an error.
    """
    target_doc = (doc_id or settings.pulse_doc_id or "").strip()
    if not target_doc:
        raise ValueError(
            "Google Doc ID or URL is required. Please configure PULSE_DOC_ID in "
            ".env or pass doc_id explicitly."
        )

    if pulse is None:
        raise ValueError("Pulse object is required to append to Google Doc.")

    # Format pulse text for doc insertion
    formatted_text = format_pulse_for_docs(pulse)
    request_id = str(uuid.uuid4())

    arguments = {
        "document_id": target_doc,
        "text": formatted_text,
        "prepend_newline": True,
        "request_id": request_id,
    }

    logger.info(
        "Appending pulse to Google Doc via MCP",
        extra={
            "tool": "google_docs_append_text",
            "doc_id": target_doc,
            "text_length": len(formatted_text),
            "request_id": request_id,
        },
    )

    mcp = client or MCPClient()
    response = mcp.call_tool_sync("google_docs_append_text", arguments)

    # Derive accessible Google Docs URL
    if target_doc.startswith("http://") or target_doc.startswith("https://"):
        doc_url = target_doc
    else:
        doc_url = f"https://docs.google.com/document/d/{target_doc}/edit"

    characters_appended = response.get("characters_appended", len(formatted_text))
    status_msg = response.get("status", "Text appended to Google Doc")

    logger.info(
        "Successfully appended pulse to Google Doc",
        extra={
            "doc_id": target_doc,
            "characters_appended": characters_appended,
            "doc_url": doc_url,
        },
    )

    return {
        "success": True,
        "document_id": target_doc,
        "characters_appended": characters_appended,
        "status": status_msg,
        "doc_url": doc_url,
        "request_id": request_id,
    }
