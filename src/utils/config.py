"""
src/utils/config.py
───────────────────
Loads config/settings.yaml and exposes a typed, singleton Settings object.

Environment variables take priority over settings.yaml values, allowing
per-run overrides without editing the config file (see .env.example).

Usage:
    from src.utils.config import settings
    print(settings.review_window_weeks)
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Load .env into the process environment before pydantic reads env vars
load_dotenv()

# ── Resolve paths ─────────────────────────────────────────────────────────────
_PROJECT_ROOT = Path(__file__).resolve().parents[2]   # AI Agent/
_SETTINGS_FILE = _PROJECT_ROOT / "config" / "settings.yaml"


def _load_yaml_defaults() -> dict:
    """Read settings.yaml and return its contents as a flat dict."""
    if not _SETTINGS_FILE.exists():
        return {}
    with _SETTINGS_FILE.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


_yaml_defaults = _load_yaml_defaults()


class Settings(BaseSettings):
    """
    Typed configuration for the Groww Review Intelligence Agent.

    Resolution order (highest → lowest priority):
        1. Environment variables (including values from .env)
        2. config/settings.yaml
        3. Field defaults defined here
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Review Ingestion ──────────────────────────────────────────────────────
    app_id: str = Field(
        default=_yaml_defaults.get("app_id", "com.nextbillion.groww"),
        description="Google Play Store app ID to fetch reviews for.",
    )
    review_window_weeks: int = Field(
        default=_yaml_defaults.get("review_window_weeks", 10),
        ge=1,
        description="Number of weeks of reviews to ingest.",
    )
    max_reviews: int = Field(
        default=_yaml_defaults.get("max_reviews", 500),
        ge=1,
        description="Maximum number of reviews to fetch per pipeline run.",
    )
    min_review_length: int = Field(
        default=_yaml_defaults.get("min_review_length", 20),
        ge=0,
        description="Reviews shorter than this (chars) are dropped by the scrubber.",
    )
    lang: str = Field(
        default=_yaml_defaults.get("lang", "en"),
        description="Language code for Play Store reviews.",
    )
    country: str = Field(
        default=_yaml_defaults.get("country", "in"),
        description="Country code for Play Store reviews.",
    )
    non_english_policy: str = Field(
        default=_yaml_defaults.get("non_english_policy", "keep"),
        description=(
            "How to handle non-ASCII reviews (Hindi, Tamil, etc.). "
            '"keep" passes them to the LLM; "drop" discards them. '
            "Real data: ~19% of Groww reviews are non-ASCII."
        ),
    )

    # ── Theme Clustering ──────────────────────────────────────────────────────
    max_themes: int = Field(
        default=_yaml_defaults.get("max_themes", 5),
        ge=1,
        le=10,
        description="Hard cap on the number of themes the LLM may return.",
    )
    llm_batch_size: int = Field(
        default=_yaml_defaults.get("llm_batch_size", 50),
        ge=1,
        description="Number of reviews per LLM clustering batch.",
    )
    llm_max_retries: int = Field(
        default=_yaml_defaults.get("llm_max_retries", 3),
        ge=1,
        description="Max retries if the LLM returns malformed or non-compliant output.",
    )

    # ── Pulse Generation ──────────────────────────────────────────────────────
    pulse_themes: int = Field(
        default=_yaml_defaults.get("pulse_themes", 3),
        ge=1,
        description="Number of top themes to feature in the weekly pulse.",
    )
    pulse_max_words: int = Field(
        default=_yaml_defaults.get("pulse_max_words", 250),
        ge=50,
        description="Hard word cap for the generated pulse body.",
    )
    quotes_per_pulse: int = Field(
        default=_yaml_defaults.get("quotes_per_pulse", 3),
        ge=1,
        description="Number of verbatim user quotes to include in the pulse.",
    )
    action_ideas_count: int = Field(
        default=_yaml_defaults.get("action_ideas_count", 3),
        ge=1,
        description="Number of action ideas the LLM should generate.",
    )

    # ── LLM Provider ──────────────────────────────────────────────────────────
    llm_provider: str = Field(
        default=os.getenv("LLM_PROVIDER", "gemini"),
        description='LLM backend to use. One of: "gemini" | "openai".',
    )
    gemini_api_key: str = Field(
        default=os.getenv("GEMINI_API_KEY", ""),
        description="Gemini API key (required when llm_provider=gemini).",
    )
    openai_api_key: str = Field(
        default=os.getenv("OPENAI_API_KEY", ""),
        description="OpenAI API key (required when llm_provider=openai).",
    )

    # ── MCP Server (Google Workspace MCP) ─────────────────────────────────────
    mcp_server_url: str = Field(
        default=os.getenv(
            "MCP_SERVER_URL",
            _yaml_defaults.get("mcp_server_url", "https://mcp-server-production-3be3.up.railway.app"),
        ),
        description="Base URL for the Google Workspace MCP server (SSE endpoint).",
    )
    mcp_api_key: str = Field(
        default=os.getenv("MCP_API_KEY", _yaml_defaults.get("mcp_api_key", "")),
        description="Optional Bearer API key for the MCP server.",
    )
    pulse_doc_id: str = Field(
        default=os.getenv("PULSE_DOC_ID", _yaml_defaults.get("pulse_doc_id", "")),
        description="Google Doc ID or URL where weekly pulses are appended.",
    )
    pulse_email_recipient: str = Field(
        default=os.getenv("PULSE_EMAIL_RECIPIENT", _yaml_defaults.get("pulse_email_recipient", "")),
        description="Email address to receive weekly pulse notes via MCP.",
    )

    # Legacy aliases (backward compatibility)
    google_docs_mcp_server_url: str = Field(
        default=os.getenv("GOOGLE_DOCS_MCP_SERVER_URL", "https://mcp-server-production-3be3.up.railway.app"),
        description="Legacy alias for Docs MCP server URL.",
    )
    gmail_mcp_server_url: str = Field(
        default=os.getenv("GMAIL_MCP_SERVER_URL", "https://mcp-server-production-3be3.up.railway.app"),
        description="Legacy alias for Gmail MCP server URL.",
    )

    # ── Logging ───────────────────────────────────────────────────────────────
    log_level: str = Field(
        default=_yaml_defaults.get("log_level", "INFO"),
        description="Logging verbosity: DEBUG | INFO | WARNING | ERROR.",
    )


# Singleton — import this everywhere instead of constructing Settings() directly
settings = Settings()
