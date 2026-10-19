"""
src/mcp/gmail_tools.py
──────────────────────
Phase 6C — Gmail MCP Tool Adapter.

Dispatches the weekly pulse summary via email using the Model Context Protocol
tool ``gmail_send_email`` provided by the Google Workspace MCP Server.

Two-Phase Safe Confirmation:
By default, the server enforces a confirmation policy (REQUIRE_EMAIL_CONFIRMATION).
This adapter supplies ``confirm_send=True`` by default to fulfill this policy
safely during authorized automated runs, while allowing callers to prompt for
interactive confirmation when needed.

Usage:
    from src.mcp.gmail_tools import send_pulse_email

    result = send_pulse_email(
        recipient="product-team@example.com",
        pulse=pulse,
        doc_url="https://docs.google.com/document/d/1abc.../edit",
    )
    print(result["message_id"])
"""

from __future__ import annotations

import uuid
from typing import Any

from src.intelligence.pulse_generator import Pulse
from src.mcp.client import MCPClient
from src.utils.config import settings
from src.utils.logger import get_logger

logger = get_logger(__name__)


def format_pulse_email_body(pulse: Pulse, doc_url: str = "") -> str:
    """
    Format a Pulse object into a plain-text email body with clear structure
    and an optional link to the full Google Docs journal.

    Args:
        pulse: The validated Pulse object.
        doc_url: URL to the Google Doc where pulse notes are logged.

    Returns:
        Formatted plain-text email string.
    """
    lines = [
        "Hi Team,",
        "",
        f"Here is the Groww Weekly Review Pulse for the week of {pulse.week_of}:",
        "",
        "--------------------------------------------------------------------------------",
        pulse.body.strip(),
        "--------------------------------------------------------------------------------",
    ]

    if doc_url:
        lines.extend([
            "",
            "View Full Historical Document Journal on Google Docs:",
            f"{doc_url.strip()}",
            "",
        ])

    lines.extend([
        "--------------------------------------------------------------------------------",
        "Generated automatically by the Groww Review Intelligence Agent.",
        "Ingested from public Google Play Store reviews with PII strictly scrubbed.",
        "--------------------------------------------------------------------------------",
    ])

    return "\n".join(lines)


def send_pulse_email(
    recipient: str | list[str] | None = None,
    pulse: Pulse | None = None,
    doc_url: str = "",
    confirm_send: bool = True,
    client: MCPClient | None = None,
) -> dict[str, Any]:
    """
    Send the weekly pulse email to recipients via the MCP server.

    Args:
        recipient: Email address string or list of recipient email addresses.
                   Falls back to settings.pulse_email_recipient.
        pulse: Validated Pulse object to format and send.
        doc_url: Optional link to the Google Doc journal to include in the email.
        confirm_send: Explicit user confirmation flag. Defaults to True to fulfill
                      the MCP server's two-phase confirmation requirement.
        client: Optional custom MCPClient instance (for dependency injection).

    Returns:
        dict containing:
            - success (bool)
            - message_id (str)
            - thread_id (str | None)
            - status (str)
            - to (list[str])
            - subject (str)
            - request_id (str)

    Raises:
        ValueError: If recipient is missing or pulse is not provided.
        MCPClientError: If tool execution fails or confirmation is rejected.
    """
    raw_recipient = recipient or settings.pulse_email_recipient
    if not raw_recipient:
        raise ValueError(
            "Recipient email address is required. Please configure PULSE_EMAIL_RECIPIENT "
            "in .env or pass recipient explicitly."
        )

    if pulse is None:
        raise ValueError("Pulse object is required to send email.")

    # Normalize recipients into a list of clean email strings
    if isinstance(raw_recipient, str):
        recipients = [
            addr.strip()
            for addr in raw_recipient.replace(";", ",").split(",")
            if addr.strip()
        ]
    else:
        recipients = [str(addr).strip() for addr in raw_recipient if str(addr).strip()]

    if not recipients:
        raise ValueError("No valid recipient email addresses found.")

    subject = f"Groww Weekly Review Pulse — Week of {pulse.week_of}"
    body = format_pulse_email_body(pulse, doc_url=doc_url)
    request_id = str(uuid.uuid4())

    arguments: dict[str, Any] = {
        "to": recipients,
        "subject": subject,
        "body": body,
        "confirm_send": confirm_send,
        "request_id": request_id,
    }

    logger.info(
        "Sending weekly pulse email via MCP",
        extra={
            "tool": "gmail_send_email",
            "recipients_count": len(recipients),
            "subject": subject,
            "confirm_send": confirm_send,
            "request_id": request_id,
        },
    )

    mcp = client or MCPClient()
    response = mcp.call_tool_sync("gmail_send_email", arguments)

    message_id = response.get("message_id", "unknown")
    thread_id = response.get("thread_id")
    status_msg = response.get("status", "Email sent")

    logger.info(
        "Pulse email sent successfully via MCP",
        extra={
            "message_id": message_id,
            "recipients": recipients,
            "status": status_msg,
        },
    )

    return {
        "success": True,
        "message_id": message_id,
        "thread_id": thread_id,
        "status": status_msg,
        "to": recipients,
        "subject": subject,
        "request_id": request_id,
    }
