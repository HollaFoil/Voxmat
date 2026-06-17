"""Progressive voxel renderer with a per-voxel irradiance cache (GLSL 330).

Instead of a deep per-pixel path tracer, this uses *progressive voxel radiosity*:

* An **irradiance cache** (a 2D atlas, one texel per voxel face) stores each
  voxel's temporally-averaged incoming light. Every frame a cheap "GI update"
  pass shoots one random ray per voxel face, reads the hit voxel's outgoing
  radiance from the previous cache (one bounce), and blends it into a running
  average. Light therefore propagates one voxel-bounce per frame and converges to
  full multi-bounce GI — with no special handling for emitters and effectively
  unlimited lights. The cache is *view-independent*, so orbiting never resets it.
* The **camera pass** is then shallow and mostly noise-free: on the primary hit
  it reads diffuse GI straight from the cache, adds a multi-bounce GGX specular
  path, and handles glass via dielectric refraction through homogeneous blocks.

This module is a pure ModernGL backend with no Qt/UI dependency. The GLSL source
lives in :mod:`voxmat.render.shaders` as standalone files. The owning widget
calls :meth:`update_gi` (while the cache converges), :meth:`accumulate` (camera
image) and :meth:`display` each repaint.
"""

from __future__ import annotations

import numpy as np

from ..core.grid import VoxelGrid
from ..core.material import MaterialLibrary
from .shaders import load_shader

MAT_TEXELS = 4   # RGBA32F texels stored per material (see _materials_array)


