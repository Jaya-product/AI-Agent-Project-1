"""
scripts/run_mcp_server.py
─────────────────────────
Launches the Google Workspace MCP Server locally using the authorized
credentials stored at C:/Users/jaya.manocha/Documents/myProjects/MCP/.credentials/tokens.json.
Runs on http://127.0.0.1:8080 over Streamable HTTP (SSE).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_MCP_DIR = _ROOT.parent / "MCP"
_TOKENS_PATH = _MCP_DIR / ".credentials" / "tokens.json"
_MCP_ENV = _MCP_DIR / ".env"
_MCP_DIST = _MCP_DIR / "dist" / "src" / "index.js"


def main():
    if not _TOKENS_PATH.exists():
        print(f"Error: Credentials not found at {_TOKENS_PATH}")
        sys.exit(1)

    if not _MCP_DIST.exists():
        print(f"Error: MCP dist not found at {_MCP_DIST}. Run 'npm run build' in {_MCP_DIR}")
        sys.exit(1)

    env = dict(os.environ)
    # Read MCP .env
    if _MCP_ENV.exists():
        with open(_MCP_ENV, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    if k not in env:
                        env[k] = v

    env["MCP_TRANSPORT"] = "streamable-http"
    env["MCP_PORT"] = "8080"
    env["MCP_HOST"] = "127.0.0.1"
    env["TOKEN_STORAGE_PATH"] = str(_TOKENS_PATH)

    print("Starting local Google Workspace MCP Server on http://127.0.0.1:8080...")
    sys.stdout.flush()

    cmd = ["node", str(_MCP_DIST)]
    try:
        proc = subprocess.run(cmd, env=env, cwd=str(_MCP_DIR))
        sys.exit(proc.returncode)
    except KeyboardInterrupt:
        print("\nStopping local MCP server.")


if __name__ == "__main__":
    main()
