"""View & Orientation panel.

Quick, low-friction controls that act on the *already loaded* model — no need to
re-open the import dialog. Orientation flips/rotations are applied in place to
every frame, so material assignments are preserved.
"""

from __future__ import annotations

from PySide6.QtWidgets import (QCheckBox, QGridLayout, QGroupBox, QLabel,
                               QPushButton, QVBoxLayout, QWidget)

from ...core.document import Document
from ..viewport.gl_widget import VoxelView


class ViewPanel(QWidget):
    def __init__(self, document: Document, view: VoxelView, parent=None):
        super().__init__(parent)
        self.document = document
        self.view = view
        root = QVBoxLayout(self)

        recenter = QPushButton("Recenter view")
        recenter.setToolTip("Frame the whole model in the viewport.")
        recenter.clicked.connect(self._recenter)
        root.addWidget(recenter)

        # -- orientation --
        box = QGroupBox("Orientation")
        box.setToolTip("Flip or rotate the loaded model. Materials are kept.\n"
                       "Press a flip again to undo it.")
        grid = QGridLayout(box)
        grid.addWidget(self._flip_btn("Flip X", 0), 0, 0)
        grid.addWidget(self._flip_btn("Flip Y", 1), 0, 1)
        grid.addWidget(self._flip_btn("Flip Z", 2), 1, 0)
        rot = QPushButton("Rotate 90° (XY)")
        rot.setToolTip("Swap the X and Y axes (turn the model about the vertical).")
        rot.clicked.connect(self._rotate)
        grid.addWidget(rot, 1, 1)
        root.addWidget(box)

        self.dims_label = QLabel()
        root.addWidget(self.dims_label)
        document.frames_changed.connect(self._update_dims)

        # -- camera --
        cam = QGroupBox("Camera")
        cam_l = QVBoxLayout(cam)
        self.swap_cb = QCheckBox("Right mouse orbits (MagicaVoxel style)")
        self.swap_cb.setToolTip(
            "On: right-drag orbits, left-click selects (like MagicaVoxel).\n"
            "Off: left-drag orbits. Middle-drag always pans; wheel zooms.")
        self.swap_cb.setChecked(view.swap_camera_buttons)
        self.swap_cb.toggled.connect(self._toggle_swap)
        cam_l.addWidget(self.swap_cb)
        root.addWidget(cam)

        root.addStretch(1)
        self._update_dims()

    def _flip_btn(self, label, axis):
        b = QPushButton(label)
        b.setToolTip(f"Mirror the model along {label[-1]}.")
        b.clicked.connect(lambda: self._flip(axis))
        return b

    # -- actions ----------------------------------------------------------
    def _recenter(self):
        if self.document.dims != (0, 0, 0):
            self.view.camera.frame_dims(self.document.dims)
            self.view.update()

    def _flip(self, axis):
        if not self.document.frames:
            return
        self.document.flip_axis(axis)
        self._recenter()

    def _rotate(self):
        if not self.document.frames:
            return
        self.document.rotate90(1)
        self._recenter()

    def _toggle_swap(self, checked):
        self.view.swap_camera_buttons = checked

    def _update_dims(self):
        x, y, z = self.document.dims
        self.dims_label.setText(f"Model size: {x} × {y} × {z}"
                                if self.document.frames else "No model loaded")
