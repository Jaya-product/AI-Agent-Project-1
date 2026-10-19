"""
src/intelligence
────────────────
Intelligence layer for Groww Review Intelligence Agent.

Modules:
    clusterer: Phase 4 — Semantic clustering of reviews into themes.
    pulse_generator: Phase 5 — One-page weekly pulse note generation.
"""

from src.intelligence.clusterer import (
    Theme,
    cluster,
)
from src.intelligence.pulse_generator import (
    Pulse,
    generate_pulse,
)

__all__ = [
    "Theme",
    "cluster",
    "Pulse",
    "generate_pulse",
]
