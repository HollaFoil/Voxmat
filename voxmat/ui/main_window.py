"""MainWindow — assembles the viewport and dock panels and wires interactions."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QSettings
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (QDockWidget, QFileDialog, QMainWindow,
                               QMessageBox)

from ..core.document import Document
from ..core.selection import (mask_at, mask_box, mask_by_color, mask_flood)
from ..io.binary_export import read_mmvox, write_mmvox
from ..io.image_import import import_frames
from ..io.paths import samples_dir
from .commands import AssignMaterialCommand, UndoStack
from .panels.frame_list import FrameListPanel
from .panels.import_dialog import ImportDialog
from .panels.material_editor import MaterialEditorPanel
from .panels.render_panel import RenderPanel
from .panels.selection_tools import SelectionToolsPanel
from .panels.view_panel import ViewPanel
from .render_window import RenderWindow
from .viewport.gl_widget import VoxelView

_SHIFT = Qt.ShiftModifier.value
_CTRL = Qt.ControlModifier.value


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Voxmat")
        self.resize(1280, 800)
        self.document = Document()
        self.undo_stack = UndoStack(self.document)
        self._box_corner = None   # first corner for the box select tool
        self._render_window: RenderWindow | None = None

        self.view = VoxelView()
        self.view.set_document(self.document)
        self.view.voxel_picked.connect(self._on_pick)
        self.setCentralWidget(self.view)

        self._build_docks()
        self._build_menus()

    # -- layout -----------------------------------------------------------
    def _build_docks(self):
        self.frame_panel = FrameListPanel(self.document)
        self.view_panel = ViewPanel(self.document, self.view)
        self.tools_panel = SelectionToolsPanel(self.document)
        self.material_panel = MaterialEditorPanel(self.document)
        self.material_panel.assign_requested.connect(self._assign_material)
        self.render_panel = RenderPanel(self.view)

        self._docks = []
        self._add_dock("View", self.view_panel, Qt.LeftDockWidgetArea)
        self._add_dock("Frames", self.frame_panel, Qt.LeftDockWidgetArea)
        self._add_dock("Select", self.tools_panel, Qt.LeftDockWidgetArea)
        mat_dock = self._add_dock("Materials", self.material_panel, Qt.RightDockWidgetArea)
        render_dock = self._add_dock("Render", self.render_panel, Qt.RightDockWidgetArea)
        # Materials and Render share one tabbed column to keep the viewport wide.
        self.tabifyDockWidget(mat_dock, render_dock)
        mat_dock.raise_()
        self._populate_window_menu()

    def _add_dock(self, title, widget, area):
        dock = QDockWidget(title, self)
        dock.setObjectName(f"dock_{title}")
        dock.setWidget(widget)
        self.addDockWidget(area, dock)
        self._docks.append(dock)
        return dock

    def _build_menus(self):
        m = self.menuBar().addMenu("&File")
        self._action(m, "Import sliced image…", self._import, "Ctrl+I")
        m.addSeparator()
        self._action(m, "Open .mmvox…", self._open_project, "Ctrl+O")
        self._action(m, "Export .mmvox…", self._export, "Ctrl+E")
        m.addSeparator()
        self._action(m, "Quit", self.close, "Ctrl+Q")

        e = self.menuBar().addMenu("&Edit")
        self._action(e, "Undo", self.undo_stack.undo, "Ctrl+Z")
        self._action(e, "Redo", self.undo_stack.redo, "Ctrl+Y")

        self.window_menu = self.menuBar().addMenu("&Window")
        self._populate_window_menu()

    def _populate_window_menu(self):
        """List every dock with a checkable show/hide action so closed panels
        can always be reopened."""
        menu = getattr(self, "window_menu", None)
        if menu is None:
            return
        menu.clear()
        self._action(menu, "Open render window", self._open_render_window)
        menu.addSeparator()
        for dock in self._docks:
            menu.addAction(dock.toggleViewAction())
        menu.addSeparator()
        self._action(menu, "Show all panels", self._show_all_panels)

    def _show_all_panels(self):
        for dock in self._docks:
            dock.show()

    def _open_render_window(self):
        if self._render_window is None:
            self._render_window = RenderWindow(self.document, self)
        self._render_window.show()
        self._render_window.raise_()
        self._render_window.activateWindow()

    def _action(self, menu, text, slot, shortcut=None):
        act = QAction(text, self)
        if shortcut:
            act.setShortcut(shortcut)
        act.triggered.connect(slot)
        menu.addAction(act)
        return act

    # -- file actions -----------------------------------------------------
    def _import(self):
        dlg = ImportDialog(self)
        if dlg.exec() != ImportDialog.Accepted:
            return
        source = dlg.source()
        if not source:
            return
        try:
            frames = import_frames(source, dlg.config())
            self.document.set_frames(frames)
        except Exception as exc:  # surface import errors to the user
            QMessageBox.critical(self, "Import failed", str(exc))
            return
        self.view.camera.frame_dims(self.document.dims)
        self.view.rebuild()
        self.statusBar().showMessage(
            f"Imported {len(frames)} frame(s), {self.document.dims} voxels", 5000)

    def _project_dir(self) -> str:
        """Initial directory for the .mmvox dialogs: the last used one, or the
        bundled samples folder on first run."""
        return QSettings("Voxmat", "paths").value("project_dir", str(samples_dir()))

    def _remember_project_dir(self, path: str) -> None:
        QSettings("Voxmat", "paths").setValue("project_dir", str(Path(path).parent))

    def _export(self):
        if not self.document.frames:
            QMessageBox.information(self, "Nothing to export", "Import a model first.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export .mmvox", self._project_dir(), "Voxmat voxel (*.mmvox)")
        if not path:
            return
        if not path.lower().endswith(".mmvox"):
            path += ".mmvox"
        write_mmvox(path, self.document)
        self._remember_project_dir(path)
        self.statusBar().showMessage(f"Exported {Path(path).name}", 5000)

    def _open_project(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open .mmvox", self._project_dir(), "Voxmat voxel (*.mmvox)")
        if not path:
            return
        try:
            doc = read_mmvox(path)
        except Exception as exc:
            QMessageBox.critical(self, "Open failed", str(exc))
            return
        self._remember_project_dir(path)
        self._swap_document(doc)

    def _swap_document(self, doc: Document):
        self.document = doc
        self.undo_stack = UndoStack(doc)
        self.view.set_document(doc)
        # Rebuild panels (and their docks) bound to the old document.
        for dock in self.findChildren(QDockWidget):
            dock.setParent(None)
            dock.deleteLater()
        self._build_docks()
        self.view.camera.frame_dims(doc.dims)
        self.view.rebuild()
        if self._render_window is not None:
            self._render_window.set_document(doc)

    # -- material assignment (undoable) -----------------------------------
    def _assign_material(self, material_id: int):
        frame = self.document.current_frame
        if frame is None or self.document.selection.is_empty:
            return
        if material_id not in self.document.materials:
            return
        cmd = AssignMaterialCommand(frame.grid, self.document.selection.mask, material_id)
        self.undo_stack.push(cmd)
        self.statusBar().showMessage(
            f"Assigned material {material_id} to {len(cmd.coords)} voxels", 4000)

    # -- selection picking ------------------------------------------------
    def _resolve_mode(self, modifiers: int) -> str:
        if modifiers & _SHIFT:
            return "add"
        if modifiers & _CTRL:
            return "subtract"
        return self.tools_panel.active_mode()

    def _on_pick(self, coord, modifiers):
        frame = self.document.current_frame
        if frame is None:
            return
        grid = frame.grid
        tool = self.tools_panel.active_tool()
        mode = self._resolve_mode(modifiers)

        if coord is None:
            if mode == "replace":
                self.document.selection.clear()
                self.document.emit_selection_changed()
            self._box_corner = None
            return

        # Report the clicked voxel's material, and (on a plain click) jump the
        # Materials panel to it so you can see/edit what's there.
        mid = int(grid.material_id[coord])
        mat = self.document.materials.get(mid)
        rgb = tuple(int(c) for c in grid.rgba[coord][:3])
        self.statusBar().showMessage(
            f"Voxel {coord}  •  material [{mid}] {mat.name}  •  rgb{rgb}", 6000)
        if not (modifiers & (_SHIFT | _CTRL)):
            self.material_panel.select_material(mid)

        if tool == "box":
            if self._box_corner is None:
                self._box_corner = coord
                self.statusBar().showMessage("Box: click the opposite corner", 4000)
                return
            mask = mask_box(grid, self._box_corner, coord)
            self._box_corner = None
        elif tool == "single":
            mask = mask_at(grid, coord)
        elif tool == "color":
            mask = mask_by_color(grid, tuple(int(c) for c in grid.rgba[coord]))
        elif tool == "flood":
            mask = mask_flood(grid, coord)
        else:
            mask = mask_at(grid, coord)

        self.document.selection.apply(mask, mode)
        self.document.emit_selection_changed()
