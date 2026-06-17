"""Equirectangular environment maps for the path tracer.

Loads HDR/LDR images as float32 ``(H, W, 3)`` radiance and discovers available
maps (the bundled default plus any in the host MagicaVoxel ``ibl`` folder).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from .._resources import resource_root

_ASSET_DIR = resource_root() / "assets" / "env"
# A host MagicaVoxel install (when running from inside one) keeps HDRIs in ../ibl;
# absent in a standalone build, where available_environments() simply skips it.
_IBL_DIR = resource_root().parent / "ibl"

_EXTS = (".hdr", ".exr", ".png", ".jpg", ".jpeg", ".bmp")


def default_path() -> Path | None:
    """The bundled default environment, or None if it is missing."""
    candidates = sorted(_ASSET_DIR.glob("*.hdr")) + sorted(_ASSET_DIR.glob("*.png"))
    return candidates[0] if candidates else None


def available_environments() -> list[Path]:
    """All discoverable env maps: bundled assets first, then the ibl folder."""
    found: list[Path] = []
    for d in (_ASSET_DIR, _IBL_DIR):
        if d.is_dir():
            for p in sorted(d.iterdir()):
                if p.suffix.lower() in _EXTS:
                    found.append(p)
    return found


def _read_hdr_rgbe(path: str | Path) -> np.ndarray:
    """Decode a Radiance RGBE ``.hdr`` file to float32 ``(H, W, 3)`` linear radiance.

    Done by hand because imageio's default backend silently decodes ``.hdr`` as
    8-bit (clamping the highlights that carry most of an HDRI's lighting energy),
    and its float-capable freeimage plugin needs a runtime DLL download. Supports
    the standard ``-Y H +X W`` orientation with new-style RLE or flat scanlines.
    """
    data = np.fromfile(path, dtype=np.uint8)
    # -- ASCII header up to a blank line, then the resolution line --
    pos = 0
    def readline() -> bytes:
        nonlocal pos
        end = int(np.where(data[pos:] == 0x0A)[0][0]) + pos
        line = data[pos:end].tobytes(); pos = end + 1
        return line
    if not readline().startswith(b"#?"):
        raise ValueError("not a Radiance HDR file")
    while readline().strip() != b"":
        pass
    res = readline().split()
    if len(res) != 4 or res[0] != b"-Y" or res[2] != b"+X":
        raise ValueError(f"unsupported HDR orientation: {res!r}")
    height, width = int(res[1]), int(res[3])

    rgbe = np.zeros((height, width, 4), dtype=np.uint8)
    for y in range(height):
        h = data[pos:pos + 4]
        new_rle = (h.size == 4 and h[0] == 2 and h[1] == 2
                   and ((int(h[2]) << 8) | int(h[3])) == width)
        if new_rle:
            pos += 4
            for c in range(4):                 # each channel RLE-encoded separately
                x = 0
                while x < width:
                    n = int(data[pos]); pos += 1
                    if n > 128:                # run: (n-128) copies of one byte
                        rgbe[y, x:x + n - 128, c] = data[pos]; pos += 1; x += n - 128
                    else:                      # literal: n raw bytes
                        rgbe[y, x:x + n, c] = data[pos:pos + n]; pos += n; x += n
        else:                                  # flat RGBE scanline
            rgbe[y] = data[pos:pos + width * 4].reshape(width, 4); pos += width * 4

    exponent = rgbe[..., 3].astype(np.int32)
    # RGBE -> float: mantissa * 2^(E - 128 - 8); E == 0 means the pixel is black.
    scale = np.where(exponent > 0, np.exp2(exponent - (128 + 8)), 0.0).astype(np.float32)
    return np.ascontiguousarray(rgbe[..., :3].astype(np.float32) * scale[..., None])


def load_environment(path: str | Path) -> np.ndarray:
    """Read an equirectangular map as float32 ``(H, W, 3)`` linear radiance.

    ``.hdr`` files are decoded to their true (un-clamped) linear range; 8-bit
    images are de-gamma'd to approximate linear light so reflections/lighting
    look right.
    """
    path = Path(path)
    if path.suffix.lower() == ".hdr":
        return _read_hdr_rgbe(path)

    import imageio.v3 as iio
    raw = np.asarray(iio.imread(path))
    is_integer = np.issubdtype(raw.dtype, np.integer)
    arr = raw.astype(np.float32)
    if arr.ndim == 2:                          # grayscale -> rgb
        arr = np.stack([arr] * 3, axis=-1)
    arr = arr[..., :3]

    if is_integer:
        # 8-bit LDR source: normalize and convert sRGB -> linear.
        arr = np.power(np.clip(arr / 255.0, 0.0, 1.0), 2.2)
    return np.ascontiguousarray(arr)
