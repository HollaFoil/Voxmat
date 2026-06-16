"""Headless renderer tests: voxel GI cache spreads light, glass is see-through,
tone mapping doesn't crush, denoise reduces noise. Offscreen GL context.

Run: python tests/test_render.py
"""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _ctx():
    from PySide6.QtGui import QOffscreenSurface, QOpenGLContext, QSurfaceFormat
    from PySide6.QtWidgets import QApplication
    import moderngl
    app = QApplication.instance() or QApplication(sys.argv)
    fmt = QSurfaceFormat(); fmt.setVersion(3, 3); fmt.setProfile(QSurfaceFormat.CoreProfile)
    QSurfaceFormat.setDefaultFormat(fmt)
    surf = QOffscreenSurface(); surf.setFormat(fmt); surf.create()
    gl = QOpenGLContext(); gl.setFormat(fmt); gl.create(); gl.makeCurrent(surf)
    return app, surf, gl, moderngl.create_context()


def _render(tr, ctx, cam, w=96, h=96, gi=250, spp=40, denoise=False):
    out = ctx.texture((w, h), 4, dtype="f1")
    fbo = ctx.framebuffer(color_attachments=[out])
    tr.reset()
    tr.denoise_enabled = denoise
    for _ in range(gi):
        tr.update_gi(1)
    for _ in range(spp):
        tr.accumulate(cam, w, h)
    tr.denoise(w, h)
    fbo.use(); ctx.clear(0, 0, 0, 1); tr.display(w, h)
    data = np.frombuffer(fbo.read(components=4), dtype=np.uint8).reshape(h, w, 4)
    return data[..., :3].astype(np.float32)


