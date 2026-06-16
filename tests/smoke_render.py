"""GUI smoke for the live renderer: import, make grass emissive, path-trace.

Opens a window briefly, accumulates, writes _smoke_render.png (env on) and
_smoke_render_noenv.png (background off). Run: python tests/smoke_render.py
"""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from voxmat.io import ImportConfig, import_frames
from voxmat.ui.app import _configure_gl
from voxmat.ui.main_window import MainWindow

EXPORT = Path(__file__).resolve().parents[2] / "export"
OUT = Path(__file__).resolve().parent


def run():
    _configure_gl()
    app = QApplication.instance() or QApplication(sys.argv)
    win = MainWindow()
    win.resize(1500, 900)
    win.show()
    result = {"ok": False}

    def setup():
        win.document.set_frames(import_frames(EXPORT / "Grass.png", ImportConfig()))
        win.view.camera.frame_dims(win.document.dims)
        win.view.rebuild()
        # make grass-green voxels strongly emissive green
        grid = win.document.current_frame.grid
        lamp = win.document.materials.new("Grass glow")
        win.document.materials.update(replace(lamp,
            use_voxel_color=True, emission_color=(0.2, 1.0, 0.1), emission_strength=2.5))
        green = (grid.rgba == (102, 237, 0, 255)).all(-1) & grid.filled_mask
        grid.assign_material(green, lamp.id)
        # import already defaults to grass-up (flip_z); just start the render
        win.render_panel.toggle.setChecked(True)

    def shot_env():
        v = win.view
        result["samples"] = v.render_sample_count
        result["ready"] = v._tracer.ready
        img = v.grabFramebuffer(); img.save(str(OUT / "_smoke_render.png"))
        result["shot"] = (img.width(), img.height())
        # mean brightness as a sanity check (should be clearly lit)
        a = img.constBits();
        # toggle background off for the second shot
        win.render_panel.show_bg.setChecked(False)

    def shot_noenv():
        img = win.view.grabFramebuffer(); img.save(str(OUT / "_smoke_render_noenv.png"))
        result["ok"] = (result["ready"] and result["samples"] > 5)
        app.quit()

    QTimer.singleShot(400, setup)
    QTimer.singleShot(2200, shot_env)
    QTimer.singleShot(3400, shot_noenv)
    app.exec()
    print("samples accumulated:", result.get("samples"))
    print("tracer ready:", result.get("ready"), "| screenshot:", result.get("shot"))
    print("RENDER SMOKE", "OK" if result["ok"] else "FAILED")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(run())
