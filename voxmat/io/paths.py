"""Filesystem locations for Voxmat projects and bundled sample scenes.

`samples_dir()` is the folder of bundled `.mmvox` examples (e.g. the default
Cornell box). `default_project_dir()` is where the open/export dialogs start: the
samples folder when run from source, or a writable ``Voxmat`` folder under the
user's home in a frozen build (the bundled samples are read-only there, so they're
copied in on first use). After the user opens/saves somewhere, the UI remembers
that location instead.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from .._resources import is_frozen, resource_root


def samples_dir() -> Path:
    """Directory holding the bundled sample projects (read-only when frozen)."""
    return resource_root() / "samples"


def default_project_dir() -> Path:
    """Initial directory for the .mmvox open/export dialogs."""
    if not is_frozen():
        return samples_dir()
    target = Path.home() / "Voxmat"
    target.mkdir(parents=True, exist_ok=True)
    bundled = samples_dir()
    if bundled.is_dir():                       # seed the samples once
        for sample in bundled.glob("*.mmvox"):
            dest = target / sample.name
            if not dest.exists():
                try:
                    shutil.copy2(sample, dest)
                except OSError:
                    pass
    return target
