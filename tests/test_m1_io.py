"""M1 headless verification: import + binary round-trip against real exports.

Run with:  python -m pytest tests/ -q     (from the Voxmat folder)
or simply: python tests/test_m1_io.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

# Allow running directly (python tests/test_m1_io.py) without install.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from voxmat.core.document import Document
from voxmat.core.material import Material
from voxmat.io import (FrameSource, ImportConfig, import_frames, probe_image,
                        read_mmvox, write_mmvox)

# The MagicaVoxel export folder sits two levels up from Voxmat/.
EXPORT = Path(__file__).resolve().parents[2] / "export"


def test_grass_import_counts():
    cfg = ImportConfig()  # defaults: vertical strip, auto square slices
    frames = import_frames(EXPORT / "Grass.png", cfg)
    assert len(frames) == 1
    grid = frames[0].grid
    assert grid.dims == (16, 16, 16)
    assert grid.filled_count == 3722
    colors = {c[:3] for c in grid.distinct_colors()}
    assert colors == {(226, 150, 0), (102, 237, 0)}


def test_furnace_import_dims():
    info = probe_image(EXPORT / "Blocks" / "Furnace.png")
    assert info["square_slice_count"] == 32
    frames = import_frames(EXPORT / "Blocks" / "Furnace.png", ImportConfig())
    assert frames[0].grid.dims == (32, 32, 32)


def test_beltdown_multifile_frames():
    folder = EXPORT / "Blocks" / "BeltDown"
    files = sorted(folder.glob("BeltDown-*.png"))
    assert len(files) == 4
    cfg = ImportConfig(frame_source=FrameSource.MULTI_FILE)
    frames = import_frames(list(files), cfg)
    assert len(frames) == 4
    dims = {f.dims for f in frames}
    assert dims == {(16, 16, 16)}


def test_binary_roundtrip():
    frames = import_frames(EXPORT / "Grass.png", ImportConfig())
    doc = Document(frames)
    # Make an emissive material and assign it to the grass-green voxels.
    grass = doc.materials.new("Grass")
    grass = grass.__class__(**{**grass.__dict__,
                               "emission_color": (0.2, 1.0, 0.1),
                               "emission_strength": 3.0})
    doc.materials.update(grass)
    grid = doc.frames[0].grid
    green = (grid.rgba == (102, 237, 0, 255)).all(-1) & grid.filled_mask
    grid.assign_material(green, grass.id)

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "grass.mmvox"
        write_mmvox(out, doc)
        loaded = read_mmvox(out)

    lg = loaded.frames[0].grid
    assert lg.dims == grid.dims
    assert lg.filled_count == grid.filled_count
    # green voxels kept their material id
    lgreen = (lg.rgba == (102, 237, 0, 255)).all(-1) & lg.filled_mask
    assert (lg.material_id[lgreen] == grass.id).all()
    # material props survived
    lm = loaded.materials.get(grass.id)
    assert lm.emission_strength == 3.0
    assert abs(lm.emission_color[1] - 1.0) < 1e-6


if __name__ == "__main__":
    test_grass_import_counts()
    test_furnace_import_dims()
    test_beltdown_multifile_frames()
    test_binary_roundtrip()
    print("M1 OK: all checks passed")
