"""Import dialog — choose file(s) and configure how to read the sliced image."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                               QFileDialog, QFormLayout, QHBoxLayout, QLabel,
                               QPushButton, QSpinBox, QVBoxLayout, QWidget)

from ...io.image_import import FrameSource, ImportConfig, Layout, probe_image


class ImportDialog(QDialog):
    """Collects a list of source paths + an :class:`ImportConfig`."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Import sliced image")
        self.setMinimumWidth(420)
        self._paths: list[str] = []

        layout = QVBoxLayout(self)

        # -- file picker --
        file_row = QHBoxLayout()
        self.path_label = QLabel("No file selected")
        self.path_label.setWordWrap(True)
        pick_btn = QPushButton("Choose file(s)…")
        pick_btn.clicked.connect(self._choose_files)
        file_row.addWidget(self.path_label, 1)
        file_row.addWidget(pick_btn)
        layout.addLayout(file_row)

        # -- config form --
        form = QFormLayout()
        self.source_combo = QComboBox()
        self.source_combo.addItem("Single image (frames stacked)", FrameSource.SINGLE_IMAGE)
        self.source_combo.addItem("Multiple files (1 frame each)", FrameSource.MULTI_FILE)
        self.source_combo.currentIndexChanged.connect(self._update_enabled)
        form.addRow("Frame source", self.source_combo)

        self.layout_combo = QComboBox()
        self.layout_combo.addItem("Vertical strip", Layout.VERTICAL_STRIP)
        self.layout_combo.addItem("Horizontal strip", Layout.HORIZONTAL_STRIP)
        self.layout_combo.addItem("Grid", Layout.GRID)
        form.addRow("Slice layout", self.layout_combo)

        self.slice_w = self._spin(0, 4096, 0)
        self.slice_h = self._spin(0, 4096, 0)
        self.slice_count = self._spin(0, 4096, 0)
        self.frame_count = self._spin(1, 4096, 1)
        self.grid_cols = self._spin(0, 4096, 0)
        form.addRow("Slice width (0=auto)", self.slice_w)
        form.addRow("Slice height (0=auto)", self.slice_h)
        form.addRow("Slice count / Z (0=auto)", self.slice_count)
        form.addRow("Frame count (single image)", self.frame_count)
        form.addRow("Grid columns (grid layout)", self.grid_cols)

        self.alpha_thresh = self._spin(0, 255, 0)
        form.addRow("Empty alpha threshold", self.alpha_thresh)

        flips = QHBoxLayout()
        self.flip_x = QCheckBox("Flip X")
        self.flip_y = QCheckBox("Flip Y")
        self.flip_z = QCheckBox("Flip Z")
        self.swap_xy = QCheckBox("Swap XY")
        for cb in (self.flip_x, self.flip_y, self.flip_z, self.swap_xy):
            flips.addWidget(cb)
        flip_w = QWidget()
        flip_w.setLayout(flips)
        form.addRow("Orientation", flip_w)

        layout.addLayout(form)

        self.info_label = QLabel("")
        self.info_label.setStyleSheet("color: #888;")
        layout.addWidget(self.info_label)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self._ok_button = buttons.button(QDialogButtonBox.Ok)
        self._ok_button.setEnabled(False)
        layout.addWidget(buttons)

        self.flip_z.setChecked(True)   # default: model right way up (grass on top)
        self._settings = QSettings("Voxmat", "ImportDialog")
        self._restore_settings()
        self._update_enabled()

    # -- persistence (remember last-used config) --------------------------
    _PERSISTED = ("slice_w", "slice_h", "slice_count", "frame_count",
                  "grid_cols", "alpha_thresh")
    _PERSISTED_FLAGS = ("flip_x", "flip_y", "flip_z", "swap_xy")

    def _restore_settings(self):
        s = self._settings
        for name in self._PERSISTED:
            val = s.value(name)
            if val is not None:
                getattr(self, name).setValue(int(val))
        for name in self._PERSISTED_FLAGS:
            # fall back to the checkbox's current default if not previously saved
            cb = getattr(self, name)
            default = "true" if cb.isChecked() else "false"
            cb.setChecked(s.value(name, default) == "true")
        li = s.value("layout_index")
        if li is not None:
            self.layout_combo.setCurrentIndex(int(li))
        si = s.value("source_index")
        if si is not None:
            self.source_combo.setCurrentIndex(int(si))

    def _save_settings(self):
        s = self._settings
        for name in self._PERSISTED:
            s.setValue(name, getattr(self, name).value())
        for name in self._PERSISTED_FLAGS:
            s.setValue(name, "true" if getattr(self, name).isChecked() else "false")
        s.setValue("layout_index", self.layout_combo.currentIndex())
        s.setValue("source_index", self.source_combo.currentIndex())

    def accept(self):
        self._save_settings()
        super().accept()

    @staticmethod
    def _spin(lo, hi, val):
        s = QSpinBox()
        s.setRange(lo, hi)
        s.setValue(val)
        return s

    def _choose_files(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Select sliced image(s)", "",
            "Images (*.png *.jpg *.jpeg *.bmp *.webp)")
        if not paths:
            return
        self._paths = paths
        if len(paths) == 1:
            self.path_label.setText(paths[0])
            info = probe_image(paths[0])
            self.info_label.setText(
                f"{info['width']}x{info['height']} {info['mode']} "
                f"— square-slice guess: {info['square_slice_count']}")
            if len(paths) == 1 and self.source_combo.currentData() is FrameSource.MULTI_FILE:
                pass
        else:
            self.path_label.setText(f"{len(paths)} files selected")
            self.source_combo.setCurrentIndex(
                self.source_combo.findData(FrameSource.MULTI_FILE))
            self.info_label.setText("Multiple files → one frame each.")
        self._ok_button.setEnabled(True)
        self._update_enabled()

    def _update_enabled(self):
        is_single = self.source_combo.currentData() is FrameSource.SINGLE_IMAGE
        self.frame_count.setEnabled(is_single)
        is_grid = self.layout_combo.currentData() is Layout.GRID
        self.grid_cols.setEnabled(is_grid)

    # -- results ----------------------------------------------------------
    def source(self):
        if self.source_combo.currentData() is FrameSource.MULTI_FILE:
            return list(self._paths)
        return self._paths[0] if self._paths else None

    def config(self) -> ImportConfig:
        return ImportConfig(
            slice_w=self.slice_w.value(),
            slice_h=self.slice_h.value(),
            slice_count=self.slice_count.value(),
            layout=self.layout_combo.currentData(),
            grid_cols=self.grid_cols.value(),
            frame_source=self.source_combo.currentData(),
            frame_count=self.frame_count.value(),
            flip_x=self.flip_x.isChecked(),
            flip_y=self.flip_y.isChecked(),
            flip_z=self.flip_z.isChecked(),
            swap_xy=self.swap_xy.isChecked(),
            alpha_threshold=self.alpha_thresh.value(),
        )
