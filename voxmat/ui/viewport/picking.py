"""GPU colour-ID picking helpers.

Each instance is drawn to an offscreen buffer with a unique colour encoding its
``pick_id + 1`` (0 is reserved for "background / nothing"). Reading the pixel
under the cursor and decoding gives the voxel's linear id, which
``mesh.linear_id_to_coord`` turns back into an (x, y, z) coordinate.
"""

from __future__ import annotations


def encode_id_to_rgb(value: int) -> tuple[float, float, float]:
    """value (0..2^24-1) -> normalized RGB floats."""
    r = (value & 0xFF)
    g = (value >> 8) & 0xFF
    b = (value >> 16) & 0xFF
    return r / 255.0, g / 255.0, b / 255.0


def decode_rgb_to_id(rgb: tuple[int, int, int]) -> int:
    """Inverse of :func:`encode_id_to_rgb` for 0..255 byte values."""
    r, g, b = rgb
    return r | (g << 8) | (b << 16)
