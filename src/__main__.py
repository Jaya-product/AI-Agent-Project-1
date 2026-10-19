"""
src/__main__.py
───────────────
Module entrypoint allowing:
  python -m src
  python -m src --dry-run
"""

from src.agent.core import main

if __name__ == "__main__":
    main()
