"""A standalone window showing the live path-traced render of the document.

Shares the same :class:`Document` as the main editor (so edits propagate), but
uses its own GL viewport in render mode and its own Render controls. The main
editor viewport stays in edit mode.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QDockWidget, QMainWindow

from ..core.document import Document
from .panels.render_panel import RenderPanel
from .viewport.gl_widget import VoxelView


class RenderWindow(QMainWindow):
    def __init__(self, document: Document, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Render view")
        self.resize(840, 760)

        self.view = VoxelView()
        self.setCentralWidget(self.view)

        self.panel = RenderPanel(self.view)
        dock = QDockWidget("Render", self)
        dock.setWidget(self.panel)
        self.addDockWidget(Qt.RightDockWidgetArea, dock)

        self.set_document(document)

    def set_document(self, document: Document) -> None:
        self.view.set_document(document)
        if document.dims != (0, 0, 0):
            self.view.camera.frame_dims(document.dims)

    def showEvent(self, e):
        super().showEvent(e)
        # Defer until the widget is laid out, then force GL init and start render.
        QTimer.singleShot(0, self._begin)

    def _begin(self):
        self.view.grabFramebuffer()             # forces initializeGL (tracer ready)
        if not self.panel.toggle.isChecked():
            self.panel.toggle.setChecked(True)  # start live render

    def closeEvent(self, e):
        # Stop GPU work when the window is closed/hidden.
        if self.panel.toggle.isChecked():
            self.panel.toggle.setChecked(False)
        super().closeEvent(e)
