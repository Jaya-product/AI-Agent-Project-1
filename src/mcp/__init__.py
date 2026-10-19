"""
src/mcp
───────
Model Context Protocol (MCP) Integration Module.

Exposes high-level tools to interact with Google Workspace (Google Docs & Gmail)
via the remote MCP Server without bespoke OAuth or REST API wiring.
"""

from src.mcp.client import (
    MCPClient,
    MCPClientError,
    MCPConfirmationRequiredError,
    MCPConnectionError,
    MCPToolCallError,
)
from src.mcp.docs_tools import append_pulse_to_doc, format_pulse_for_docs
from src.mcp.gmail_tools import format_pulse_email_body, send_pulse_email

__all__ = [
    "MCPClient",
    "MCPClientError",
    "MCPConnectionError",
    "MCPToolCallError",
    "MCPConfirmationRequiredError",
    "append_pulse_to_doc",
    "format_pulse_for_docs",
    "send_pulse_email",
    "format_pulse_email_body",
]
