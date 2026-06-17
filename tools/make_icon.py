"""Generate the Voxmat app icon programmatically (Pillow, deterministic).

A corner-on isometric cube drawn as three rhombus faces with a small
negative-space gap between them (no outlines), on a transparent background.

    python tools/make_icon.py            # list schemes
    python tools/make_icon.py blue       # write assets/icon.{png,ico,icns}
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

from PIL import Image, ImageDraw

_ROOT = Path(__file__).resolve().parents[1]

# (top, right, left) face colours. All-same schemes rely purely on the gaps to
# read as a cube; shaded schemes also use light/medium/dark for depth.
SCHEMES: dict[str, tuple] = {
    "white": ((245, 245, 245),) * 3,
    "black": ((20, 20, 20),) * 3,
    "grey":  ((236, 236, 236), (180, 180, 180), (132, 132, 132)),
    "blue":  ((120, 194, 250), (78, 150, 216), (50, 108, 172)),
    "rgb":   ((235, 85, 85), (95, 200, 115), (95, 135, 235)),
    "amber": ((250, 212, 120), (226, 166, 72), (181, 120, 46)),
}

GAP = 0.03   # inset distance per face as a fraction of the cube radius; the gap
             # between two faces is ~2x this. A fixed distance (not a ratio) keeps
             # the gaps equal between large outer faces and small inner ones.


def _faces(cx: float, cy: float, r: float):
    """The three visible rhombi of a corner-on isometric cube."""
    def v(deg):
        a = math.radians(deg)
        return (cx + math.cos(a) * r, cy - math.sin(a) * r)
    c = (cx, cy)
    top = [v(90), v(30), c, v(150)]
    right = [v(30), v(330), v(270), c]
    left = [v(150), c, v(270), v(210)]
    return top, right, left


def _line_intersect(p1, d1, p2, d2):
    denom = d1[0] * d2[1] - d1[1] * d2[0]
    if abs(denom) < 1e-9:
        return p1
    dx, dy = p2[0] - p1[0], p2[1] - p1[1]
    t = (dx * d2[1] - dy * d2[0]) / denom
    return (p1[0] + t * d1[0], p1[1] + t * d1[1])


def _inset(poly, d, keep=None):
    """Inset a simple polygon inward by a fixed distance ``d`` (uniform gap), by
    offsetting each edge along its inward normal and intersecting neighbours.

    'Inward' is taken from the polygon's winding (signed area), not a centroid
    point — for an L-shape the vertex centroid can fall on the reflex corner and
    give the wrong direction, collapsing the gap on those pieces.

    ``keep`` is an optional point: edges with an endpoint there are NOT inset, so
    faces meeting at that point stay flush (used to fuse the inner cube faces)."""
    n = len(poly)
    area = sum(poly[i][0] * poly[(i + 1) % n][1] - poly[(i + 1) % n][0] * poly[i][1]
               for i in range(n))
    ccw = area > 0
    edges = []                                   # (offset point, unit dir) per edge
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        ex, ey = x2 - x1, y2 - y1
        length = math.hypot(ex, ey) or 1.0
        ux, uy = ex / length, ey / length
        nx, ny = (-uy, ux) if ccw else (uy, -ux)  # inward normal for this winding
        di = d
        if keep is not None and (math.hypot(x1 - keep[0], y1 - keep[1]) < 1e-3
                                 or math.hypot(x2 - keep[0], y2 - keep[1]) < 1e-3):
            di = 0.0                               # shared edge — leave it flush
        edges.append(((x1 + nx * di, y1 + ny * di), (ux, uy)))
    return [_line_intersect(*edges[i - 1], *edges[i]) for i in range(n)]


def _darken(c, f=0.9):
    return tuple(int(round(v * f)) for v in c)


def _notched_faces(cx, cy, r, top_c, right_c, left_c, t=0.5):
    """A 2x2x2 cube with the near octant removed: the three outer faces become
    L-shapes and three darker inner faces line the notch (meeting at the centre,
    since the cube's edge vectors sum to zero in this projection)."""
    # The three cube edges from the near corner, as screen vectors (length r).
    A = (math.cos(math.radians(30)) * r, -math.sin(math.radians(30)) * r)   # up-right
    B = (math.cos(math.radians(150)) * r, -math.sin(math.radians(150)) * r)  # up-left
    D = (0.0, r)                                                             # down

    def p(a, b, d):
        return (cx + a * A[0] + b * B[0] + d * D[0],
                cy + a * A[1] + b * B[1] + d * D[1])

    return [
        # darker inner walls of the notch first (is_inner=True)
        ([p(0, 0, t), p(t, 0, t), p(t, t, t), p(0, t, t)], _darken(top_c), True),
        ([p(0, t, 0), p(t, t, 0), p(t, t, t), p(0, t, t)], _darken(right_c), True),
        ([p(t, 0, 0), p(t, t, 0), p(t, t, t), p(t, 0, t)], _darken(left_c), True),
        # outer L-shaped faces on top
        ([p(t, 0, 0), p(1, 0, 0), p(1, 1, 0), p(0, 1, 0), p(0, t, 0), p(t, t, 0)], top_c, False),
        ([p(t, 0, 0), p(1, 0, 0), p(1, 0, 1), p(0, 0, 1), p(0, 0, t), p(t, 0, t)], right_c, False),
        ([p(0, t, 0), p(0, 1, 0), p(0, 1, 1), p(0, 0, 1), p(0, 0, t), p(0, t, t)], left_c, False),
    ]


def render(scheme: str, size: int = 256, supersample: int = 4,
           notch: bool = False, solid_inner: bool = False) -> Image.Image:
    """`notch`: scoop out the near octant. `solid_inner`: when notched, fuse the
    three inner faces (no gaps between them) so the recess reads as one block."""
    top_c, right_c, left_c = SCHEMES[scheme]
    s = size * supersample
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    cx = cy = s / 2
    r = s * 0.43
    if notch:
        faces = _notched_faces(cx, cy, r, top_c, right_c, left_c)
    else:
        top, right, left = _faces(cx, cy, r)
        faces = [(top, top_c, False), (right, right_c, False), (left, left_c, False)]
    d = r * GAP
    for poly, col, is_inner in faces:
        keep = (cx, cy) if (solid_inner and is_inner) else None
        draw.polygon(_inset(poly, d, keep=keep), fill=tuple(col) + (255,))
    return img.resize((size, size), Image.LANCZOS)


def write_icons(scheme: str, notch: bool = False, solid_inner: bool = False) -> None:
    icon = render(scheme, notch=notch, solid_inner=solid_inner)
    assets = _ROOT / "assets"
    icon.save(assets / "icon.png")
    icon.save(assets / "icon.ico",
              sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    try:
        icon.save(assets / "icon.icns")
    except Exception:
        pass   # .icns optional; PyInstaller falls back to the default on macOS
    print(f"wrote assets/icon.png/.ico/.icns from scheme '{scheme}'")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] in SCHEMES:
        flags = sys.argv[2:]
        write_icons(sys.argv[1], notch="notch" in flags, solid_inner="solid" in flags)
    else:
        print("schemes:", ", ".join(SCHEMES))
        print("usage: python tools/make_icon.py <scheme> [notch] [solid]")
