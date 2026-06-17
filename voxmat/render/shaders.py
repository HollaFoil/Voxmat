"""Loader for the path tracer's GLSL source files.

Shaders live as plain ``.glsl`` / ``.vert`` / ``.frag`` files under ``shaders/``
so they can be edited with proper syntax highlighting and shared between passes.
A minimal ``#include "name"`` directive is resolved at load time (include-once),
which lets every fragment pass pull in the shared :file:`common.glsl` without the
fragile text-substitution this module replaced.
"""

from __future__ import annotations

import re
from functools import lru_cache

from .._resources import resource_root

_SHADER_DIR = resource_root() / "voxmat" / "render" / "shaders"
_INCLUDE_RE = re.compile(r'^\s*#include\s+"([^"]+)"\s*$', re.MULTILINE)


def _read(name: str) -> str:
    path = _SHADER_DIR / name
    return path.read_text(encoding="utf-8")


def _resolve(name: str, seen: set[str]) -> str:
    """Return the source of ``name`` with any ``#include`` lines spliced in.

    ``seen`` guards against including the same file (or a cycle) twice.
    """
    if name in seen:
        return ""
    seen.add(name)

    def _splice(match: re.Match) -> str:
        return _resolve(match.group(1), seen)

    return _INCLUDE_RE.sub(_splice, _read(name))


@lru_cache(maxsize=None)
def load_shader(name: str) -> str:
    """Load shader ``name`` from the shaders directory, resolving ``#include``s."""
    return _resolve(name, set())
