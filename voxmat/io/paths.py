"""Filesystem locations for Voxmat projects and bundled sample scenes.

`samples_dir()` is the folder of bundled `.mmvox` examples (e.g. the default
Cornell box) and the initial directory the open/export dialogs point at. After the
user opens or saves somewhere, the UI remembers that location instead.
"""

from __future__ import annotations

from pathlib import Path

# voxmat/io/paths.py -> repo/app root (contains voxmat/, samples/, assets/).
_ROOT = Path(__file__).resolve().parents[2]


def samples_dir() -> Path:
    """Directory holding the bundled sample projects."""
    return _ROOT / "samples"
