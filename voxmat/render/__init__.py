"""Rendering backend: the progressive voxel path tracer and environment maps.

This package is the pure GL renderer (ModernGL + GLSL in :mod:`.shaders`); it has
no Qt/UI dependency. The Qt integration lives in :mod:`voxmat.ui.viewport`.
"""

from .environment import (available_environments, default_path,
                          load_environment)
from .pathtracer import PathTracer

__all__ = ["available_environments", "default_path", "load_environment",
           "PathTracer"]