class PathTracer:
    def __init__(self, ctx):
        self.ctx = ctx
        vert = load_shader("quad.vert")
        self.gi_prog = ctx.program(vertex_shader=vert, fragment_shader=load_shader("gi_update.frag"))
        self.trace_prog = ctx.program(vertex_shader=vert, fragment_shader=load_shader("camera.frag"))
        self.display_prog = ctx.program(vertex_shader=vert, fragment_shader=load_shader("display.frag"))
        self.atrous_prog = ctx.program(vertex_shader=vert, fragment_shader=load_shader("atrous.frag"))
        quad = np.array([-1, -1, 3, -1, -1, 3], dtype="f4")
        self._quad = ctx.buffer(quad.tobytes())
        self._gi_vao = ctx.vertex_array(self.gi_prog, [(self._quad, "2f", "in_pos")])
        self._trace_vao = ctx.vertex_array(self.trace_prog, [(self._quad, "2f", "in_pos")])
        self._display_vao = ctx.vertex_array(self.display_prog, [(self._quad, "2f", "in_pos")])
        self._atrous_vao = ctx.vertex_array(self.atrous_prog, [(self._quad, "2f", "in_pos")])

        # image accumulation (ping-pong) + G-buffers + denoise temps
        self._size = (0, 0)
        self._tex = [None, None]
        self._fbo = [None, None]
        self._read = 0
        self.sample_count = 0
        self._gbuf_albedo = None
        self._gbuf_normal = None
        self._dn_tex = [None, None]
        self._dn_fbo = [None, None]
        self._dn_read = 0
        self._dn_valid = False

        # irradiance cache atlas (ping-pong)
        self._atlas = [None, None]
        self._atlas_fbo = [None, None]
        self._atlas_read = 0
        self._atlas_size = (0, 0)
        self.gi_frame = 0

        self.dims = (0, 0, 0)
        self._vol_color = None
        self._vol_mat = None
        self._materials = None
        self._env = None

        # params
        self.exposure = 1.0
        self.tonemap = 0
        self.gi_scale = 1.0          # physically ~correct with the directional cache
        self.glass_density = 0.6
        self.max_bounces = 4          # specular reflection bounces in the camera pass
        self.max_trans_depth = 32
        self.env_intensity = 1.0
        self.env_enabled = True
        self.env_is_sky = True
        self.env_flip_h = False
        self.env_flip_v = False
        self.ambient = (0.35, 0.37, 0.4)
        self.max_samples = 256
        self.gi_max = 600
        self.denoise_enabled = False   # off by default: the À-Trous guide blurs
        self.denoise_iters = 5         # glossy/refractive detail (see RenderPanel)

    # -- uploads ----------------------------------------------------------
    def upload_volume(self, grid: VoxelGrid) -> None:
        new_dims = grid.dims
        self.dims = new_dims
        x, y, z = new_dims
        color = np.ascontiguousarray(np.transpose(grid.rgba, (2, 1, 0, 3)))
        mat = np.ascontiguousarray(np.transpose(grid.material_id, (2, 1, 0)))
        if self._vol_color is not None:
            self._vol_color.release(); self._vol_mat.release()
        self._vol_color = self.ctx.texture3d((x, y, z), 4, color.tobytes(), dtype="f1")
        self._vol_color.filter = (self.ctx.NEAREST, self.ctx.NEAREST)
        self._vol_mat = self.ctx.texture3d((x, y, z), 1, mat.tobytes(), dtype="u2")
        self._vol_mat.filter = (self.ctx.NEAREST, self.ctx.NEAREST)
        self._ensure_atlas(x, y * z * 6)   # 6 faces per voxel (directional cache)
        self.reset()

    def upload_materials(self, library: MaterialLibrary) -> None:
        arr = self._materials_array(library)
        if self._materials is not None:
            self._materials.release()
        self._materials = self.ctx.texture((MAT_TEXELS, arr.shape[0]), 4,
                                           np.ascontiguousarray(arr).tobytes(), dtype="f4")
        self._materials.filter = (self.ctx.NEAREST, self.ctx.NEAREST)
        self.reset()

    @staticmethod
    def _materials_array(library: MaterialLibrary) -> np.ndarray:
        max_id = max((m.id for m in library), default=0)
        arr = np.zeros((max_id + 1, MAT_TEXELS, 4), dtype="f4")
        for m in library:
            arr[m.id, 0] = (m.albedo[0], m.albedo[1], m.albedo[2],
                            1.0 if m.use_voxel_color else 0.0)
            arr[m.id, 1] = (m.metallic, m.roughness, m.transmission, m.ior)
            arr[m.id, 2] = (m.emission_color[0], m.emission_color[1],
                            m.emission_color[2], m.emission_strength)
            arr[m.id, 3] = (1.0 if m.emission_use_voxel_color else 0.0,
                            float(m.flags), 0.0, 0.0)
        return arr

    def set_environment(self, arr: np.ndarray | None, enabled: bool) -> None:
        self.env_enabled = enabled
        self.env_is_sky = arr is None
        if arr is not None:
            h, w = arr.shape[0], arr.shape[1]
            data = np.ascontiguousarray(arr[..., :3], dtype="f4")
            if self._env is not None:
                self._env.release()
            self._env = self.ctx.texture((w, h), 3, data.tobytes(), dtype="f4")
            self._env.filter = (self.ctx.LINEAR, self.ctx.LINEAR)
            self._env.repeat_x = True
            self._env.repeat_y = False
        self.reset()

    # -- params / reset ---------------------------------------------------
    def set_params(self, *, exposure=None, tonemap=None, gi_scale=None,
                   env_intensity=None, ambient=None, env_flip_h=None,
                   env_flip_v=None, glass_density=None, max_bounces=None,
                   denoise_enabled=None, denoise_iters=None) -> None:
        # display-only (no reset at all): exposure, tonemap, denoise
        if exposure is not None:
            self.exposure = exposure
        if tonemap is not None:
            self.tonemap = tonemap
        if denoise_enabled is not None:
            self.denoise_enabled = denoise_enabled
        if denoise_iters is not None:
            self.denoise_iters = denoise_iters
        # Params read only by the camera pass: changing them invalidates the
        # accumulated image but NOT the (view-independent) irradiance cache, so
        # the converged GI survives — only the per-pixel image restarts.
        camera_only = (("gi_scale", gi_scale), ("glass_density", glass_density),
                       ("max_bounces", max_bounces))
        # Params that feed the GI gather (env lighting): the cache must reconverge.
        cache_affecting = (("env_intensity", env_intensity), ("ambient", ambient),
                           ("env_flip_h", env_flip_h), ("env_flip_v", env_flip_v))
        image_dirty = cache_dirty = False
        for name, val in camera_only:
            if val is not None and getattr(self, name) != val:
                setattr(self, name, val); image_dirty = True
        for name, val in cache_affecting:
            if val is not None and getattr(self, name) != val:
                setattr(self, name, val); cache_dirty = True
        if cache_dirty:
            self.reset()
        elif image_dirty:
            self.reset_image()

    def reset_image(self) -> None:
        self.sample_count = 0

    def reset_cache(self) -> None:
        self.gi_frame = 0

    def reset(self) -> None:
        self.reset_image(); self.reset_cache()

    @property
    def converged(self) -> bool:
        return self.sample_count >= self.max_samples and self.gi_frame >= self.gi_max

    @property
    def gi_converged(self) -> bool:
        return self.gi_frame >= self.gi_max

    @property
    def ready(self) -> bool:
        return self._vol_color is not None and self._materials is not None

    # -- targets ----------------------------------------------------------
    def _ensure_atlas(self, w: int, h: int) -> None:
        if self._atlas_size == (w, h) and self._atlas[0] is not None:
            return
        for t in self._atlas:
            if t is not None:
                t.release()
        for f in self._atlas_fbo:
            if f is not None:
                f.release()
        self._atlas = [self.ctx.texture((w, h), 4, dtype="f4") for _ in range(2)]
        for t in self._atlas:
            t.filter = (self.ctx.NEAREST, self.ctx.NEAREST)
        self._atlas_fbo = [self.ctx.framebuffer(color_attachments=[t]) for t in self._atlas]
        self._atlas_size = (w, h)
        self._atlas_read = 0
        self.reset_cache()

    def _ensure_targets(self, w: int, h: int) -> None:
        if self._size == (w, h) and self._tex[0] is not None:
            return
        old = (self._tex + self._fbo + self._dn_tex + self._dn_fbo
               + [self._gbuf_albedo, self._gbuf_normal])
        for o in old:
            if o is not None:
                o.release()
        mk = lambda: self.ctx.texture((w, h), 4, dtype="f4")
        self._tex = [mk(), mk()]
        self._gbuf_albedo = mk()
        self._gbuf_normal = mk()
        self._dn_tex = [mk(), mk()]
        for t in self._tex + self._dn_tex + [self._gbuf_albedo, self._gbuf_normal]:
            t.filter = (self.ctx.NEAREST, self.ctx.NEAREST)
        # camera pass writes colour + the two G-buffers (shared across ping-pong)
        self._fbo = [self.ctx.framebuffer(
            color_attachments=[t, self._gbuf_albedo, self._gbuf_normal]) for t in self._tex]
        self._dn_fbo = [self.ctx.framebuffer(color_attachments=[t]) for t in self._dn_tex]
        self._size = (w, h)
        self.reset_image()

    # -- common uniforms --------------------------------------------------
    @staticmethod
    def _u(prog, name, value) -> None:
        """Set a uniform if the program actually uses it (GLSL may drop unused)."""
        m = prog.get(name, None)
        if m is not None:
            m.value = value

    def _bind_scene(self, prog) -> None:
        """Bind the volume/material/env/cache textures and shared uniforms."""
        self._vol_color.use(location=1); self._u(prog, "u_vol_color", 1)
        self._vol_mat.use(location=2); self._u(prog, "u_vol_mat", 2)
        self._materials.use(location=3); self._u(prog, "u_materials", 3)
        if self._env is not None:
            self._env.use(location=4); self._u(prog, "u_env", 4)
        self._atlas[self._atlas_read].use(location=5); self._u(prog, "u_gi", 5)
        self._u(prog, "u_dims", (int(self.dims[0]), int(self.dims[1]), int(self.dims[2])))
        self._u(prog, "u_max_steps", int(sum(self.dims) + 3))
        self._u(prog, "u_max_trans_depth", int(self.max_trans_depth))
        self._u(prog, "u_gi_scale", float(self.gi_scale))
        self._u(prog, "u_glass_density", float(self.glass_density))
        env_mode = 0 if not self.env_enabled else (2 if (self.env_is_sky or self._env is None) else 1)
        self._u(prog, "u_env_mode", env_mode)
        self._u(prog, "u_env_intensity", float(self.env_intensity))
        self._u(prog, "u_env_flip_h", bool(self.env_flip_h))
        self._u(prog, "u_env_flip_v", bool(self.env_flip_v))
        self._u(prog, "u_ambient", tuple(float(v) for v in self.ambient))

    # -- render -----------------------------------------------------------
    def update_gi(self, iterations: int = 1) -> None:
        """Advance the irradiance cache by ``iterations`` gather passes."""
        if not self.ready or self.gi_converged:
            return
        w, h = self._atlas_size
        for _ in range(iterations):
            if self.gi_converged:
                break
            p = self.gi_prog
            self._bind_scene(p)
            p["u_gi_frame"].value = self.gi_frame
            write = 1 - self._atlas_read
            self._atlas_fbo[write].use()
            self.ctx.disable(self.ctx.DEPTH_TEST)
            self.ctx.viewport = (0, 0, w, h)
            self._gi_vao.render()
            self._atlas_read = write
            self.gi_frame += 1

    def accumulate(self, camera, w: int, h: int) -> None:
        """Add one camera sample. ``camera`` is any object exposing ``eye``,
        ``target``, ``up`` and ``fovy`` (e.g. the UI's OrbitCamera)."""
        self._ensure_targets(max(w, 1), max(h, 1))
        if not self.ready or self.sample_count >= self.max_samples:
            return
        eye = camera.eye
        forward = camera.target - eye
        forward = forward/(np.linalg.norm(forward)+1e-9)
        right = np.cross(forward, camera.up); right = right/(np.linalg.norm(right)+1e-9)
        up = np.cross(right, forward)

        p = self.trace_prog
        self._bind_scene(p)
        self._tex[self._read].use(location=0); p["u_accum"].value = 0
        p["u_eye"].value = tuple(float(v) for v in eye)
        p["u_forward"].value = tuple(float(v) for v in forward)
        p["u_right"].value = tuple(float(v) for v in right)
        p["u_up"].value = tuple(float(v) for v in up)
        p["u_tan_half_fov"].value = float(np.tan(np.radians(camera.fovy)/2.0))
        p["u_aspect"].value = w/max(h, 1)
        p["u_frame"].value = self.sample_count
        p["u_max_bounces"].value = int(self.max_bounces)
        p["u_resolution"].value = (float(w), float(h))

        write = 1 - self._read
        self._fbo[write].use()
        self.ctx.disable(self.ctx.DEPTH_TEST)
        self.ctx.viewport = (0, 0, w, h)
        self._trace_vao.render()
        self._read = write
        self.sample_count += 1

    def denoise(self, w: int, h: int) -> None:
        """Edge-avoiding À-Trous filter over the averaged image (guided by the
        albedo + normal G-buffers). Sets the denoised texture for display."""
        self._dn_valid = False
        if not self.denoise_enabled or self.sample_count == 0 or self._tex[self._read] is None:
            return
        self.ctx.disable(self.ctx.DEPTH_TEST)
        p = self.atrous_prog
        src = self._tex[self._read]
        is_accum = True
        for i in range(self.denoise_iters):
            step = 1 << i
            dst = i % 2
            src.use(location=0); p["u_in"].value = 0
            self._gbuf_albedo.use(location=1); p["u_albedo"].value = 1
            self._gbuf_normal.use(location=2); p["u_normal"].value = 2
            p["u_size"].value = (w, h)
            p["u_step"].value = step
            p["u_is_accum"].value = is_accum
            p["u_samples"].value = max(self.sample_count, 1)
            # Constant across À-Trous levels: the colour weight is relative to the
            # centre luminance (see atrous.frag), so it is already scale-invariant
            # and does not need to be loosened at coarse steps.
            p["u_c_phi"].value = 1.0
            p["u_n_phi"].value = 64.0
            p["u_a_phi"].value = 0.02
            self._dn_fbo[dst].use()
            self.ctx.viewport = (0, 0, w, h)
            self._atrous_vao.render()
            src = self._dn_tex[dst]
            is_accum = False
            self._dn_read = dst
        self._dn_valid = True

    def display(self, w: int, h: int) -> None:
        self.ctx.viewport = (0, 0, w, h)
        if self._dn_valid and self.denoise_enabled:
            self._dn_tex[self._dn_read].use(location=0)
            samples = 1                      # denoised image is already averaged
        elif self._tex[self._read] is not None:
            self._tex[self._read].use(location=0)
            samples = max(self.sample_count, 1)
        else:
            return
        self.display_prog["u_accum"].value = 0
        self.display_prog["u_samples"].value = samples
        self.display_prog["u_exposure"].value = float(self.exposure)
        self.display_prog["u_tonemap"].value = int(self.tonemap)
        self._display_vao.render()
