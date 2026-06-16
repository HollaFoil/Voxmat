"""Material editor — manage the library and edit per-material PBR/GI properties.

Editing writes straight back into the document's MaterialLibrary and emits
``materials_changed`` so the viewport (and anything else) can react. The property
widgets are built from a small declarative table so adding a new float property
is a one-line change here once the field exists on :class:`Material`.
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import (QDoubleSpinBox, QFormLayout,
                               QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QCheckBox, QPushButton,
                               QVBoxLayout, QWidget)

from ...core.document import Document
from ...core.material import Material
from ..widgets.color_picker import ColorPickerDialog


# Plain-language explanations shown as hover tooltips / info chips.
_TIPS = {
    "use_voxel_color": "Use each voxel's own imported colour as the base colour.\n"
                       "Untick to use the Albedo tint below as a flat colour instead.",
    "albedo": "Base surface colour. If 'use voxel colour' is on, this multiplies\n"
              "(tints) the voxel's colour; white = no change.",
    "metallic": "Is it metal? 0 = non-metal (wood, stone, plastic, skin).\n"
                "1 = metal (iron, gold, copper). Most materials are 0 or 1.",
    "roughness": "How blurry reflections are. 0 = mirror-smooth and shiny,\n"
                 "1 = matte / fully diffuse. Try ~0.3 for polished, ~0.8 for rough.",
    "emission": "The colour of light this voxel emits. Black = no glow.\n"
                "Use for lamps, fire, lava, screens.",
    "emission_strength": "How brightly it glows and lights nearby surfaces (global\n"
                         "illumination). 0 = off. Higher = brighter light source.",
    "transmission": "How see-through it is. 0 = solid/opaque, 1 = clear like\n"
                    "glass or water. Values between give frosted/tinted glass.",
    "ior": "Index of Refraction — how much light bends passing through a\n"
           "transparent material. Air 1.0, water 1.33, glass 1.5, diamond 2.4.\n"
           "Only matters when Transmission is above 0.",
}


class _ColorButton(QPushButton):
    """A button showing a colour swatch; opens a colour dialog on click."""

    _counter = 0

    def __init__(self, on_change, has_alpha=False):
        super().__init__()
        self._on_change = on_change
        self._has_alpha = has_alpha
        self._color = (255, 255, 255, 255)
        self.setFixedWidth(80)
        # Unique object name so the colour stylesheet is scoped to THIS button
        # and never cascades onto child widgets (e.g. a dialog parented here).
        _ColorButton._counter += 1
        self.setObjectName(f"colorbtn_{_ColorButton._counter}")
        self.clicked.connect(self._open)

    def set_color(self, rgba):
        c = [int(round(v * 255)) for v in rgba]
        while len(c) < 4:
            c.append(255)
        self._color = tuple(c)
        self.setStyleSheet(
            f"#{self.objectName()} {{ background-color: rgb({c[0]},{c[1]},{c[2]}); "
            f"border: 1px solid #555; }}")

    def _open(self):
        c = QColor(*self._color)
        # Parent the dialog to the top-level window (not this coloured button)
        # so the dialog doesn't inherit the button's background colour.
        chosen = ColorPickerDialog.get_color(c, self._has_alpha, self.window())
        if chosen is not None:
            self._color = (chosen.red(), chosen.green(), chosen.blue(), chosen.alpha())
            self.set_color([v / 255 for v in self._color])
            self._on_change()

    def rgba(self):
        return tuple(v / 255 for v in self._color)


class MaterialEditorPanel(QWidget):
    # emitted when the user clicks "Assign to selection" (material id)
    assign_requested = Signal(int)

    def __init__(self, document: Document, parent=None):
        super().__init__(parent)
        self.document = document
        self._current: Material | None = None
        self._loading = False

        root = QVBoxLayout(self)

        self.list = QListWidget()
        self.list.currentRowChanged.connect(self._on_select)
        root.addWidget(self.list, 1)

        btn_row = QHBoxLayout()
        add_btn = QPushButton("Add")
        add_btn.clicked.connect(self._add)
        del_btn = QPushButton("Remove")
        del_btn.clicked.connect(self._remove)
        btn_row.addWidget(add_btn)
        btn_row.addWidget(del_btn)
        root.addLayout(btn_row)

        # -- property form --
        form = QFormLayout()
        self.name_edit = QLineEdit()
        self.name_edit.editingFinished.connect(self._commit)
        form.addRow("Name", self.name_edit)

        self.use_voxel_color = QCheckBox("Use voxel colour as albedo")
        self.use_voxel_color.toggled.connect(self._commit)
        self._add_row(form, "", self.use_voxel_color, _TIPS["use_voxel_color"])

        self.albedo_btn = _ColorButton(self._commit, has_alpha=True)
        self._add_row(form, "Albedo tint", self.albedo_btn, _TIPS["albedo"])

        self.metallic = self._float(0, 1, 0.05)
        self._add_row(form, "Metallic", self.metallic, _TIPS["metallic"])
        self.roughness = self._float(0, 1, 0.05)
        self._add_row(form, "Roughness", self.roughness, _TIPS["roughness"])

        self.emission_use_voxel = QCheckBox("Emit the voxel's own colour")
        self.emission_use_voxel.setToolTip(
            "Use each voxel's imported colour as the glow colour, instead of the\n"
            "fixed Emission colour below. Great for multi-coloured emissive parts.")
        self.emission_use_voxel.toggled.connect(self._commit)
        self._add_row(form, "", self.emission_use_voxel, _TIPS["emission"])

        self.emission_btn = _ColorButton(self._commit)
        self._add_row(form, "Emission colour", self.emission_btn, _TIPS["emission"])
        self.emission_strength = self._float(0, 100, 0.1)
        self._add_row(form, "Emission strength", self.emission_strength,
                      _TIPS["emission_strength"])

        self.transmission = self._float(0, 1, 0.05)
        self._add_row(form, "Transmission", self.transmission, _TIPS["transmission"])
        self.ior = self._float(1.0, 3.0, 0.01)
        self._add_row(form, "IOR", self.ior, _TIPS["ior"])

        root.addLayout(form)

        assign_btn = QPushButton("Assign to selection")
        assign_btn.clicked.connect(self._assign)
        root.addWidget(assign_btn)

        select_btn = QPushButton("Select voxels using this material")
        select_btn.setToolTip("Select every voxel in the current frame that uses\n"
                              "the highlighted material — handy to see where it's applied.")
        select_btn.clicked.connect(self._select_by_material)
        root.addWidget(select_btn)

        self.sel_info = QLabel("No selection")
        self.sel_info.setWordWrap(True)
        self.sel_info.setStyleSheet("color: #aaa;")
        root.addWidget(self.sel_info)

        document.materials_changed.connect(self._reload)
        document.selection_changed.connect(self._update_selection_info)
        document.current_changed.connect(self._update_selection_info)
        self._reload()
        self._update_selection_info()

    def _float(self, lo, hi, step):
        s = QDoubleSpinBox()
        s.setRange(lo, hi)
        s.setSingleStep(step)
        s.setDecimals(3)
        s.valueChanged.connect(self._commit)
        return s

    @staticmethod
    def _add_row(form, label_text, widget, tip):
        """Add a form row with a hover tooltip and a small 'ⓘ' info chip."""
        widget.setToolTip(tip)
        label = QLabel((label_text + "  ⓘ") if label_text else "ⓘ")
        label.setToolTip(tip)
        form.addRow(label, widget)

    # -- library list -----------------------------------------------------
    @staticmethod
    def _swatch(mat: Material) -> QIcon:
        """A colour chip: emission colour if emissive, else the albedo tint."""
        if mat.emission_strength > 0:
            rgb = mat.emission_color
        else:
            rgb = mat.albedo[:3]
        pm = QPixmap(14, 14)
        pm.fill(QColor(*[int(round(min(max(c, 0.0), 1.0) * 255)) for c in rgb]))
        return QIcon(pm)

    def _reload(self):
        self._loading = True
        row = self.list.currentRow()
        self.list.clear()
        for mat in self.document.materials:
            tag = " ✦" if mat.emission_strength > 0 else ""
            item = QListWidgetItem(self._swatch(mat), f"[{mat.id}] {mat.name}{tag}")
            item.setData(Qt.UserRole, mat.id)
            self.list.addItem(item)
        if 0 <= row < self.list.count():
            self.list.setCurrentRow(row)
        elif self.list.count():
            self.list.setCurrentRow(0)
        self._loading = False

    def select_material(self, material_id: int) -> None:
        """Highlight the row for ``material_id`` (used when a voxel is picked)."""
        for i in range(self.list.count()):
            if self.list.item(i).data(Qt.UserRole) == material_id:
                self.list.setCurrentRow(i)
                break

    def _on_select(self, row: int):
        if row < 0:
            self._current = None
            return
        mat_id = self.list.item(row).data(Qt.UserRole)
        self._current = self.document.materials.get(mat_id)
        self._load_fields(self._current)

    def _load_fields(self, m: Material):
        self._loading = True
        self.name_edit.setText(m.name)
        self.use_voxel_color.setChecked(m.use_voxel_color)
        self.albedo_btn.set_color(m.albedo)
        self.metallic.setValue(m.metallic)
        self.roughness.setValue(m.roughness)
        self.emission_use_voxel.setChecked(m.emission_use_voxel_color)
        self.emission_btn.set_color(m.emission_color)
        self.emission_btn.setEnabled(not m.emission_use_voxel_color)
        self.emission_strength.setValue(m.emission_strength)
        self.transmission.setValue(m.transmission)
        self.ior.setValue(m.ior)
        self._loading = False

    # -- editing ----------------------------------------------------------
    def _commit(self, *args):
        if self._loading or self._current is None:
            return
        emission = self.emission_btn.rgba()[:3]
        updated = replace(
            self._current,
            name=self.name_edit.text() or self._current.name,
            use_voxel_color=self.use_voxel_color.isChecked(),
            albedo=self.albedo_btn.rgba(),
            metallic=self.metallic.value(),
            roughness=self.roughness.value(),
            emission_color=emission,
            emission_strength=self.emission_strength.value(),
            emission_use_voxel_color=self.emission_use_voxel.isChecked(),
            transmission=self.transmission.value(),
            ior=self.ior.value(),
        )
        self.emission_btn.setEnabled(not updated.emission_use_voxel_color)
        self._current = updated
        self.document.materials.update(updated)
        self.document.materials_changed.emit()

    def _add(self):
        mat = self.document.materials.new()
        self.document.materials_changed.emit()
        # select the new one
        for i in range(self.list.count()):
            if self.list.item(i).data(Qt.UserRole) == mat.id:
                self.list.setCurrentRow(i)
                break

    def _remove(self):
        if self._current is None or self._current.id == 0:
            return
        self.document.materials.remove(self._current.id)
        self.document.materials_changed.emit()

    def _assign(self):
        if self._current is None:
            return
        self.assign_requested.emit(self._current.id)

    def _select_by_material(self):
        if self._current is None:
            return
        frame = self.document.current_frame
        if frame is None:
            return
        mask = (frame.grid.material_id == self._current.id) & frame.grid.filled_mask
        self.document.selection.apply(mask, "replace")
        self.document.emit_selection_changed()

    def _update_selection_info(self):
        sel = self.document.selection
        frame = self.document.current_frame
        if frame is None or sel.is_empty:
            self.sel_info.setText("No selection")
            return
        ids = np.unique(frame.grid.material_id[sel.mask])
        if len(ids) == 1:
            m = self.document.materials.get(int(ids[0]))
            self.sel_info.setText(f"Selection: {sel.count} voxels • material [{m.id}] {m.name}")
        else:
            self.sel_info.setText(f"Selection: {sel.count} voxels • {len(ids)} different materials")

    def current_material_id(self) -> int:
        return self._current.id if self._current else 0
