"""Voxmat — a lightweight, per-voxel material editor for sliced voxel images.

Layering:
    core/  pure data model (numpy only, no Qt/GL)
    io/    image import + binary export (depends on core)
    ui/    PySide6 + ModernGL front end (depends on io + core)
"""

__version__ = "0.1.0"
