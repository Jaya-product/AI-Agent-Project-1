"""
tests/test_mcp_client.py
────────────────────────
Unit tests for Phase 6 — MCP Integration Layer.

Coverage:
  - MCPClient:
      * SSE URL resolution and normalization
      * Header generation with/without Bearer API key
      * check_health() success and error handling (MCPConnectionError)
      * list_tools() async and sync execution with mocked session
      * call_tool() async and sync execution with mocked session
      * Structured error handling: MCPToolCallError, MCPConfirmationRequiredError
      * Network error handling: MCPConnectionError
  - docs_tools:
      * format_pulse_for_docs() output structure
      * append_pulse_to_doc() argument validation (missing doc_id, missing pulse)
      * append_pulse_to_doc() payload construction and URL normalization
      * append_pulse_to_doc() propagation of MCP errors
  - gmail_tools:
      * format_pulse_email_body() structure with and without doc_url
      * send_pulse_email() argument validation (missing recipient, missing pulse)
      * send_pulse_email() recipient normalization (string, comma-separated, list)
      * send_pulse_email() payload construction (subject, confirm_send, request_id)
      * send_pulse_email() error propagation (confirmation required, tool call failure)

All tests use mocks — no network requests are made during testing.
"""

from __future__ import annotations

import json
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from mcp.types import TextContent, Tool

from src.intelligence.clusterer import Theme
from src.intelligence.pulse_generator import Pulse
from src.mcp.client import (
    MCPClient,
    MCPClientError,
    MCPConfirmationRequiredError,
    MCPConnectionError,
    MCPToolCallError,
    _run_sync,
)
from src.mcp.docs_tools import append_pulse_to_doc, format_pulse_for_docs
from src.mcp.gmail_tools import format_pulse_email_body, send_pulse_email


# ── Test Fixtures ─────────────────────────────────────────────────────────────

@pytest.fixture
def sample_pulse() -> Pulse:
    """Returns a valid Pulse object for testing."""
    theme1 = Theme(name="UI/UX & Ease of Use", review_count=80, reviews=[], sentiment="positive", top_quotes=["Great app!"])
    theme2 = Theme(name="Brokerage Charges", review_count=50, reviews=[], sentiment="negative", top_quotes=["Fees are high."])
    theme3 = Theme(name="App Bugs & Hanging", review_count=30, reviews=[], sentiment="negative", top_quotes=["Crashes on charts."])

    body_text = (
        "# Groww Weekly Review Pulse — Week of 2026-10-14\n\n"
        "## Top Themes\n"
        "1. UI/UX & Ease of Use — 80 reviews\n"
        "2. Brokerage Charges — 50 reviews\n"
        "3. App Bugs & Hanging — 30 reviews\n\n"
        "## What Users Are Saying\n"
        "> \"Great app for beginners!\"\n"
        "> \"Flat fee of Rs 20 is too much.\"\n"
        "> \"Chart settings reset on update.\"\n\n"
        "## Recommended Actions\n"
        "1. Fix chart reset bugs on Android.\n"
        "2. Clarify DP and brokerage charges.\n"
        "3. Highlight zero fee on mutual funds."
    )

    return Pulse(
        week_of=date(2026, 10, 14),
        top_themes=[theme1, theme2, theme3],
        quotes=[
            "Great app for beginners!",
            "Flat fee of Rs 20 is too much.",
            "Chart settings reset on update.",
        ],
        action_ideas=[
            "Fix chart reset bugs on Android.",
            "Clarify DP and brokerage charges.",
            "Highlight zero fee on mutual funds.",
        ],
        body=body_text,
        word_count=len(body_text.split()),
    )


# ── MCPClient Tests ───────────────────────────────────────────────────────────

