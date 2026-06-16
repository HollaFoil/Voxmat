"""Selection — a boolean mask over a grid, with composable selection ops.

Operations return *masks* (so the UI can preview / combine them) and the
``Selection`` object stores the committed mask. Add / subtract / replace let the
UI implement Shift / Ctrl modifiers cleanly.
"""

from __future__ import annotations

from collections import deque

import numpy as np

from .grid import VoxelGrid


class Selection:
    def __init__(self, dims: tuple[int, int, int]):
        self.mask = np.zeros(dims, dtype=bool)

    @property
    def dims(self) -> tuple[int, int, int]:
        return self.mask.shape  # type: ignore[return-value]

    @property
    def count(self) -> int:
        return int(np.count_nonzero(self.mask))

    @property
    def is_empty(self) -> bool:
        return not self.mask.any()

    # -- committing a new mask using a modifier ---------------------------
    def apply(self, mask: np.ndarray, mode: str = "replace") -> None:
        if mode == "replace":
            self.mask = mask.copy()
        elif mode == "add":
            self.mask |= mask
        elif mode == "subtract":
            self.mask &= ~mask
        elif mode == "intersect":
            self.mask &= mask
        else:
            raise ValueError(f"Unknown selection mode: {mode}")

    def clear(self) -> None:
        self.mask[:] = False

    def select_all_filled(self, grid: VoxelGrid) -> None:
        self.mask = grid.filled_mask.copy()


# -- mask builders (pure functions over a grid) ---------------------------

def mask_by_color(grid: VoxelGrid, color: tuple[int, int, int, int]) -> np.ndarray:
    """All filled voxels whose RGBA equals ``color``."""
    eq = np.all(grid.rgba == np.array(color, dtype=np.uint8), axis=-1)
    return eq & grid.filled_mask


def mask_at(grid: VoxelGrid, coord: tuple[int, int, int]) -> np.ndarray:
    """A single voxel, if it is filled."""
    mask = np.zeros(grid.dims, dtype=bool)
    if grid.filled_mask[coord]:
        mask[coord] = True
    return mask


def mask_box(grid: VoxelGrid, lo: tuple[int, int, int], hi: tuple[int, int, int],
             filled_only: bool = True) -> np.ndarray:
    """Axis-aligned box between corners ``lo`` and ``hi`` (inclusive)."""
    mask = np.zeros(grid.dims, dtype=bool)
    x0, x1 = sorted((lo[0], hi[0]))
    y0, y1 = sorted((lo[1], hi[1]))
    z0, z1 = sorted((lo[2], hi[2]))
    mask[x0:x1 + 1, y0:y1 + 1, z0:z1 + 1] = True
    if filled_only:
        mask &= grid.filled_mask
    return mask


def mask_flood(grid: VoxelGrid, coord: tuple[int, int, int],
               same_color: bool = True) -> np.ndarray:
    """Contiguous (6-connected) flood fill from ``coord``.

    If ``same_color`` only voxels sharing the seed's colour are joined,
    otherwise any filled neighbour is joined.
    """
    mask = np.zeros(grid.dims, dtype=bool)
    if not grid.filled_mask[coord]:
        return mask
    dims = grid.dims
    seed_color = grid.rgba[coord]
    filled = grid.filled_mask
    queue = deque([coord])
    mask[coord] = True
    while queue:
        x, y, z = queue.popleft()
        for dx, dy, dz in ((1, 0, 0), (-1, 0, 0), (0, 1, 0),
                           (0, -1, 0), (0, 0, 1), (0, 0, -1)):
            nx, ny, nz = x + dx, y + dy, z + dz
            if not (0 <= nx < dims[0] and 0 <= ny < dims[1] and 0 <= nz < dims[2]):
                continue
            if mask[nx, ny, nz] or not filled[nx, ny, nz]:
                continue
            if same_color and not np.array_equal(grid.rgba[nx, ny, nz], seed_color):
                continue
            mask[nx, ny, nz] = True
            queue.append((nx, ny, nz))
    return mask
