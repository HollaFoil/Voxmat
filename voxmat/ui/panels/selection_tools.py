"""Selection tools panel — pick the active select mode and combine modifier.

The actual selection is applied in MainWindow's pick handler, which reads
``active_tool()`` and ``active_mode()`` here. Tools:

* single  — the clicked voxel only
* color   — every voxel of the clicked colour (whole grid)
* flood   — contiguous same-colour region from the click
* box      — two clicks define an inclusive box
"""

from __future__ import annotations

from PySide6.QtWidgets import (QButtonGroup, QLabel, QPushButton, QRadioButton,
                               QVBoxLayout, QWidget)

from ...core.document import Document


class SelectionToolsPanel(QWidget):
    def __init__(self, document: Document, parent=None):
        super().__init__(parent)
        self.document = document
        layout = QVBoxLayout(self)

        layout.addWidget(QLabel("<b>Select tool</b>"))
        self.tool_group = QButtonGroup(self)
        self._tools = {}
        for key, label in (("single", "Single voxel"),
                           ("color", "Same colour (all)"),
                           ("flood", "Contiguous colour"),
                           ("box", "Box (two clicks)")):
            rb = QRadioButton(label)
            layout.addWidget(rb)
            self.tool_group.addButton(rb)
            self._tools[key] = rb
        self._tools["color"].setChecked(True)

        layout.addSpacing(8)
        layout.addWidget(QLabel("<b>Combine</b> (or hold Shift/Ctrl)"))
        self.mode_group = QButtonGroup(self)
        self._modes = {}
        for key, label in (("replace", "Replace"),
                           ("add", "Add (Shift)"),
                           ("subtract", "Subtract (Ctrl)")):
            rb = QRadioButton(label)
            layout.addWidget(rb)
            self.mode_group.addButton(rb)
            self._modes[key] = rb
        self._modes["replace"].setChecked(True)

        layout.addSpacing(8)
        all_btn = QPushButton("Select all filled")
        all_btn.clicked.connect(self._select_all)
        clear_btn = QPushButton("Clear selection")
        clear_btn.clicked.connect(self._clear)
        layout.addWidget(all_btn)
        layout.addWidget(clear_btn)

        self.count_label = QLabel("0 voxels selected")
        layout.addWidget(self.count_label)
        document.selection_changed.connect(self._update_count)

        layout.addStretch(1)

    def active_tool(self) -> str:
        for key, rb in self._tools.items():
            if rb.isChecked():
                return key
        return "single"

    def active_mode(self) -> str:
        for key, rb in self._modes.items():
            if rb.isChecked():
                return key
        return "replace"

    def _select_all(self):
        frame = self.document.current_frame
        if frame is None:
            return
        self.document.selection.select_all_filled(frame.grid)
        self.document.emit_selection_changed()

    def _clear(self):
        self.document.selection.clear()
        self.document.emit_selection_changed()

    def _update_count(self):
        self.count_label.setText(f"{self.document.selection.count} voxels selected")
