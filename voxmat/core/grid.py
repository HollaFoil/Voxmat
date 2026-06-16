"""VoxelGrid — the per-frame voxel volume.

A grid stores two parallel arrays indexed ``[x, y, z]``:

* ``rgba``        uint8  shape (X, Y, Z, 4)  — colour; alpha 0 means *empty*.
* ``material_id`` uint16 shape (X, Y, Z)     — index into a MaterialLibrary;
                                               0 is the reserved "default" material.

Axis convention is X = width, Y = depth (image row within a slice), Z = height
(slice index). This matches how the sliced PNGs are read in ``io.image_import``.
"""

from __future__ import annotations

from typing import Iterable

import numpy as np

DEFAULT_MATERIAL_ID = 0


class VoxelGrid:
    __slots__ = ("rgba", "material_id")

    def __init__(self, rgba: np.ndarray, material_id: np.ndarray | None = None):
        if rgba.ndim != 4 or rgba.shape[3] != 4:
            raise ValueError(f"rgba must have shape (X, Y, Z, 4), got {rgba.shape}")
        if rgba.dtype != np.uint8:
            rgba = rgba.astype(np.uint8)
        self.rgba = rgba
        if material_id is None:
            material_id = np.zeros(rgba.shape[:3], dtype=np.uint16)
        if material_id.shape != rgba.shape[:3]:
            raise ValueError("material_id shape must match grid dimensions")
        self.material_id = material_id.astype(np.uint16, copy=False)

    # -- construction -----------------------------------------------------
    @classmethod
    def empty(cls, dims: tuple[int, int, int]) -> "VoxelGrid":
        x, y, z = dims
        return cls(np.zeros((x, y, z, 4), dtype=np.uint8))

    # -- queries ----------------------------------------------------------
    @property
    def dims(self) -> tuple[int, int, int]:
        return self.rgba.shape[0], self.rgba.shape[1], self.rgba.shape[2]

    @property
    def filled_mask(self) -> np.ndarray:
        """Boolean (X, Y, Z) mask where alpha > 0."""
        return self.rgba[..., 3] > 0

    @property
    def filled_count(self) -> int:
        return int(np.count_nonzero(self.rgba[..., 3]))

    def filled_coords(self) -> np.ndarray:
        """(N, 3) int array of filled voxel coordinates."""
        return np.argwhere(self.filled_mask)

    def distinct_colors(self) -> set[tuple[int, int, int, int]]:
        """Set of distinct RGBA tuples among filled voxels."""
        rgba = self.rgba[self.filled_mask]
        if rgba.size == 0:
            return set()
        uniq = np.unique(rgba.reshape(-1, 4), axis=0)
        return {tuple(int(c) for c in row) for row in uniq}

    # -- mutation ---------------------------------------------------------
    def assign_material(self, mask: np.ndarray, material_id: int) -> None:
        """Set material_id on every voxel selected by ``mask`` (X, Y, Z bool)."""
        self.material_id[mask] = material_id

    def flip_axis(self, axis: int) -> None:
        """Mirror the grid along axis 0=X, 1=Y, 2=Z (in place)."""
        self.rgba = np.ascontiguousarray(np.flip(self.rgba, axis))
        self.material_id = np.ascontiguousarray(np.flip(self.material_id, axis))

    def transpose_xy(self) -> None:
        """Swap the X and Y axes (mirror across the diagonal), in place."""
        self.rgba = np.ascontiguousarray(np.transpose(self.rgba, (1, 0, 2, 3)))
        self.material_id = np.ascontiguousarray(
            np.transpose(self.material_id, (1, 0, 2)))

    def rotate90(self, k: int = 1) -> None:
        """True 90°·k rotation about the vertical (Z) axis, in place."""
        self.rgba = np.ascontiguousarray(np.rot90(self.rgba, k, axes=(0, 1)))
        self.material_id = np.ascontiguousarray(
            np.rot90(self.material_id, k, axes=(0, 1)))

    def copy(self) -> "VoxelGrid":
        return VoxelGrid(self.rgba.copy(), self.material_id.copy())

    def __repr__(self) -> str:
        x, y, z = self.dims
        return f"VoxelGrid({x}x{y}x{z}, filled={self.filled_count})"
