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
from PySide6.QtWidgets import (QDoubleSpinBox, QFormLayout, QGroupBox,
                               QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QCheckBox, QPushButton,
                               QVBoxLayout, QWidget)

from ...core.document import Document
from ...core.material import Material
from ..commands import AddMaterialCommand, MaterialEditCommand, RemoveMaterialCommand
from ..widgets.color_picker import ColorPickerDialog
from ..widgets.info import with_info


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
        self.setMinimumWidth(80)
        # Unique object name so the colour stylesheet is scoped to THIS button
        # and never cascades onto child widgets (e.g. a dialog parented here).
        _ColorButton._counter += 1
        self.setObjectName(f"colorbtn_{_ColorButton._counter}")
        self.clicked.connect(self._open)
        self._restyle()

    def set_color(self, rgba):
        c = [int(round(v * 255)) for v in rgba]
        while len(c) < 4:
            c.append(255)
        self._color = tuple(c)
        self._restyle()

    def setEnabled(self, on):                 # grey the swatch when disabled
        super().setEnabled(on)
        self._restyle()

    def _restyle(self):
        c = self._color
        if self.isEnabled():
            fill, border = f"rgb({c[0]},{c[1]},{c[2]})", "#555"
        else:
            fill, border = "#3a3a3a", "#444"
        self.setStyleSheet(
            f"#{self.objectName()} {{ background-color: {fill}; border: 1px solid {border}; }}")

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

    def __init__(self, document: Document, push_command=None, parent=None):
        super().__init__(parent)
        self.document = document
        self._push = push_command or (lambda cmd: cmd.do())
        self._current: Material | None = None
        self._loading = False

        root = QVBoxLayout(self)

        # ===== Materials: pick, add/remove, assign — the primary workflow =====
        mat_box = QGroupBox("Materials")
        mat_l = QVBoxLayout(mat_box)

        self.list = QListWidget()
        self.list.currentRowChanged.connect(self._on_select)
        mat_l.addWidget(self.list, 1)

        tools = QHBoxLayout()
        self.add_btn = self._icon_btn("+", "Add a new material", self._add)
        self.del_btn = self._icon_btn("−", "Remove the selected material", self._remove)
        tools.addWidget(self.add_btn)
        tools.addWidget(self.del_btn)
        tools.addStretch(1)
        mat_l.addLayout(tools)

        assign_btn = QPushButton("Assign to selection")
        assign_btn.setToolTip("Set the selected voxels to use the highlighted material.")
        assign_btn.clicked.connect(self._assign)
        mat_l.addWidget(assign_btn)

        select_btn = QPushButton("Select voxels using this material")
        select_btn.setToolTip("Select every voxel in the current frame that uses\n"
                              "the highlighted material — handy to see where it's applied.")
        select_btn.clicked.connect(self._select_by_material)
        mat_l.addWidget(select_btn)

        self.sel_info = QLabel("No selection")
        self.sel_info.setWordWrap(True)
        self.sel_info.setStyleSheet("color: #aaa;")
        mat_l.addWidget(self.sel_info)
        root.addWidget(mat_box)

        # ===== Properties of the highlighted material — secondary =====
        prop_box = QGroupBox("Properties")
        prop_l = QVBoxLayout(prop_box)
        form = QFormLayout()
        self.name_edit = QLineEdit()
        self.name_edit.editingFinished.connect(self._commit)
        form.addRow("Name", self.name_edit)

        self.use_voxel_color = QCheckBox()
        self.use_voxel_color.toggled.connect(self._commit)
        self.use_voxel_color.toggled.connect(self._sync_color_enabled)
        self._add_row(form, "Use voxel colour", self.use_voxel_color, _TIPS["use_voxel_color"])

        self.albedo_btn = _ColorButton(self._commit, has_alpha=True)
        self._albedo_label = self._add_row(form, "Albedo tint", self.albedo_btn, _TIPS["albedo"])

        self.metallic = self._float(0, 1, 0.05)
        self._add_row(form, "Metallic", self.metallic, _TIPS["metallic"])
        self.roughness = self._float(0, 1, 0.05)
        self._add_row(form, "Roughness", self.roughness, _TIPS["roughness"])

        self.emission_use_voxel = QCheckBox()
        self.emission_use_voxel.toggled.connect(self._commit)
        self.emission_use_voxel.toggled.connect(self._sync_color_enabled)
        self._add_row(form, "Emit voxel colour", self.emission_use_voxel,
                      "Use each voxel's imported colour as the glow colour, instead of\n"
                      "the fixed Emission colour below. Great for multi-coloured parts.")

        self.emission_btn = _ColorButton(self._commit)
        self._emission_label = self._add_row(form, "Emission colour", self.emission_btn, _TIPS["emission"])
        self.emission_strength = self._float(0, 100, 0.1)
        self._add_row(form, "Emission strength", self.emission_strength,
                      _TIPS["emission_strength"])

        self.transmission = self._float(0, 1, 0.05)
        self._add_row(form, "Transmission", self.transmission, _TIPS["transmission"])
        self.ior = self._float(1.0, 3.0, 0.01)
        self._add_row(form, "IOR", self.ior, _TIPS["ior"])

        prop_l.addLayout(form)

        self.reset_btn = QPushButton("Reset properties to default")
        self.reset_btn.setToolTip("Reset every property of the highlighted material to\n"
                                  "its default value (keeps its name).")
        self.reset_btn.clicked.connect(self._reset_to_default)
        prop_l.addWidget(self.reset_btn)
        root.addWidget(prop_box)

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

    def _icon_btn(self, text, tip, slot):
        b = QPushButton(text)
        b.setFixedWidth(30)
        b.setToolTip(tip)
        b.clicked.connect(slot)
        return b

    @staticmethod
    def _add_row(form, label_text, widget, tip):
        """Add a form row whose field carries a right-aligned, click-to-show 'ⓘ'.
        Returns the row's label so callers can grey it out with its field."""
        widget.setToolTip(tip)
        label = QLabel(label_text)
        form.addRow(label, with_info(widget, tip))
        return label

    def _sync_color_enabled(self):
        """A 'use voxel colour' checkbox makes its colour picker irrelevant, so
        disable (and grey) the picker and its label while it's ticked."""
        use_vox = self.use_voxel_color.isChecked()
        self.albedo_btn.setEnabled(not use_vox)
        self._albedo_label.setEnabled(not use_vox)
        emit_vox = self.emission_use_voxel.isChecked()
        self.emission_btn.setEnabled(not emit_vox)
        self._emission_label.setEnabled(not emit_vox)

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
            self.del_btn.setEnabled(False)
            self.reset_btn.setEnabled(False)
            return
        mat_id = self.list.item(row).data(Qt.UserRole)
        self._current = self.document.materials.get(mat_id)
        self.del_btn.setEnabled(mat_id != 0)      # the default material is permanent
        self.reset_btn.setEnabled(True)
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
        self.emission_strength.setValue(m.emission_strength)
        self.transmission.setValue(m.transmission)
        self.ior.setValue(m.ior)
        self._sync_color_enabled()
        self._loading = False

    # -- editing ----------------------------------------------------------
    def _commit(self, *args):
        if self._loading or self._current is None:
            return
        before = self._current
        emission = self.emission_btn.rgba()[:3]
        updated = replace(
            before,
            name=self.name_edit.text() or before.name,
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
        self._sync_color_enabled()
        self._current = updated
        # Undoable; rapid edits to the same material coalesce into one step.
        self._push(MaterialEditCommand(self.document, before, updated))

    def _add(self):
        mat = self.document.materials.new()          # allocates a stable id
        self._push(AddMaterialCommand(self.document, mat))
        # select the new one
        for i in range(self.list.count()):
            if self.list.item(i).data(Qt.UserRole) == mat.id:
                self.list.setCurrentRow(i)
                break

    def _remove(self):
        if self._current is None or self._current.id == 0:
            return
        self._push(RemoveMaterialCommand(self.document, self._current))

    def _reset_to_default(self):
        if self._current is None:
            return
        before = self._current
        # Fresh Material defaults, keeping the id and name.
        default = Material(id=before.id, name=before.name)
        self._current = default
        self._push(MaterialEditCommand(self.document, before, default))

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
