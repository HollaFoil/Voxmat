"""VoxelView — a ModernGL-backed QOpenGLWidget that renders the current frame
as instanced cubes, supports orbit/pan/zoom, highlights the selection, and
performs GPU colour-ID picking of individual voxels.
"""

from __future__ import annotations

import moderngl
import numpy as np
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QMouseEvent, QWheelEvent
from PySide6.QtOpenGLWidgets import QOpenGLWidget

from ...core.document import Document
from ...render import PathTracer
from . import mesh
from .camera import OrbitCamera
from .picking import decode_rgb_to_id

_MAIN_VS = """
#version 330
uniform mat4 u_mvp;
in vec3 in_pos;
in vec3 in_normal;
in vec3 in_offset;
in vec4 in_color;
in float in_sel;
out vec3 v_normal;
out vec4 v_color;
out float v_sel;
void main() {
    vec3 world = in_pos + in_offset;
    gl_Position = u_mvp * vec4(world, 1.0);
    v_normal = in_normal;
    v_color = in_color;
    v_sel = in_sel;
}
"""

_MAIN_FS = """
#version 330
uniform vec3 u_light_dir;
in vec3 v_normal;
in vec4 v_color;
in float v_sel;
out vec4 f_color;
void main() {
    float diff = max(dot(normalize(v_normal), normalize(u_light_dir)), 0.0);
    float shade = 0.35 + 0.65 * diff;          // ambient + lambert
    vec3 rgb = v_color.rgb * shade;
    if (v_sel > 0.5) {
        rgb = mix(rgb, vec3(1.0, 0.55, 0.05), 0.55);   // selection tint
    }
    f_color = vec4(rgb, 1.0);
}
"""

_PICK_VS = """
#version 330
uniform mat4 u_mvp;
in vec3 in_pos;
in vec3 in_offset;
in float in_pick;
flat out float v_pick;
void main() {
    gl_Position = u_mvp * vec4(in_pos + in_offset, 1.0);
    v_pick = in_pick;
}
"""

_PICK_FS = """
#version 330
flat in float v_pick;
out vec4 f_color;
void main() {
    float id = v_pick + 1.0;                    // 0 reserved for background
    float r = mod(id, 256.0);
    float g = mod(floor(id / 256.0), 256.0);
    float b = mod(floor(id / 65536.0), 256.0);
    f_color = vec4(r / 255.0, g / 255.0, b / 255.0, 1.0);
}
"""


