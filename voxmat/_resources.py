"""Locate bundled resources whether running from source or a PyInstaller build.

In a frozen build PyInstaller unpacks data files under ``sys._MEIPASS``; from
source they sit in the repository root. Everything that loads a shipped file
(shaders, environment maps, the sample scene) resolves it through here.
"""

from __future__ import annotations

import sys
from pathlib import Path


def is_frozen() -> bool:
    return getattr(sys, "frozen", False)


def resource_root() -> Path:
    """Directory containing ``voxmat/``, ``assets/`` and ``samples/`` — the repo
    root when run from source, or the PyInstaller extraction dir when frozen."""
    if is_frozen():
        return Path(sys._MEIPASS)            # type: ignore[attr-defined]
    return Path(__file__).resolve().parents[1]