class TestMCPClient:
    """Tests for MCPClient initialization, URL handling, headers, and protocol dispatch."""

    def test_resolve_sse_url_variations(self) -> None:
        """Verify URL normalization appends /sse appropriately."""
        assert MCPClient._resolve_sse_url("https://example.com") == "https://example.com/sse"
        assert MCPClient._resolve_sse_url("https://example.com/") == "https://example.com/sse"
        assert MCPClient._resolve_sse_url("https://example.com/sse") == "https://example.com/sse"
        assert MCPClient._resolve_sse_url("https://example.com/mcp") == "https://example.com/mcp"
        assert MCPClient._resolve_sse_url("http://localhost:3000") == "http://localhost:3000/sse"

    def test_headers_without_api_key(self) -> None:
        """Verify headers do not include Authorization if no API key is set."""
        client = MCPClient(server_url="https://example.com", api_key="")
        headers = client._get_headers()
        assert "Authorization" not in headers
        assert "Accept" in headers
        assert headers["User-Agent"] == "GrowwReviewAgent/1.0"

    def test_headers_with_api_key(self) -> None:
        """Verify headers include Bearer Authorization when API key is provided."""
        client = MCPClient(server_url="https://example.com", api_key="secret-key-123")
        headers = client._get_headers()
        assert headers["Authorization"] == "Bearer secret-key-123"

    @patch("src.mcp.client.requests.get")
    def test_check_health_success(self, mock_get: MagicMock) -> None:
        """Verify check_health parses successful JSON response."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"status": "healthy", "name": "google-workspace-mcp", "version": "1.0.0"}
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        client = MCPClient(server_url="https://example.com")
        result = client.check_health_sync()
        assert result["status"] == "healthy"
        assert result["version"] == "1.0.0"
        mock_get.assert_called_once()
        assert mock_get.call_args[0][0] == "https://example.com/health"

    @patch("src.mcp.client.requests.get")
    def test_check_health_failure_raises_connection_error(self, mock_get: MagicMock) -> None:
        """Verify check_health wraps network errors into MCPConnectionError."""
        mock_get.side_effect = Exception("Connection refused")
        client = MCPClient(server_url="https://example.com")

        with pytest.raises(MCPConnectionError) as exc_info:
            client.check_health_sync()
        assert "Health check failed" in str(exc_info.value)

    @pytest.mark.anyio
    @patch("src.mcp.client.ClientSession")
    @patch("src.mcp.client.sse_client")
    async def test_list_tools_async_success(
        self, mock_sse: MagicMock, mock_session_cls: MagicMock
    ) -> None:
        """Verify list_tools retrieves and standardizes tool definitions."""
        mock_sse.return_value.__aenter__.return_value = (MagicMock(), MagicMock())
        mock_session = AsyncMock()
        mock_session_cls.return_value.__aenter__.return_value = mock_session

        mock_tool_1 = Tool(
            name="google_docs_append_text",
            description="Appends text to Doc",
            inputSchema={"type": "object", "properties": {"text": {"type": "string"}}},
        )
        mock_tool_2 = Tool(
            name="gmail_send_email",
            description="Sends email",
            inputSchema={"type": "object", "properties": {"to": {"type": "array"}}},
        )

        mock_resp = MagicMock()
        mock_resp.tools = [mock_tool_1, mock_tool_2]
        mock_session.list_tools.return_value = mock_resp

        client = MCPClient(server_url="https://example.com")
        tools = await client.list_tools()

        assert len(tools) == 2
        assert tools[0]["name"] == "google_docs_append_text"
        assert tools[1]["name"] == "gmail_send_email"
        mock_session.initialize.assert_awaited_once()
        mock_session.list_tools.assert_awaited_once()

    @patch("src.mcp.client.ClientSession")
    @patch("src.mcp.client.sse_client")
    def test_list_tools_sync_wrapper(
        self, mock_sse: MagicMock, mock_session_cls: MagicMock
    ) -> None:
        """Verify synchronous list_tools_sync runs correctly."""
        mock_sse.return_value.__aenter__.return_value = (MagicMock(), MagicMock())
        mock_session = AsyncMock()
        mock_session_cls.return_value.__aenter__.return_value = mock_session

        mock_tool = Tool(
            name="google_docs_append_text",
            description="Appends text",
            inputSchema={},
        )
        mock_resp = MagicMock()
        mock_resp.tools = [mock_tool]
        mock_session.list_tools.return_value = mock_resp

        client = MCPClient(server_url="https://example.com")
        tools = client.list_tools_sync()
        assert len(tools) == 1
        assert tools[0]["name"] == "google_docs_append_text"

    @pytest.mark.anyio
    @patch("src.mcp.client.sse_client")
    async def test_list_tools_connection_error(self, mock_sse: MagicMock) -> None:
        """Verify list_tools maps connection exceptions to MCPConnectionError."""
        mock_sse.side_effect = Exception("SSE Stream failed to open")
        client = MCPClient(server_url="https://example.com")

        with pytest.raises(MCPConnectionError) as exc_info:
            await client.list_tools()
        assert "Failed to connect to MCP server" in str(exc_info.value)

    @pytest.mark.anyio
    @patch("src.mcp.client.ClientSession")
    @patch("src.mcp.client.sse_client")
    async def test_call_tool_async_success(
        self, mock_sse: MagicMock, mock_session_cls: MagicMock
    ) -> None:
        """Verify successful call_tool returns parsed JSON dictionary."""
        mock_sse.return_value.__aenter__.return_value = (MagicMock(), MagicMock())
        mock_session = AsyncMock()
        mock_session_cls.return_value.__aenter__.return_value = mock_session

        payload = {
            "success": True,
            "document_id": "test-doc-id",
            "characters_appended": 250,
            "status": "Text appended to Google Doc",
        }
        mock_result = MagicMock()
        mock_result.is_error = False
        mock_result.content = [TextContent(type="text", text=json.dumps(payload))]
        mock_session.call_tool.return_value = mock_result

        client = MCPClient(server_url="https://example.com")
        result = await client.call_tool("google_docs_append_text", {"document_id": "test-doc-id", "text": "hello"})

        assert result["success"] is True
        assert result["document_id"] == "test-doc-id"
        assert result["characters_appended"] == 250
        mock_session.call_tool.assert_awaited_once_with(
            "google_docs_append_text", {"document_id": "test-doc-id", "text": "hello"}
        )

    @patch("src.mcp.client.ClientSession")
    @patch("src.mcp.client.sse_client")
    def test_call_tool_sync_wrapper(
        self, mock_sse: MagicMock, mock_session_cls: MagicMock
    ) -> None:
        """Verify call_tool_sync runs synchronously."""
        mock_sse.return_value.__aenter__.return_value = (MagicMock(), MagicMock())
        mock_session = AsyncMock()
        mock_session_cls.return_value.__aenter__.return_value = mock_session

        payload = {"success": True, "message_id": "msg-123", "status": "Email sent"}
        mock_result = MagicMock()
        mock_result.is_error = False
        mock_result.content = [TextContent(type="text", text=json.dumps(payload))]
        mock_session.call_tool.return_value = mock_result

        client = MCPClient(server_url="https://example.com")
        result = client.call_tool_sync("gmail_send_email", {"to": ["test@example.com"], "subject": "Test", "body": "Hi"})

        assert result["success"] is True
        assert result["message_id"] == "msg-123"

    @pytest.mark.anyio
    @patch("src.mcp.client.ClientSession")
    @patch("src.mcp.client.sse_client")
    async def test_call_tool_is_error_raises_mcp_tool_call_error(
        self, mock_sse: MagicMock, mock_session_cls: MagicMock
    ) -> None:
        """Verify is_error=True raises MCPToolCallError with code and message."""
        mock_sse.return_value.__aenter__.return_value = (MagicMock(), MagicMock())
        mock_session = AsyncMock()
        mock_session_cls.return_value.__aenter__.return_value = mock_session

        error_payload = {
            "success": False,
            "error_code": "INVALID_ARGUMENT",
            "message": "Invalid document append arguments: document_id: Invalid format",
            "retryable": False,
        }
        mock_result = MagicMock()
        mock_result.is_error = True
        mock_result.content = [TextContent(type="text", text=json.dumps(error_payload))]
        mock_session.call_tool.return_value = mock_result

        client = MCPClient(server_url="https://example.com")
        with pytest.raises(MCPToolCallError) as exc_info:
            await client.call_tool("google_docs_append_text", {"document_id": "invalid"})

        err = exc_info.value
        assert err.error_code == "INVALID_ARGUMENT"
        assert "Invalid document append arguments" in err.message
        assert err.retryable is False

    @pytest.mark.anyio
    @patch("src.mcp.client.ClientSession")
    @patch("src.mcp.client.sse_client")
    async def test_call_tool_confirmation_required_raises_specialized_error(
        self, mock_sse: MagicMock, mock_session_cls: MagicMock
    ) -> None:
        """Verify error_code=CONFIRMATION_REQUIRED raises MCPConfirmationRequiredError."""
        mock_sse.return_value.__aenter__.return_value = (MagicMock(), MagicMock())
        mock_session = AsyncMock()
        mock_session_cls.return_value.__aenter__.return_value = mock_session

        confirm_payload = {
            "success": False,
            "error_code": "CONFIRMATION_REQUIRED",
            "message": "Email confirmation required before sending.",
        }
        mock_result = MagicMock()
        mock_result.is_error = True
        mock_result.content = [TextContent(type="text", text=json.dumps(confirm_payload))]
        mock_session.call_tool.return_value = mock_result

        client = MCPClient(server_url="https://example.com")
        with pytest.raises(MCPConfirmationRequiredError) as exc_info:
            await client.call_tool("gmail_send_email", {"to": ["test@example.com"], "confirm_send": False})

        assert exc_info.value.error_code == "CONFIRMATION_REQUIRED"
        assert "Email confirmation required" in str(exc_info.value)

    @pytest.mark.anyio
    @patch("src.mcp.client.sse_client")
    async def test_call_tool_network_failure_raises_connection_error(
        self, mock_sse: MagicMock
    ) -> None:
        """Verify network or stream failure during tool execution raises MCPConnectionError."""
        mock_sse.side_effect = Exception("Server dropped stream connection")
        client = MCPClient(server_url="https://example.com")

        with pytest.raises(MCPConnectionError) as exc_info:
            await client.call_tool("gmail_send_email", {})
        assert "Connection failure calling tool" in str(exc_info.value)

    def test_run_sync_coroutine_helper(self) -> None:
        """Verify _run_sync executes coroutines correctly."""
        async def dummy():
            return 42

        assert _run_sync(dummy()) == 42


# ── Google Docs Tool Adapter Tests ────────────────────────────────────────────

class TestDocsTools:
    """Tests for format_pulse_for_docs and append_pulse_to_doc."""

    def test_format_pulse_for_docs_structure(self, sample_pulse: Pulse) -> None:
        """Verify formatted entry contains date header, separators, and pulse body."""
        formatted = format_pulse_for_docs(sample_pulse)
        assert "GROWW WEEKLY REVIEW PULSE — WEEK OF 2026-10-14" in formatted
        assert "=" * 80 in formatted
        assert "## Top Themes" in formatted
        assert "## What Users Are Saying" in formatted
        assert "## Recommended Actions" in formatted

    def test_append_pulse_to_doc_missing_doc_id_raises_value_error(self, sample_pulse: Pulse) -> None:
        """Verify missing doc_id (and empty settings.pulse_doc_id) raises ValueError."""
        with patch("src.mcp.docs_tools.settings.pulse_doc_id", ""):
            with pytest.raises(ValueError) as exc_info:
                append_pulse_to_doc(doc_id="", pulse=sample_pulse)
            assert "Google Doc ID or URL is required" in str(exc_info.value)

    def test_append_pulse_to_doc_missing_pulse_raises_value_error(self) -> None:
        """Verify missing pulse raises ValueError."""
        with pytest.raises(ValueError) as exc_info:
            append_pulse_to_doc(doc_id="doc-123", pulse=None)
        assert "Pulse object is required" in str(exc_info.value)

    def test_append_pulse_to_doc_success_with_doc_id(self, sample_pulse: Pulse) -> None:
        """Verify successful doc append with raw doc ID constructs URL correctly."""
        mock_client = MagicMock()
        mock_client.call_tool_sync.return_value = {
            "success": True,
            "document_id": "1abcXYZ789",
            "characters_appended": 450,
            "status": "Text appended to Google Doc",
        }

        result = append_pulse_to_doc(doc_id="1abcXYZ789", pulse=sample_pulse, client=mock_client)

        assert result["success"] is True
        assert result["document_id"] == "1abcXYZ789"
        assert result["characters_appended"] == 450
        assert result["doc_url"] == "https://docs.google.com/document/d/1abcXYZ789/edit"
        assert "request_id" in result

        # Verify arguments passed to tool
        mock_client.call_tool_sync.assert_called_once()
        tool_name, args = mock_client.call_tool_sync.call_args[0]
        assert tool_name == "google_docs_append_text"
        assert args["document_id"] == "1abcXYZ789"
        assert args["prepend_newline"] is True
        assert "GROWW WEEKLY REVIEW PULSE" in args["text"]

    def test_append_pulse_to_doc_success_with_full_url(self, sample_pulse: Pulse) -> None:
        """Verify passing full Google Doc URL preserves it as doc_url."""
        mock_client = MagicMock()
        full_url = "https://docs.google.com/document/d/1abcXYZ789/edit?usp=sharing"
        mock_client.call_tool_sync.return_value = {
            "success": True,
            "document_id": full_url,
            "characters_appended": 450,
        }

        result = append_pulse_to_doc(doc_id=full_url, pulse=sample_pulse, client=mock_client)
        assert result["doc_url"] == full_url

    def test_append_pulse_to_doc_propagates_mcp_error(self, sample_pulse: Pulse) -> None:
        """Verify MCP errors from tool call are propagated to caller."""
        mock_client = MagicMock()
        mock_client.call_tool_sync.side_effect = MCPToolCallError(
            tool_name="google_docs_append_text",
            error_code="PERMISSION_DENIED",
            message="No edit access to doc",
        )

        with pytest.raises(MCPToolCallError) as exc_info:
            append_pulse_to_doc(doc_id="doc-no-access", pulse=sample_pulse, client=mock_client)
        assert exc_info.value.error_code == "PERMISSION_DENIED"


# ── Gmail Tool Adapter Tests ──────────────────────────────────────────────────

class TestGmailTools:
    """Tests for format_pulse_email_body and send_pulse_email."""

    def test_format_pulse_email_body_with_doc_url(self, sample_pulse: Pulse) -> None:
        """Verify email body includes greeting, pulse body, and doc URL."""
        doc_url = "https://docs.google.com/document/d/123/edit"
        body = format_pulse_email_body(sample_pulse, doc_url=doc_url)

        assert "Hi Team," in body
        assert "Week of 2026-10-14" in body
        assert "## Top Themes" in body
        assert doc_url in body
        assert "Generated automatically by the Groww Review Intelligence Agent" in body

    def test_format_pulse_email_body_without_doc_url(self, sample_pulse: Pulse) -> None:
        """Verify email body renders cleanly when no doc_url is provided."""
        body = format_pulse_email_body(sample_pulse, doc_url="")
        assert "Hi Team," in body
        assert "View Full Historical Document Journal" not in body

    def test_send_pulse_email_missing_recipient_raises_value_error(self, sample_pulse: Pulse) -> None:
        """Verify missing recipient (and empty settings.pulse_email_recipient) raises ValueError."""
        with patch("src.mcp.gmail_tools.settings.pulse_email_recipient", ""):
            with pytest.raises(ValueError) as exc_info:
                send_pulse_email(recipient="", pulse=sample_pulse)
            assert "Recipient email address is required" in str(exc_info.value)

    def test_send_pulse_email_missing_pulse_raises_value_error(self) -> None:
        """Verify missing pulse raises ValueError."""
        with pytest.raises(ValueError) as exc_info:
            send_pulse_email(recipient="test@example.com", pulse=None)
        assert "Pulse object is required" in str(exc_info.value)

    def test_send_pulse_email_normalizes_comma_separated_recipients(self, sample_pulse: Pulse) -> None:
        """Verify string with multiple comma/semicolon recipients is split into list."""
        mock_client = MagicMock()
        mock_client.call_tool_sync.return_value = {
            "success": True,
            "message_id": "msg-999",
            "status": "Email sent",
        }

        recipient_str = " lead@groww.in,  growth@groww.in; pm@groww.in "
        result = send_pulse_email(recipient=recipient_str, pulse=sample_pulse, client=mock_client)

        assert result["success"] is True
        assert result["to"] == ["lead@groww.in", "growth@groww.in", "pm@groww.in"]

        tool_name, args = mock_client.call_tool_sync.call_args[0]
        assert args["to"] == ["lead@groww.in", "growth@groww.in", "pm@groww.in"]
        assert args["confirm_send"] is True

    def test_send_pulse_email_list_recipients(self, sample_pulse: Pulse) -> None:
        """Verify passing list of recipients works as expected."""
        mock_client = MagicMock()
        mock_client.call_tool_sync.return_value = {
            "success": True,
            "message_id": "msg-888",
            "thread_id": "thread-111",
            "status": "Email sent",
        }

        recipients = ["pm@groww.in", "analyst@groww.in"]
        result = send_pulse_email(
            recipient=recipients,
            pulse=sample_pulse,
            doc_url="https://docs.google.com/document/d/123/edit",
            client=mock_client,
        )

        assert result["success"] is True
        assert result["message_id"] == "msg-888"
        assert result["thread_id"] == "thread-111"
        assert result["subject"] == "Groww Weekly Review Pulse — Week of 2026-10-14"

    def test_send_pulse_email_explicit_confirm_send_false(self, sample_pulse: Pulse) -> None:
        """Verify confirm_send can be passed as False if caller wishes to test confirmation."""
        mock_client = MagicMock()
        mock_client.call_tool_sync.return_value = {"success": True, "message_id": "msg-000"}

        send_pulse_email(
            recipient="test@example.com",
            pulse=sample_pulse,
            confirm_send=False,
            client=mock_client,
        )

        _, args = mock_client.call_tool_sync.call_args[0]
        assert args["confirm_send"] is False

    def test_send_pulse_email_confirmation_required_propagates(self, sample_pulse: Pulse) -> None:
        """Verify MCPConfirmationRequiredError is raised and propagated when confirmation is needed."""
        mock_client = MagicMock()
        mock_client.call_tool_sync.side_effect = MCPConfirmationRequiredError(
            tool_name="gmail_send_email",
            message="User confirmation required before dispatching email.",
        )

        with pytest.raises(MCPConfirmationRequiredError) as exc_info:
            send_pulse_email(
                recipient="test@example.com",
                pulse=sample_pulse,
                confirm_send=False,
                client=mock_client,
            )
        assert exc_info.value.error_code == "CONFIRMATION_REQUIRED"

    def test_send_pulse_email_tool_error_propagates(self, sample_pulse: Pulse) -> None:
        """Verify generic tool errors propagate as MCPToolCallError."""
        mock_client = MagicMock()
        mock_client.call_tool_sync.side_effect = MCPToolCallError(
            tool_name="gmail_send_email",
            error_code="RATE_LIMIT_EXCEEDED",
            message="Gmail daily quota exceeded",
            retryable=True,
        )

        with pytest.raises(MCPToolCallError) as exc_info:
            send_pulse_email(
                recipient="test@example.com",
                pulse=sample_pulse,
                client=mock_client,
            )
        assert exc_info.value.error_code == "RATE_LIMIT_EXCEEDED"
        assert exc_info.value.retryable is True