def run():
    from voxmat.core.grid import VoxelGrid
    from voxmat.core.material import MaterialLibrary
    from voxmat.render import PathTracer
    from voxmat.ui.viewport.camera import OrbitCamera

    app, surf, gl, ctx = _ctx()
    tr = PathTracer(ctx)

    # -- (a) directional GI: a face toward a nearby emitter is strongly lit,
    #        a face pointing away is not (per-face irradiance cache) ----------
    m = 12
    rgba = np.zeros((m, m, m, 4), dtype=np.uint8)
    rgba[3, 5, 5] = (255, 255, 255, 255)         # diffuse probe
    rgba[7, 5, 5] = (255, 255, 255, 255)         # emitter, gap on the probe's +X
    grid = VoxelGrid(rgba)
    lib = MaterialLibrary()
    lamp = lib.new("lamp")
    lib.update(replace(lib.get(lamp.id), emission_color=(1, 1, 1), emission_strength=10.0))
    grid.material_id[7, 5, 5] = lamp.id
    tr.upload_volume(grid); tr.upload_materials(lib)
    tr.set_environment(None, enabled=False); tr.ambient = (0.0, 0.0, 0.0)
    for _ in range(400):
        tr.update_gi(1)
    atlas = tr._atlas[tr._atlas_read]
    fbo = ctx.framebuffer(color_attachments=[atlas])
    A = np.frombuffer(fbo.read(components=4, dtype="f4"), dtype=np.float32).reshape(
        atlas.size[1], atlas.size[0], 4)
    Y, Z = grid.dims[1], grid.dims[2]
    face_I = lambda v, fa: A[v[1] + v[2] * Y + fa * (Y * Z), v[0], :3]
    toward = face_I((3, 5, 5), 0).mean()         # +X face, faces the lamp
    away = face_I((3, 5, 5), 1).mean()           # -X face, away from lamp
    assert toward > 0.2 and toward > away * 4 + 0.05, (toward, away)
    print(f"(a) directional GI OK: facing={toward:.3f} away={away:.3f}")

    # -- (b) glass is see-through: a multi-voxel glass slab sits BETWEEN the
    #        camera and a red wall, so every central ray must refract through the
    #        slab to reach the wall ----------------------------------------------
    D = 10
    rgba = np.zeros((D, D, D, 4), dtype=np.uint8)
    rgba[:, D - 1, :] = (230, 40, 40, 255)        # red wall on the +y side
    rgba[2:8, 4:7, 2:8] = (220, 235, 255, 255)    # 3-voxel-thick glass slab in front
    grid = VoxelGrid(rgba)
    lib = MaterialLibrary()
    glass = lib.new("glass")
    lib.update(replace(lib.get(glass.id), transmission=1.0, ior=1.45, roughness=0.05))
    gi, gj, gk = np.where((rgba[..., :3] == (220, 235, 255)).all(-1))
    grid.material_id[gi, gj, gk] = glass.id
    tr.upload_volume(grid); tr.upload_materials(lib)
    tr.set_environment(None, enabled=True)
    # eye on the -y side looking toward +y: ray = camera -> glass slab -> red wall.
    cam = OrbitCamera(); cam.frame_dims(grid.dims)
    cam.azimuth = np.radians(-90); cam.elevation = np.radians(8)
    patch = lambda im: im[im.shape[0] // 2 - 20:im.shape[0] // 2 + 20,
                          im.shape[1] // 2 - 20:im.shape[1] // 2 + 20]
    rgb = patch(_render(tr, ctx, cam, gi=200, spp=60)).reshape(-1, 3).mean(0)
    # The red wall must be visible THROUGH the slab: red dominant over green AND
    # blue. Red > blue specifically rules out the old bug where the slab showed a
    # (bluish) mirror of the sky instead of refracting the scene behind it.
    assert rgb.max() > 40, rgb
    assert rgb[0] > rgb[1] + 8 and rgb[0] > rgb[2] + 8, rgb
    # Make the same slab opaque: it occludes the wall and reads as a near-neutral
    # lit surface, whereas through glass the wall's red shows clearly. Compare red
    # *saturation* (not magnitude — a white slab is also bright in red).
    lib.update(replace(lib.get(glass.id), transmission=0.0))
    tr.upload_materials(lib)
    opaque = patch(_render(tr, ctx, cam, gi=200, spp=60)).reshape(-1, 3).mean(0)
    redness = lambda v: v[0] - 0.5 * (v[1] + v[2])
    assert redness(rgb) > redness(opaque) + 15, (rgb, opaque)
    print(f"(b) glass see-through OK: through-glass RGB={rgb.round(1)} opaque RGB={opaque.round(1)}")

    # -- (c) tone map lifts shadows (filmic vs ACES) ---------------------
    # a uniformly DIM cube (ambient only): filmic must read brighter than ACES
    # for these low values (ACES has a harder toe -> crushed blacks).
    rgba = np.zeros((4, 4, 4, 4), dtype=np.uint8); rgba[..., :] = (180, 180, 180, 255)
    grid = VoxelGrid(rgba)
    lib = MaterialLibrary()
    tr.upload_volume(grid); tr.upload_materials(lib)
    tr.set_environment(None, enabled=False); tr.ambient = (0.08, 0.08, 0.08)
    cam = OrbitCamera(); cam.frame_dims(grid.dims)
    tr.tonemap = 0; film = _render(tr, ctx, cam, gi=150, spp=20)
    tr.tonemap = 1; aces = _render(tr, ctx, cam, gi=150, spp=20)
    model = lambda im: im.reshape(-1, 3).mean(1)
    fdark = model(film)[model(film) > 1].mean()
    adark = model(aces)[model(aces) > 1].mean()
    assert fdark > adark, (fdark, adark)
    print(f"(c) tone map OK: filmic dim={fdark:.1f} > aces dim={adark:.1f}")

    # -- (d) denoise reduces noise of a glossy surface (specular is noisy) --
    rgba = np.zeros((6, 6, 6, 4), dtype=np.uint8); rgba[1:5, 1:5, 1:5] = (200, 200, 200, 255)
    grid = VoxelGrid(rgba)
    lib = MaterialLibrary()
    g = lib.new("glossy")
    lib.update(replace(lib.get(g.id), use_voxel_color=True, metallic=1.0, roughness=0.45))
    gi_, gj_, gk_ = np.where((rgba[..., :3] == (200, 200, 200)).all(-1))
    grid.material_id[gi_, gj_, gk_] = g.id
    tr.upload_volume(grid); tr.upload_materials(lib)
    tr.set_environment(None, enabled=True)
    cam = OrbitCamera(); cam.frame_dims(grid.dims); cam.azimuth = np.radians(35); cam.elevation = np.radians(25)
    noisy = _render(tr, ctx, cam, gi=60, spp=2, denoise=False)
    clean = _render(tr, ctx, cam, gi=60, spp=2, denoise=True)
    # std over a patch on the model (exclude flat sky)
    def patch_std(im):
        c = im[im.shape[0]//2-12:im.shape[0]//2+12, im.shape[1]//2-12:im.shape[1]//2+12]
        return c.reshape(-1, 3).std()
    assert patch_std(clean) < patch_std(noisy), (patch_std(noisy), patch_std(clean))
    print(f"(d) denoise OK: std noisy={patch_std(noisy):.2f} -> clean={patch_std(clean):.2f}")

    # -- (e) metallic surfaces reflect (not black) -----------------------
    # A mirror-metal block under the sky must show the reflected environment on
    # its faces, not pure black (regression: voxelOutgoing dropped metal light).
    rgba = np.zeros((5, 5, 5, 4), dtype=np.uint8); rgba[1:4, 1:4, 1:4] = (200, 200, 210, 255)
    grid = VoxelGrid(rgba)
    lib = MaterialLibrary()
    met = lib.new("mirror")
    lib.update(replace(lib.get(met.id), use_voxel_color=True, metallic=1.0, roughness=0.05))
    mi, mj, mk = np.where((rgba[..., :3] == (200, 200, 210)).all(-1))
    grid.material_id[mi, mj, mk] = met.id
    tr.upload_volume(grid); tr.upload_materials(lib)
    tr.set_environment(None, enabled=True)
    cam = OrbitCamera(); cam.frame_dims(grid.dims)
    cam.azimuth = np.radians(35); cam.elevation = np.radians(25)
    img = _render(tr, ctx, cam, gi=120, spp=40)
    lum = img.reshape(-1, 3).mean(1)
    model = lum[lum < 180]                     # exclude bright sky background
    black_frac = float((model < 6).mean()) if model.size else 1.0
    assert black_frac < 0.25, black_frac       # metal must not be mostly black
    print(f"(e) metallic reflects OK: black fraction on model={black_frac:.2f}")

    # -- (f) glass actually REFRACTS (bends) light: changing the IOR must change
    #        the through-glass image of a structured background. A flat
    #        passthrough (no bending) would be identical for any IOR. ------------
    D = 12
    rgba = np.zeros((D, D, D, 4), dtype=np.uint8)
    for z in range(D):                              # striped back wall (bands in z)
        rgba[:, D - 1, z] = (235, 235, 235, 255) if (z // 2) % 2 == 0 else (12, 12, 12, 255)
    rgba[2:10, 4:7, 2:10] = (220, 235, 255, 255)    # 3-voxel-thick glass slab
    grid = VoxelGrid(rgba)
    lib = MaterialLibrary()
    glass = lib.new("glass")
    gi, gj, gk = np.where((rgba[..., :3] == (220, 235, 255)).all(-1))
    grid.material_id[gi, gj, gk] = glass.id
    tr.upload_volume(grid)
    tr.set_environment(None, enabled=False)         # ambient only — isolate refraction
    tr.ambient = (0.6, 0.6, 0.6)
    cam = OrbitCamera(); cam.frame_dims(grid.dims)
    cam.azimuth = np.radians(-90); cam.elevation = np.radians(35)   # oblique -> bends in z
    patch_f = lambda im: im[im.shape[0] // 2 - 16:im.shape[0] // 2 + 16,
                            im.shape[1] // 2 - 16:im.shape[1] // 2 + 16]

    def render_ior(value):
        lib.update(replace(lib.get(glass.id), transmission=1.0, ior=value, roughness=0.02))
        tr.upload_materials(lib)
        return patch_f(_render(tr, ctx, cam, gi=120, spp=30))

    straight, straight2 = render_ior(1.0), render_ior(1.0)   # IOR 1 = no bending
    bent = render_ior(1.6)                                    # IOR 1.6 = visible bending
    control = float(np.abs(straight - straight2).mean())     # baseline render noise
    shift = float(np.abs(bent - straight).mean())
    # Bending the rays must change the refracted image far more than baseline noise.
    assert shift > 5.0 and shift > control * 10.0, (shift, control)
    print(f"(f) refraction bends OK: |IOR1.6 - IOR1.0|={shift:.1f} (noise={control:.3f})")

    # -- (g) environment vertical orientation: a sky-bright / ground-dark map must
    #        light an UP-facing voxel face more than a DOWN-facing one. Guards the
    #        equirectangular V mapping (regression: env was vertically inverted, so
    #        the ground lit from above and the sky from below). The GI gather reads
    #        the env directly, so this exercises the real lighting path. ----------
    rgba = np.zeros((3, 3, 3, 4), dtype=np.uint8)
    rgba[1, 1, 1] = (255, 255, 255, 255)            # one diffuse voxel
    grid = VoxelGrid(rgba)
    lib = MaterialLibrary()
    H, W = 64, 128
    env = np.zeros((H, W, 3), dtype=np.float32)
    env[:H // 2] = (3.0, 3.0, 3.0)                  # top half (sky) bright
    env[H // 2:] = (0.0, 0.0, 0.0)                  # bottom half (ground) dark
    tr.upload_volume(grid); tr.upload_materials(lib)
    tr.set_environment(env, enabled=True)
    tr.env_flip_v = False; tr.env_flip_h = False
    for _ in range(400):
        tr.update_gi(1)
    atlas = tr._atlas[tr._atlas_read]
    fbo = ctx.framebuffer(color_attachments=[atlas])
    A = np.frombuffer(fbo.read(components=4, dtype="f4"), dtype=np.float32).reshape(
        atlas.size[1], atlas.size[0], 4)
    Y, Z = grid.dims[1], grid.dims[2]
    face_I = lambda v, fa: A[v[1] + v[2] * Y + fa * (Y * Z), v[0], :3]
    up_face = face_I((1, 1, 1), 4).mean()           # +Z face -> samples the sky
    down_face = face_I((1, 1, 1), 5).mean()         # -Z face -> samples the ground
    assert up_face > down_face * 2 + 0.05, (up_face, down_face)
    print(f"(g) env orientation OK: up-face={up_face:.3f} > down-face={down_face:.3f}")

    print("render OK")


if __name__ == "__main__":
    run()
