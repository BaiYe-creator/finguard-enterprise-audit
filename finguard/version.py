"""FinGuard release metadata."""

from __future__ import annotations

__version__ = "0.8.0"
RELEASE_NAME = "Data Governance"
RELEASE_CHANNEL = "internal"


def version_label() -> str:
    return f"FinGuard V{__version__} · {RELEASE_NAME} · {RELEASE_CHANNEL}"
