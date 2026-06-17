"""M5 logic: hidden-voxel culling and undoable material assignment (no GL)."""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from voxmat.core.grid import VoxelGrid
from voxmat.ui.commands import AssignMaterialCommand, UndoStack
from voxmat.core.document import Document
from voxmat.core.frame import Frame
from voxmat.ui.viewport.mesh import visible_mask


def _solid_cube(n=4) -> VoxelGrid:
    rgba = np.zeros((n, n, n, 4), dtype=np.uint8)
    rgba[..., :] = (200, 100, 50, 255)
    return VoxelGrid(rgba)


def test_visible_mask_hides_interior():
    grid = _solid_cube(4)            # 4^3 = 64 filled, interior 2^3 = 8 hidden
    vis = visible_mask(grid.filled_mask)
    assert vis.sum() == 64 - 8       # only the shell remains
    # a corner is visible, the centre is not
    assert vis[0, 0, 0]
    assert not vis[1, 1, 1] and not vis[2, 2, 2]


def test_assign_command_undo_redo():
    grid = _solid_cube(3)
    doc = Document([Frame("f", grid)])
    stack = UndoStack(doc)
    mask = np.zeros(grid.dims, dtype=bool)
    mask[0, 0, 0] = True
    mask[1, 1, 1] = True

    stack.push(AssignMaterialCommand(doc, grid, mask, 7))
    assert grid.material_id[0, 0, 0] == 7 and grid.material_id[1, 1, 1] == 7

    stack.undo()
    assert grid.material_id[0, 0, 0] == 0 and grid.material_id[1, 1, 1] == 0

    stack.redo()
    assert grid.material_id[0, 0, 0] == 7


def test_orientation_preserves_materials_and_dims():
    # non-cubic so an XY swap is observable
    rgba = np.zeros((2, 3, 4, 4), dtype=np.uint8)
    rgba[0, 0, 0] = (10, 20, 30, 255)
    rgba[1, 2, 3] = (40, 50, 60, 255)
    grid = VoxelGrid(rgba)
    grid.material_id[1, 2, 3] = 9
    doc = Document([Frame("f", grid)])

    doc.flip_axis(0)                      # mirror X
    g = doc.frames[0].grid
    assert g.dims == (2, 3, 4)
    # the voxel that was at x=1 is now at x=0, material id carried along
    assert g.material_id[0, 2, 3] == 9
    assert tuple(g.rgba[0, 2, 3]) == (40, 50, 60, 255)

    doc.swap_xy()                         # X<->Y, dims (2,3,4) -> (3,2,4)
    g = doc.frames[0].grid
    assert g.dims == (3, 2, 4)
    assert g.material_id[2, 0, 3] == 9    # (x=0,y=2) -> (x=2,y=0)
    assert doc.selection.dims == (3, 2, 4)  # selection reset to new dims


def test_rotate90_is_a_real_rotation():
    # distinct marker voxels so we can tell rotation from a diagonal mirror
    rgba = np.zeros((3, 2, 1, 4), dtype=np.uint8)
    rgba[0, 0, 0] = (10, 0, 0, 255)   # corner A
    rgba[2, 0, 0] = (20, 0, 0, 255)   # corner B (far along X)
    grid = VoxelGrid(rgba)
    doc = Document([Frame("f", grid)])

    doc.rotate90(1)                    # 90° about Z: (3,2)->(2,3)
    g = doc.frames[0].grid
    assert g.dims == (2, 3, 1)
    # true rotation (not a diagonal mirror): A and B keep their separation
    assert tuple(g.rgba[1, 0, 0]) == (10, 0, 0, 255)
    assert tuple(g.rgba[1, 2, 0]) == (20, 0, 0, 255)
    # four rotations return to the original
    doc.rotate90(3)
    assert doc.frames[0].grid.dims == (3, 2, 1)
    assert tuple(doc.frames[0].grid.rgba[2, 0, 0]) == (20, 0, 0, 255)


def test_emission_use_voxel_color_roundtrips():
    import tempfile
    from voxmat.io import write_mmvox, read_mmvox
    rgba = np.zeros((2, 2, 2, 4), dtype=np.uint8)
    rgba[0, 0, 0] = (50, 120, 200, 255)
    doc = Document([Frame("f", VoxelGrid(rgba))])
    m = doc.materials.new("glow")
    doc.materials.update(replace(m, emission_use_voxel_color=True,
                                 emission_strength=4.0, flags=0))
    doc.frames[0].grid.material_id[0, 0, 0] = m.id
    with tempfile.TemporaryDirectory() as t:
        p = Path(t) / "x.mmvox"
        write_mmvox(p, doc)
        loaded = read_mmvox(p)
    lm = loaded.materials.get(m.id)
    assert lm.emission_use_voxel_color is True
    assert lm.emission_strength == 4.0


if __name__ == "__main__":
    test_visible_mask_hides_interior()
    test_assign_command_undo_redo()
    test_orientation_preserves_materials_and_dims()
    test_rotate90_is_a_real_rotation()
    test_emission_use_voxel_color_roundtrips()
    print("M5 logic OK")
