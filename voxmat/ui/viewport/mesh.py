"""Build GPU instance data from a VoxelGrid.

Each filled voxel becomes one instanced unit cube. The instance buffer carries:
    offset   3f  (voxel x,y,z)
    color    4f  (rgba 0..1)
    pick_id  1f  (encoded later for picking; here just the linear voxel index)
    selected 1f  (0/1 highlight flag)

For now every filled voxel is emitted. Interior-face culling / greedy meshing is
a later optimisation (M5); instanced cubes handle <=48^3 comfortably.
"""

from __future__ import annotations

import numpy as np

from ...core.grid import VoxelGrid

# 36-vertex unit cube (position + normal), CCW, spanning [0,1]^3.
# Built once at import.
_CUBE = None


def cube_geometry() -> np.ndarray:
    """(36, 6) float32 array of (px,py,pz, nx,ny,nz)."""
    global _CUBE
    if _CUBE is not None:
        return _CUBE
    # faces: (normal, four corners CCW seen from outside)
    faces = [
        ((0, 0, -1), [(0, 0, 0), (0, 1, 0), (1, 1, 0), (1, 0, 0)]),   # -Z
        ((0, 0, 1),  [(0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1)]),   # +Z
        ((0, -1, 0), [(0, 0, 0), (1, 0, 0), (1, 0, 1), (0, 0, 1)]),   # -Y
        ((0, 1, 0),  [(0, 1, 0), (0, 1, 1), (1, 1, 1), (1, 1, 0)]),   # +Y
        ((-1, 0, 0), [(0, 0, 0), (0, 0, 1), (0, 1, 1), (0, 1, 0)]),   # -X
        ((1, 0, 0),  [(1, 0, 0), (1, 1, 0), (1, 1, 1), (1, 0, 1)]),   # +X
    ]
    verts = []
    for normal, quad in faces:
        a, b, c, d = quad
        for tri in ((a, b, c), (a, c, d)):
            for p in tri:
                verts.append((*p, *normal))
    _CUBE = np.array(verts, dtype="f4")
    return _CUBE


def visible_mask(filled: np.ndarray) -> np.ndarray:
    """Filled voxels that expose at least one face (a 6-neighbour is empty/edge).

    Fully-enclosed interior voxels are invisible, so dropping them removes most
    of the geometry in solid blocks without changing the rendered silhouette.
    """
    # A voxel is hidden only if all six neighbours are filled. Pad with False
    # (out-of-bounds counts as empty) so boundary voxels stay visible.
    enclosed = np.ones_like(filled)
    for axis in (0, 1, 2):
        for shift in (1, -1):
            neighbour = np.roll(filled, shift, axis=axis)
            # voxels on the rolled-in edge have no real neighbour -> treat empty
            idx = 0 if shift == 1 else filled.shape[axis] - 1
            sl = [slice(None)] * 3
            sl[axis] = idx
            neighbour[tuple(sl)] = False
            enclosed &= neighbour
    return filled & ~enclosed


def build_instances(grid: VoxelGrid, selection_mask: np.ndarray | None = None,
                    cull_hidden: bool = True):
    """Return (offsets f4 (N,3), colors f4 (N,4), pick_ids f4 (N,), selected f4 (N,)).

    ``pick_ids`` are the linear indices ``x*Y*Z + y*Z + z`` so picking can map a
    decoded id straight back to a voxel coordinate. With ``cull_hidden`` only
    surface voxels are emitted (see :func:`visible_mask`).
    """
    filled = grid.filled_mask
    mask = visible_mask(filled) if cull_hidden else filled
    coords = np.argwhere(mask)                     # (N, 3)
    n = len(coords)
    if n == 0:
        empty = np.zeros((0, 3), "f4")
        return empty, np.zeros((0, 4), "f4"), np.zeros((0,), "f4"), np.zeros((0,), "f4")

    offsets = coords.astype("f4")
    xs, ys, zs = coords[:, 0], coords[:, 1], coords[:, 2]
    colors = grid.rgba[xs, ys, zs].astype("f4") / 255.0

    _, Y, Z = grid.dims
    pick_ids = (xs * (Y * Z) + ys * Z + zs).astype("f4")

    if selection_mask is not None:
        selected = selection_mask[xs, ys, zs].astype("f4")
    else:
        selected = np.zeros(n, dtype="f4")

    return offsets, colors, pick_ids, selected


def linear_id_to_coord(pick_id: int, dims: tuple[int, int, int]) -> tuple[int, int, int]:
    _, Y, Z = dims
    x = pick_id // (Y * Z)
    rem = pick_id % (Y * Z)
    y = rem // Z
    z = rem % Z
    return int(x), int(y), int(z)
