"""Import sliced voxel images into :class:`~voxmat.core.frame.Frame` objects.

A *frame image* is a strip/grid of square (or rectangular) slices; each slice is
one XY layer of the model and the slices step through Z. Empty voxels are pixels
with ``alpha <= alpha_threshold``.

``ImportConfig`` exposes every "how to read this file" knob so the same loader
serves the observed MagicaVoxel exports and future variants:

* slice size (auto-derived as square when left at 0),
* slice layout (vertical / horizontal strip, or a grid),
* frame source (one tall multi-frame image, or many single-frame files),
* axis flips / xy-swap to reconcile MagicaVoxel and Unity orientations.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

import numpy as np
from PIL import Image

from ..core.frame import Frame
from ..core.grid import VoxelGrid


class Layout(Enum):
    VERTICAL_STRIP = "vertical"      # slices stacked top->bottom (observed format)
    HORIZONTAL_STRIP = "horizontal"  # slices stacked left->right
    GRID = "grid"                    # slices in a cols x rows grid, row-major


class FrameSource(Enum):
    SINGLE_IMAGE = "single"          # one image holds all frames back-to-back
    MULTI_FILE = "multi"             # each file is one frame


def natural_key(path: Path):
    """belt1 < belt2 < belt10 — mirrors the user's stack_frames script."""
    return [int(t) if t.isdigit() else t.lower()
            for t in re.split(r"(\d+)", path.stem)]


@dataclass
class ImportConfig:
    slice_w: int = 0          # 0 => auto (square slices from image width)
    slice_h: int = 0          # 0 => auto (== slice_w)
    slice_count: int = 0      # Z depth; 0 => auto from available slices
    layout: Layout = Layout.VERTICAL_STRIP
    grid_cols: int = 0        # for Layout.GRID; 0 => auto
    frame_source: FrameSource = FrameSource.SINGLE_IMAGE
    frame_count: int = 1      # for SINGLE_IMAGE
    flip_x: bool = False
    flip_y: bool = False
    # Default True: MagicaVoxel slice strips stack top->bottom, so flipping Z puts
    # the model the right way up (e.g. grass on top) in this Z-up viewer.
    flip_z: bool = True
    swap_xy: bool = False     # transpose within each slice
    alpha_threshold: int = 0

    # convenience: shared frame name prefix
    name_prefix: str = "frame"

    def resolved_slice_size(self, img_w: int, img_h: int) -> tuple[int, int]:
        sw = self.slice_w or img_w
        sh = self.slice_h or sw
        return sw, sh


def probe_image(path: str | Path) -> dict:
    """Return basic info (size, mode, square-slice guess) for UI defaults."""
    with Image.open(path) as im:
        w, h = im.size
        mode = im.mode
    guess_count = h // w if w and h % w == 0 else 0
    return {"width": w, "height": h, "mode": mode,
            "square_slice_count": guess_count}


def _slice_origins(img_w: int, img_h: int, sw: int, sh: int,
                   layout: Layout, grid_cols: int) -> list[tuple[int, int]]:
    """Top-left (x0, y0) of each slice region, in reading order."""
    origins: list[tuple[int, int]] = []
    if layout is Layout.VERTICAL_STRIP:
        for y0 in range(0, img_h - sh + 1, sh):
            origins.append((0, y0))
    elif layout is Layout.HORIZONTAL_STRIP:
        for x0 in range(0, img_w - sw + 1, sw):
            origins.append((x0, 0))
    elif layout is Layout.GRID:
        cols = grid_cols or max(1, img_w // sw)
        rows = img_h // sh
        for r in range(rows):
            for c in range(cols):
                origins.append((c * sw, r * sh))
    return origins


def _grid_from_slices(slices: list[np.ndarray], cfg: ImportConfig,
                      sw: int, sh: int) -> VoxelGrid:
    """Assemble a list of (sh, sw, 4) slice arrays into a VoxelGrid (X,Y,Z,4)."""
    count = len(slices)
    # Build (X, Y, Z, 4): x<-col, y<-row, z<-slice index.
    grid = np.zeros((sw, sh, count, 4), dtype=np.uint8)
    for z, sl in enumerate(slices):
        # sl is (row, col, 4) == (y, x, 4); move to (x, y, 4)
        grid[:, :, z, :] = np.transpose(sl, (1, 0, 2))

    # empties: clear alpha at/below threshold so filled_mask is exact
    if cfg.alpha_threshold > 0:
        empty = grid[..., 3] <= cfg.alpha_threshold
        grid[empty] = 0

    if cfg.swap_xy:
        grid = np.transpose(grid, (1, 0, 2, 3))
    if cfg.flip_x:
        grid = grid[::-1, :, :, :]
    if cfg.flip_y:
        grid = grid[:, ::-1, :, :]
    if cfg.flip_z:
        grid = grid[:, :, ::-1, :]
    return VoxelGrid(np.ascontiguousarray(grid))


def _load_rgba(path: str | Path) -> np.ndarray:
    with Image.open(path) as im:
        arr = np.asarray(im.convert("RGBA"), dtype=np.uint8)  # (H, W, 4)
    return arr


def _frames_from_array(arr: np.ndarray, cfg: ImportConfig,
                       base_name: str) -> list[Frame]:
    """Split one image array into one or more frames per the config."""
    img_h, img_w = arr.shape[0], arr.shape[1]
    sw, sh = cfg.resolved_slice_size(img_w, img_h)
    origins = _slice_origins(img_w, img_h, sw, sh, cfg.layout, cfg.grid_cols)
    if not origins:
        raise ValueError("No slices found — check slice size / layout.")

    frame_count = cfg.frame_count if cfg.frame_source is FrameSource.SINGLE_IMAGE else 1
    frame_count = max(1, frame_count)
    if len(origins) % frame_count != 0:
        raise ValueError(
            f"{len(origins)} slices not divisible into {frame_count} frames.")
    per_frame = len(origins) // frame_count
    slice_count = cfg.slice_count or per_frame
    if slice_count > per_frame:
        raise ValueError(
            f"slice_count={slice_count} exceeds available {per_frame} per frame.")

    frames: list[Frame] = []
    for f in range(frame_count):
        slices = []
        for s in range(slice_count):
            x0, y0 = origins[f * per_frame + s]
            slices.append(arr[y0:y0 + sh, x0:x0 + sw, :])
        grid = _grid_from_slices(slices, cfg, sw, sh)
        name = base_name if frame_count == 1 else f"{base_name}-{f + 1}"
        frames.append(Frame(name, grid))
    return frames


def import_frames(source: str | Path | list[str | Path],
                  cfg: ImportConfig | None = None) -> list[Frame]:
    """Import one or many images into a list of frames of equal dimensions."""
    cfg = cfg or ImportConfig()

    if cfg.frame_source is FrameSource.MULTI_FILE:
        paths = source if isinstance(source, list) else [source]
        paths = sorted((Path(p) for p in paths), key=natural_key)
        frames: list[Frame] = []
        for p in paths:
            arr = _load_rgba(p)
            # each file = exactly one frame, regardless of cfg.frame_count
            sub = ImportConfig(**{**cfg.__dict__,
                                  "frame_source": FrameSource.SINGLE_IMAGE,
                                  "frame_count": 1})
            frames.extend(_frames_from_array(arr, sub, p.stem))
        dims = frames[0].dims
        if any(fr.dims != dims for fr in frames):
            raise ValueError("Multi-file frames have differing dimensions.")
        return frames

    # SINGLE_IMAGE
    path = source[0] if isinstance(source, list) else source
    arr = _load_rgba(path)
    return _frames_from_array(arr, cfg, Path(path).stem)