class VoxelView(QOpenGLWidget):
    # emitted on a click-pick: (coord or None, keyboard modifiers int)
    voxel_picked = Signal(object, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)
        self.camera = OrbitCamera()
        self.document: Document | None = None

        self.ctx: moderngl.Context | None = None
        self._main_prog = None
        self._pick_prog = None
        self._cube_vbo = None
        self._cube_pos_vbo = None
        self._inst_offset = None
        self._inst_color = None
        self._inst_pick = None
        self._inst_sel = None
        self._main_vao = None
        self._pick_vao = None
        self._pick_fbo = None
        self._instance_count = 0

        # CPU-side instance data prepared by rebuild(); uploaded to the GPU only
        # when the GL context is current (paintGL / before a pick).
        self._cpu = None              # dict of f4 arrays: offsets/colors/pick/sel
        self._needs_upload = False    # full re-upload + VAO rebuild pending
        self._needs_sel_upload = False  # only the selection buffer changed

        self._last_pos = None
        self._press_pos = None
        self._dragged = False

        # MagicaVoxel-style by default: right-drag orbits, left is select/pick.
        # When False: left-drag orbits (and left-click still picks).
        self.swap_camera_buttons = True

        # path-traced render mode (None until GL is initialised)
        self.render_mode = False
        self._tracer: PathTracer | None = None
        self._env_array = None        # cached environment (float32 HxWx3) or None
        self._env_enabled = True

    # -- wiring -----------------------------------------------------------
    def set_document(self, doc: Document) -> None:
        self.document = doc
        doc.current_changed.connect(self.rebuild)
        doc.frames_changed.connect(self.rebuild)
        doc.selection_changed.connect(self.refresh_selection)
        doc.materials_changed.connect(self.update)
        # keep the path tracer in sync while render mode is active
        doc.current_changed.connect(self._render_invalidate)
        doc.frames_changed.connect(self._render_invalidate)
        doc.materials_changed.connect(self._render_invalidate)
        if doc.dims != (0, 0, 0):
            self.camera.frame_dims(doc.dims)
        self.rebuild()

    # -- GL lifecycle -----------------------------------------------------
    def initializeGL(self) -> None:
        self.ctx = moderngl.create_context()
        self.ctx.enable(moderngl.DEPTH_TEST | moderngl.CULL_FACE)
        self.ctx.cull_face = "back"
        self._main_prog = self.ctx.program(vertex_shader=_MAIN_VS, fragment_shader=_MAIN_FS)
        self._pick_prog = self.ctx.program(vertex_shader=_PICK_VS, fragment_shader=_PICK_FS)
        cube = mesh.cube_geometry()
        self._cube_vbo = self.ctx.buffer(cube.tobytes())
        # position-only copy for the pick pass (no normal attribute)
        self._cube_pos_vbo = self.ctx.buffer(
            np.ascontiguousarray(cube[:, :3], dtype="f4").tobytes())
        # placeholder instance buffers (filled by rebuild)
        self._inst_offset = self.ctx.buffer(reserve=1, dynamic=True)
        self._inst_color = self.ctx.buffer(reserve=1, dynamic=True)
        self._inst_pick = self.ctx.buffer(reserve=1, dynamic=True)
        self._inst_sel = self.ctx.buffer(reserve=1, dynamic=True)
        self._tracer = PathTracer(self.ctx)
        # If render mode was enabled before GL init (e.g. a pop-out window),
        # push the scene/environment now that the tracer exists.
        if self.render_mode and self.document is not None:
            self._sync_tracer_scene()
            self._tracer.set_environment(self._env_array, self._env_enabled)
        # If a frame was prepared before the context existed, flag it for upload.
        if self._cpu is not None:
            self._needs_upload = True

    def _build_vaos(self) -> None:
        self._main_vao = self.ctx.vertex_array(
            self._main_prog,
            [
                (self._cube_vbo, "3f 3f", "in_pos", "in_normal"),
                (self._inst_offset, "3f/i", "in_offset"),
                (self._inst_color, "4f/i", "in_color"),
                (self._inst_sel, "1f/i", "in_sel"),
            ],
        )
        self._pick_vao = self.ctx.vertex_array(
            self._pick_prog,
            [
                (self._cube_pos_vbo, "3f", "in_pos"),
                (self._inst_offset, "3f/i", "in_offset"),
                (self._inst_pick, "1f/i", "in_pick"),
            ],
        )

    def rebuild(self) -> None:
        """Prepare CPU instance data for the current frame; upload happens in GL.

        Safe to call from any thread/context (e.g. menu handlers): it only
        touches numpy, then requests a repaint where the upload occurs.
        """
        if self.document is None:
            return
        frame = self.document.current_frame
        if frame is None:
            self._cpu = None
            self._instance_count = 0
            self.update()
            return
        offsets, colors, pick_ids, selected = mesh.build_instances(
            frame.grid, self.document.selection.mask)
        self._cpu = {"offsets": offsets, "colors": colors,
                     "pick": pick_ids, "sel": selected}
        self._instance_count = len(offsets)
        self._needs_upload = True
        self.update()

    def refresh_selection(self) -> None:
        if self.document is None or self._cpu is None:
            return
        frame = self.document.current_frame
        if frame is None:
            return
        _, _, _, selected = mesh.build_instances(frame.grid, self.document.selection.mask)
        self._cpu["sel"] = selected
        self._needs_sel_upload = True
        self.update()

    @staticmethod
    def _write(buf: moderngl.Buffer, arr: np.ndarray) -> None:
        data = np.ascontiguousarray(arr, dtype="f4").tobytes()
        buf.orphan(max(len(data), 1))
        if data:
            buf.write(data)

    def _ensure_uploaded(self) -> None:
        """Push pending CPU data to GPU buffers. Must run with context current."""
        if self._inst_offset is None or self._cpu is None:
            return
        if self._needs_upload:
            self._write(self._inst_offset, self._cpu["offsets"])
            self._write(self._inst_color, self._cpu["colors"])
            self._write(self._inst_pick, self._cpu["pick"])
            self._write(self._inst_sel, self._cpu["sel"])
            self._build_vaos()
            self._needs_upload = False
            self._needs_sel_upload = False
        elif self._needs_sel_upload:
            self._write(self._inst_sel, self._cpu["sel"])
            self._needs_sel_upload = False

    # -- path-traced render mode -----------------------------------------
    def set_render_mode(self, on: bool) -> None:
        if on == self.render_mode:
            return
        self.render_mode = on
        if on and self._tracer is not None:
            self.makeCurrent()
            self._sync_tracer_scene()
            # _env_array None => crisp procedural sky (the default)
            self._tracer.set_environment(self._env_array, self._env_enabled)
            self._tracer.reset()
            self.doneCurrent()
        self.update()

    def _sync_tracer_scene(self) -> None:
        if self._tracer is None or self.document is None:
            return
        frame = self.document.current_frame
        if frame is None:
            return
        self._tracer.upload_volume(frame.grid)
        self._tracer.upload_materials(self.document.materials)

    def _render_invalidate(self) -> None:
        """Re-sync the tracer scene if a doc change happened during render mode."""
        if not self.render_mode or self._tracer is None:
            return
        self.makeCurrent()
        self._sync_tracer_scene()
        self._tracer.reset()
        self.doneCurrent()
        self.update()

    def set_environment(self, array, enabled: bool) -> None:
        """Set the environment map (float32 HxWx3 or None) and visibility."""
        self._env_array = array
        self._env_enabled = enabled
        if self._tracer is not None:
            self.makeCurrent()
            self._tracer.set_environment(array, enabled)
            self.doneCurrent()
        self.update()

    def set_render_params(self, **kwargs) -> None:
        """Forward exposure/max_bounces/env_intensity/ambient to the tracer."""
        if self._tracer is not None:
            self._tracer.set_params(**kwargs)
            self.update()

    @property
    def render_sample_count(self) -> int:
        return self._tracer.sample_count if self._tracer else 0

    @property
    def render_max_samples(self) -> int:
        return self._tracer.max_samples if self._tracer else 0

    @property
    def render_converged(self) -> bool:
        return bool(self._tracer.converged) if self._tracer else False

    def _physical_size(self) -> tuple[int, int]:
        ratio = self.devicePixelRatioF()
        return max(1, int(self.width() * ratio)), max(1, int(self.height() * ratio))

    # -- rendering --------------------------------------------------------
    def paintGL(self) -> None:
        # Capture the widget's default framebuffer BEFORE binding any other FBO
        # (accumulate() binds the tracer's FBO, which would otherwise be detected).
        screen = self.ctx.detect_framebuffer()

        if self.render_mode and self._tracer is not None and self._tracer.ready:
            pw, ph = self._physical_size()
            # Advance the view-independent irradiance cache.
            self._tracer.update_gi(4)
            # While the cache is still converging it changes each frame, so show a
            # single live sample; once stable, accumulate AA/specular.
            if not self._tracer.gi_converged:
                self._tracer.reset_image()
            self._tracer.accumulate(self.camera, pw, ph)
            self._tracer.denoise(pw, ph)
            screen.use()
            self.ctx.viewport = (0, 0, pw, ph)
            self.ctx.clear(0.0, 0.0, 0.0, 1.0)
            self._tracer.display(pw, ph)
            if not self._tracer.converged:
                self.update()   # keep converging, then idle
            return

        # Restore depth/cull state — the tracer's fullscreen pass disables depth
        # testing, so re-establish it every raster frame.
        screen.use()
        self.ctx.enable(moderngl.DEPTH_TEST | moderngl.CULL_FACE)
        self.ctx.cull_face = "back"
        self.ctx.clear(0.12, 0.12, 0.14, 1.0, depth=1.0)
        self._ensure_uploaded()
        if self._instance_count == 0 or self._main_vao is None:
            return
        mvp = self._mvp()
        self._main_prog["u_mvp"].write(mvp.T.astype("f4").tobytes())
        self._main_prog["u_light_dir"].value = (0.4, 0.6, 0.8)
        self._main_vao.render(instances=self._instance_count)

    def resizeGL(self, w: int, h: int) -> None:
        self._pick_fbo = None  # force pick fbo recreation at new size

    def _mvp(self) -> np.ndarray:
        aspect = self.width() / max(1, self.height())
        return self.camera.view_proj(aspect)

    # -- picking ----------------------------------------------------------
    def _ensure_pick_fbo(self) -> None:
        # Pick buffer is sized in *physical* pixels to match the cursor mapping.
        ratio = self.devicePixelRatioF()
        w = max(1, int(self.width() * ratio))
        h = max(1, int(self.height() * ratio))
        if self._pick_fbo is None or self._pick_fbo.size != (w, h):
            color = self.ctx.texture((w, h), 4)
            depth = self.ctx.depth_renderbuffer((w, h))
            self._pick_fbo = self.ctx.framebuffer(color_attachments=[color],
                                                  depth_attachment=depth)

    def pick(self, x: int, y: int):
        """Return the voxel coord under widget pixel (x, y) [logical], or None."""
        if self.ctx is None or self._instance_count == 0:
            return None
        self.makeCurrent()
        self._ensure_uploaded()
        if self._pick_vao is None:
            self.doneCurrent()
            return None
        self._ensure_pick_fbo()
        ratio = self.devicePixelRatioF()
        px = int(x * ratio)
        py = int(y * ratio)
        self._pick_fbo.use()
        self.ctx.clear(0.0, 0.0, 0.0, 1.0, depth=1.0)
        mvp = self._mvp()
        self._pick_prog["u_mvp"].write(mvp.T.astype("f4").tobytes())
        self._pick_vao.render(instances=self._instance_count)
        # framebuffer origin is bottom-left; widget y is top-down
        fy = self._pick_fbo.size[1] - 1 - py
        fx = min(max(px, 0), self._pick_fbo.size[0] - 1)
        fy = min(max(fy, 0), self._pick_fbo.size[1] - 1)
        data = self._pick_fbo.read(viewport=(fx, fy, 1, 1), components=3)
        self.doneCurrent()
        rid = decode_rgb_to_id((data[0], data[1], data[2]))
        if rid == 0:
            return None
        return mesh.linear_id_to_coord(rid - 1, self.document.dims)

    # -- input ------------------------------------------------------------
    def mousePressEvent(self, e: QMouseEvent) -> None:
        self._last_pos = e.position()
        self._press_pos = e.position()
        self._dragged = False

    def mouseMoveEvent(self, e: QMouseEvent) -> None:
        if self._last_pos is None:
            return
        dx = e.position().x() - self._last_pos.x()
        dy = e.position().y() - self._last_pos.y()
        if abs(e.position().x() - self._press_pos.x()) > 3 or \
           abs(e.position().y() - self._press_pos.y()) > 3:
            self._dragged = True
        buttons = e.buttons()
        orbit_btn = Qt.RightButton if self.swap_camera_buttons else Qt.LeftButton
        no_select_mod = not (e.modifiers() & (Qt.ShiftModifier | Qt.ControlModifier))
        if buttons & orbit_btn and no_select_mod:
            self.camera.orbit(-dx * 0.01, dy * 0.01)
            self._camera_moved()
        elif buttons & Qt.MiddleButton:
            self.camera.pan(dx, dy)
            self._camera_moved()
        self._last_pos = e.position()

    def mouseReleaseEvent(self, e: QMouseEvent) -> None:
        # Left click (no drag) picks/selects — disabled in render mode (view-only).
        if (e.button() == Qt.LeftButton and not self._dragged
                and not self.render_mode):
            coord = self.pick(int(e.position().x()), int(e.position().y()))
            self.voxel_picked.emit(coord, int(e.modifiers().value))
        self._last_pos = None

    def wheelEvent(self, e: QWheelEvent) -> None:
        self.camera.zoom(e.angleDelta().y() / 120.0)
        self._camera_moved()

    def _camera_moved(self) -> None:
        # The irradiance cache is view-independent — keep it, only reset the
        # per-pixel image accumulation.
        if self.render_mode and self._tracer is not None:
            self._tracer.reset_image()
        self.update()
