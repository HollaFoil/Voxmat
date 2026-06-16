"""Render panel — toggle the live path-traced preview and control it.

Holds a reference to the :class:`VoxelView` (like the View panel) and drives its
render mode, environment map and quality parameters.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QFormLayout,
                               QGroupBox, QHBoxLayout, QLabel, QPushButton,
                               QSlider, QVBoxLayout, QWidget)

from ...render import available_environments, load_environment
from ..viewport.gl_widget import VoxelView
from ..widgets.color_picker import ColorPickerDialog


class RenderPanel(QWidget):
    def __init__(self, view: VoxelView, parent=None):
        super().__init__(parent)
        self.view = view
        self._env_cache: dict[str, object] = {}
        root = QVBoxLayout(self)

        self.toggle = QPushButton("Live render")
        self.toggle.setCheckable(True)
        self.toggle.setToolTip("Path-trace the scene with global illumination.\n"
                               "Converges progressively; resets when you move the\n"
                               "camera or change materials.")
        self.toggle.toggled.connect(self._on_toggle)
        root.addWidget(self.toggle)

        self.status = QLabel("Samples: 0")
        root.addWidget(self.status)
        restart = QPushButton("Restart accumulation")
        restart.clicked.connect(lambda: self.view._camera_moved())
        root.addWidget(restart)

        # -- environment --
        env_box = QGroupBox("Environment / background")
        env_l = QVBoxLayout(env_box)
        self.show_bg = QCheckBox("Show background")
        self.show_bg.setChecked(True)
        self.show_bg.setToolTip("On: the HDRI lights the scene and shows behind it.\n"
                                "Off: a neutral ambient still lights surfaces, but no\n"
                                "backdrop is drawn.")
        self.show_bg.toggled.connect(self._apply_environment)
        env_l.addWidget(self.show_bg)

        self.env_combo = QComboBox()
        self.env_combo.setToolTip(
            "Procedural sky is crisp at any resolution. The bundled HDRIs are\n"
            "low-resolution (512px) lighting maps and look blurry as a backdrop —\n"
            "use 'Load image / HDR…' for a high-resolution photographic background.")
        self.env_combo.addItem("Procedural sky (crisp)", None)
        for p in available_environments():
            self.env_combo.addItem(p.name, str(p))
        self.env_combo.setCurrentIndex(0)   # default to the crisp sky
        self.env_combo.currentIndexChanged.connect(self._apply_environment)
        env_l.addWidget(self.env_combo)

        load_btn = QPushButton("Load image / HDR…")
        load_btn.clicked.connect(self._load_custom)
        env_l.addWidget(load_btn)

        flip_row = QHBoxLayout()
        self.flip_h = QCheckBox("Flip env horizontally")
        self.flip_v = QCheckBox("Flip env vertically")
        for cb in (self.flip_h, self.flip_v):
            cb.setToolTip("Correct mirrored / upside-down HDRIs (texture only).")
            cb.toggled.connect(self._apply_params)
            flip_row.addWidget(cb)
        env_l.addLayout(flip_row)

        ambient_row = QHBoxLayout()
        ambient_row.addWidget(QLabel("Ambient (background off)"))
        self.ambient_btn = QPushButton()
        self.ambient_btn.setToolTip("Flat fill light used when the background is off.")
        self._ambient = (0.35, 0.37, 0.4)
        self._refresh_ambient_swatch()
        self.ambient_btn.clicked.connect(self._pick_ambient)
        ambient_row.addWidget(self.ambient_btn)
        env_l.addLayout(ambient_row)
        root.addWidget(env_box)

        # -- quality --
        q_box = QGroupBox("Quality")
        form = QFormLayout(q_box)
        self.tonemap = QComboBox()
        self.tonemap.setToolTip("Maps high-dynamic-range light to screen colours.\n"
                                "Filmic lifts shadows; ACES is punchier; Reinhard is\n"
                                "soft; Linear is raw (can clip).")
        for name in ("Filmic", "ACES", "Reinhard", "Linear"):
            self.tonemap.addItem(name)
        self.tonemap.currentIndexChanged.connect(self._apply_params)
        form.addRow("Tone map  ⓘ", self.tonemap)

        self.exposure = self._slider(5, 400, 100,
            "Brightness of the final image (display only — no re-render).")
        form.addRow("Exposure  ⓘ", self.exposure)
        self.gi_scale = self._slider(0, 400, 100,
            "Strength of the bounced/indirect light from the voxel GI cache.")
        form.addRow("GI intensity  ⓘ", self.gi_scale)
        self.env_intensity = self._slider(0, 400, 70,
            "How strongly the environment lights the scene and how bright the\n"
            "background looks.")
        form.addRow("Env intensity  ⓘ", self.env_intensity)

        self.glass_density = self._slider(0, 300, 60,
            "Optical density of glass/transmissive materials: higher values tint\n"
            "and darken thicker glass more strongly (Beer-Lambert absorption).")
        form.addRow("Glass density  ⓘ", self.glass_density)

        self.denoise = QCheckBox("Denoise")
        self.denoise.setChecked(False)   # off by default — see tooltip
        self.denoise.setToolTip("Edge-avoiding À-Trous filter. Guided only by surface\n"
                                "albedo/normal, so it blurs reflected/refracted detail on\n"
                                "glossy and glass surfaces. Off by default; prefer letting\n"
                                "the render accumulate more samples instead.")
        self.denoise.toggled.connect(self._apply_params)
        form.addRow("", self.denoise)

        root.addWidget(q_box)
        self.exposure.valueChanged.connect(self._apply_params)
        self.gi_scale.valueChanged.connect(self._apply_params)
        self.env_intensity.valueChanged.connect(self._apply_params)
        self.glass_density.valueChanged.connect(self._apply_params)

        root.addStretch(1)

        self._timer = QTimer(self)
        self._timer.setInterval(200)
        self._timer.timeout.connect(self._update_status)

    def _slider(self, lo, hi, val, tip):
        s = QSlider(Qt.Horizontal)
        s.setRange(lo, hi)
        s.setValue(val)
        s.setToolTip(tip)
        return s

    # -- environment ------------------------------------------------------
    def _current_env_array(self):
        if self.env_combo.count() == 0:
            return None
        path = self.env_combo.currentData()
        if path is None:
            return None
        if path not in self._env_cache:
            try:
                self._env_cache[path] = load_environment(path)
            except Exception:
                self._env_cache[path] = None
        return self._env_cache[path]

    def _apply_environment(self):
        self.view.set_environment(self._current_env_array(), self.show_bg.isChecked())

    def _load_custom(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Load environment map", "",
            "Images (*.hdr *.exr *.png *.jpg *.jpeg *.bmp)")
        if not path:
            return
        name = Path(path).name
        self.env_combo.addItem(name, path)
        self.env_combo.setCurrentIndex(self.env_combo.count() - 1)
        self._apply_environment()

    # -- ambient ----------------------------------------------------------
    def _refresh_ambient_swatch(self):
        r, g, b = (int(round(c * 255)) for c in self._ambient)
        self.ambient_btn.setStyleSheet(
            f"background-color: rgb({r},{g},{b}); border: 1px solid #555;")

    def _pick_ambient(self):
        r, g, b = (int(round(c * 255)) for c in self._ambient)
        chosen = ColorPickerDialog.get_color(QColor(r, g, b), has_alpha=False, parent=self)
        if chosen is None:
            return
        self._ambient = (chosen.red() / 255.0, chosen.green() / 255.0, chosen.blue() / 255.0)
        self._refresh_ambient_swatch()
        self._apply_params()

    # -- params -----------------------------------------------------------
    def _apply_params(self):
        self.view.set_render_params(
            exposure=self.exposure.value() / 100.0,
            tonemap=self.tonemap.currentIndex(),
            gi_scale=self.gi_scale.value() / 100.0,
            env_intensity=self.env_intensity.value() / 100.0,
            glass_density=self.glass_density.value() / 100.0,
            env_flip_h=self.flip_h.isChecked(),
            env_flip_v=self.flip_v.isChecked(),
            ambient=self._ambient,
            denoise_enabled=self.denoise.isChecked(),
        )

    # -- toggle / status --------------------------------------------------
    def _on_toggle(self, checked: bool):
        if checked:
            self._apply_environment()
            self._apply_params()
            self.view.set_render_mode(True)
            self._timer.start()
        else:
            self.view.set_render_mode(False)
            self._timer.stop()
            self.status.setText("Samples: 0")

    def _update_status(self):
        n = self.view.render_sample_count
        cap = self.view.render_max_samples
        done = " (converged)" if self.view.render_converged else ""
        self.status.setText(f"Samples: {n} / {cap}{done}")
