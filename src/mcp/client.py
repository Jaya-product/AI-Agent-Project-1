"""
src/mcp/client.py
─────────────────
Phase 6A — Model Context Protocol (MCP) Client Transport Layer.

Connects to the remote Google Workspace MCP Server (deployed on Railway)
via Streamable HTTP (Server-Sent Events transport).

Key Features:
- SSE stream establishment on ``/sse`` (or ``/mcp``) with JSON-RPC 2.0 message dispatch.
- Bearer API Key authentication support (via headers or query param).
- Health check probe on ``/health``.
- Protocol error handling (maps server MCPToolErrors such as INVALID_ARGUMENT,
  CONFIRMATION_REQUIRED, REQUEST_TOO_LARGE).
- Both asynchronous (``call_tool``, ``list_tools``) and synchronous
  (``call_tool_sync``, ``list_tools_sync``) APIs for seamless orchestration.

Usage:
    from src.mcp.client import MCPClient

    client = MCPClient()
    # Check health
    health = client.check_health_sync()

    # Call a tool synchronously
    result = client.call_tool_sync("google_docs_append_text", {
        "document_id": "1abc...",
        "text": "Weekly Pulse Note...",
    })
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import urllib.parse
from typing import Any

import certifi
import requests
from mcp.client.session import ClientSession
from mcp.client.sse import sse_client
from mcp.types import TextContent

from src.utils.config import settings
from src.utils.logger import get_logger

logger = get_logger(__name__)


# ── Exception Hierarchy ───────────────────────────────────────────────────────

class MCPClientError(Exception):
    """Base exception for all MCP client failures."""


class MCPConnectionError(MCPClientError):
    """Raised when connecting to the remote MCP server fails or times out."""


class MCPToolCallError(MCPClientError):
    """Raised when the MCP server returns an error during tool execution."""

    def __init__(
        self,
        tool_name: str,
        error_code: str = "TOOL_ERROR",
        message: str = "",
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(f"Tool '{tool_name}' failed [{error_code}]: {message}")
        self.tool_name = tool_name
        self.error_code = error_code
        self.message = message
        self.retryable = retryable
        self.details = details or {}


class MCPConfirmationRequiredError(MCPToolCallError):
    """
    Raised when the tool requires explicit two-phase confirmation
    (e.g., outbound email send when REQUIRE_EMAIL_CONFIRMATION is active).
    """

    def __init__(
        self,
        tool_name: str,
        message: str,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(
            tool_name=tool_name,
            error_code="CONFIRMATION_REQUIRED",
            message=message,
            retryable=False,
            details=details,
        )


# ── Helper for Sync / Async Bridge ────────────────────────────────────────────

def _run_sync(coro: Any) -> Any:
    """
    Run an async coroutine synchronously, safely handling cases where
    an event loop is already running in the current thread.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result()
    else:
        return asyncio.run(coro)


# ── MCP Client Implementation ─────────────────────────────────────────────────

