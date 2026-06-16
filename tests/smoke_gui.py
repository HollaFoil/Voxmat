"""Short-lived GUI smoke test: load a model, render, screenshot, pick, quit.

Run:  python tests/smoke_gui.py
Opens a window briefly, writes _smoke.png next to this file, prints results.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from voxmat.io import ImportConfig, import_frames
from voxmat.ui.app import _configure_gl
from voxmat.ui.main_window import MainWindow

EXPORT = Path(__file__).resolve().parents[2] / "export"
OUT = Path(__file__).resolve().parent / "_smoke.png"


def run():
    _configure_gl()
    app = QApplication.instance() or QApplication(sys.argv)
    win = MainWindow()
    win.resize(900, 700)
    win.show()                      # show first so GL initializes (real usage)

    result = {"ok": False}

    def do_import():
        # Mimics MainWindow._import after the dialog: context already current
        # only inside paintGL, so this must not touch GL directly.
        frames = import_frames(EXPORT / "Grass.png", ImportConfig())
        win.document.set_frames(frames)
        win.view.camera.frame_dims(win.document.dims)
        win.view.rebuild()

    def finish():
        view = win.view
        img = view.grabFramebuffer()
        img.save(str(OUT))
        # try a pick roughly at the centre of the view
        coord = view.pick(view.width() // 2, view.height() // 2)
        print("context:", view.ctx)
        print("instances:", view._instance_count)
        print("screenshot:", OUT, img.width(), "x", img.height())
        print("center pick:", coord)
        result["ok"] = (view.ctx is not None and view._instance_count > 0
                        and coord is not None)
        app.quit()

    QTimer.singleShot(400, do_import)   # import after GL is up (real flow)
    QTimer.singleShot(1000, finish)
    app.exec()
    print("SMOKE", "OK" if result["ok"] else "FAILED")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(run())