class MCPClient:
    """
    High-level client for interacting with the Google Workspace MCP Server.

    Supports connecting over SSE, querying tool schemas, executing tools,
    and structured error parsing.
    """

    def __init__(
        self,
        server_url: str | None = None,
        api_key: str | None = None,
        timeout: float = 20.0,
        sse_read_timeout: float = 300.0,
    ):
        raw_url = server_url or settings.mcp_server_url
        self.base_url = raw_url.rstrip("/")
        self.api_key = api_key if api_key is not None else settings.mcp_api_key
        self.timeout = timeout
        self.sse_read_timeout = sse_read_timeout
        self.sse_url = self._resolve_sse_url(self.base_url)

    @staticmethod
    def _resolve_sse_url(url: str) -> str:
        """Resolve base server URL to its SSE endpoint."""
        parsed = urllib.parse.urlparse(url)
        path = parsed.path.rstrip("/")
        if path.endswith("/sse") or path.endswith("/mcp"):
            return url
        new_path = f"{path}/sse" if path else "/sse"
        return urllib.parse.urlunparse(
            (parsed.scheme, parsed.netloc, new_path, parsed.params, parsed.query, parsed.fragment)
        )

    def _get_headers(self) -> dict[str, str]:
        """Construct HTTP headers including Bearer token if configured."""
        headers = {
            "Accept": "text/event-stream, application/json",
            "User-Agent": "GrowwReviewAgent/1.0",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    # ── Health Check ──────────────────────────────────────────────────────────

    def check_health(self) -> dict[str, Any]:
        """
        Probe the server's /health endpoint to verify status without
        establishing an SSE session. Synchronous HTTP probe.
        """
        health_url = f"{self.base_url}/health"
        try:
            resp = requests.get(
                health_url,
                headers=self._get_headers(),
                timeout=self.timeout,
                verify=certifi.where(),
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            logger.error("MCP health check failed", extra={"server_url": health_url, "error": str(exc)})
            raise MCPConnectionError(f"Health check failed for {health_url}: {exc}") from exc

    def check_health_sync(self) -> dict[str, Any]:
        """Convenience alias for check_health."""
        return self.check_health()

    # ── Asynchronous Operations ───────────────────────────────────────────────

    async def list_tools(self) -> list[dict[str, Any]]:
        """
        Connect to MCP server, initialize protocol session, and retrieve
        all registered tool definitions and schemas.
        """
        headers = self._get_headers()
        try:
            async with sse_client(
                self.sse_url,
                headers=headers,
                timeout=self.timeout,
                sse_read_timeout=self.sse_read_timeout,
            ) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    response = await session.list_tools()
                    tools = [
                        {
                            "name": t.name,
                            "description": t.description,
                            "inputSchema": t.inputSchema if hasattr(t, "inputSchema") else getattr(t, "input_schema", {}),
                        }
                        for t in response.tools
                    ]
                    logger.debug("Retrieved tools from MCP server", extra={"count": len(tools)})
                    return tools
        except MCPClientError:
            raise
        except Exception as exc:
            logger.error("Failed to list tools from MCP server", extra={"sse_url": self.sse_url, "error": str(exc)})
            raise MCPConnectionError(f"Failed to connect to MCP server at {self.sse_url}: {exc}") from exc

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """
        Execute an MCP tool by name with arguments over an SSE session.
        Parses response content and handles structured error codes.
        """
        headers = self._get_headers()
        logger.info("Calling MCP tool", extra={"tool": name, "sse_url": self.sse_url})

        try:
            async with sse_client(
                self.sse_url,
                headers=headers,
                timeout=self.timeout,
                sse_read_timeout=self.sse_read_timeout,
            ) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    result = await session.call_tool(name, arguments)
            return self._parse_tool_result(name, result)
        except MCPClientError:
            raise
        except BaseException as exc:
            # Check if this is an ExceptionGroup wrapping an MCPClientError
            if hasattr(exc, "exceptions"):
                for sub in getattr(exc, "exceptions", []):
                    if isinstance(sub, MCPClientError):
                        raise sub
                    if hasattr(sub, "exceptions"):
                        for inner in getattr(sub, "exceptions", []):
                            if isinstance(inner, MCPClientError):
                                raise inner
            logger.error("MCP tool execution network failure", extra={"tool": name, "error": str(exc)})
            raise MCPConnectionError(f"Connection failure calling tool '{name}' on {self.sse_url}: {exc}") from exc

    # ── Synchronous Convenience Wrappers ──────────────────────────────────────

    def list_tools_sync(self) -> list[dict[str, Any]]:
        """Synchronously list all tools available on the MCP server."""
        return _run_sync(self.list_tools())

    def call_tool_sync(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Synchronously execute a tool on the MCP server."""
        return _run_sync(self.call_tool(name, arguments))

    # ── Result Parsing & Error Translation ────────────────────────────────────

    def _parse_tool_result(self, name: str, result: Any) -> dict[str, Any]:
        """
        Extract and parse structured JSON from CallToolResult.
        Translates error responses into appropriate MCPToolCallError subclasses.
        """
        is_error = getattr(result, "is_error", False)
        content_items = getattr(result, "content", [])

        raw_text = ""
        for item in content_items:
            if isinstance(item, TextContent) or hasattr(item, "text"):
                raw_text += getattr(item, "text", "")

        parsed_data: dict[str, Any] = {}
        if raw_text.strip():
            try:
                parsed_data = json.loads(raw_text)
            except json.JSONDecodeError:
                parsed_data = {"raw_text": raw_text}

        if is_error or parsed_data.get("success") is False:
            error_code = parsed_data.get("error_code", "TOOL_ERROR")
            message = parsed_data.get("message", raw_text or "Tool call failed")
            retryable = bool(parsed_data.get("retryable", False))

            if error_code == "CONFIRMATION_REQUIRED":
                raise MCPConfirmationRequiredError(
                    tool_name=name,
                    message=message,
                    details=parsed_data,
                )

            raise MCPToolCallError(
                tool_name=name,
                error_code=error_code,
                message=message,
                retryable=retryable,
                details=parsed_data,
            )

        logger.info("MCP tool executed successfully", extra={"tool": name})
        return parsed_data if parsed_data else {"success": True, "raw_text": raw_text}
