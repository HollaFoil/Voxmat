"""Orbit camera producing view / projection matrices (numpy, column-major GL)."""

from __future__ import annotations

import numpy as np


def perspective(fovy_deg: float, aspect: float, near: float, far: float) -> np.ndarray:
    f = 1.0 / np.tan(np.radians(fovy_deg) / 2.0)
    m = np.zeros((4, 4), dtype="f4")
    m[0, 0] = f / max(aspect, 1e-6)
    m[1, 1] = f
    m[2, 2] = (far + near) / (near - far)
    m[2, 3] = (2 * far * near) / (near - far)
    m[3, 2] = -1.0
    return m


def look_at(eye: np.ndarray, target: np.ndarray, up: np.ndarray) -> np.ndarray:
    f = target - eye
    f = f / np.linalg.norm(f)
    s = np.cross(f, up)
    s = s / np.linalg.norm(s)
    u = np.cross(s, f)
    m = np.eye(4, dtype="f4")
    m[0, :3] = s
    m[1, :3] = u
    m[2, :3] = -f
    m[0, 3] = -np.dot(s, eye)
    m[1, 3] = -np.dot(u, eye)
    m[2, 3] = np.dot(f, eye)
    return m


class OrbitCamera:
    """Azimuth/elevation orbit around a target point."""

    def __init__(self):
        self.target = np.zeros(3, dtype="f4")
        self.distance = 60.0
        self.azimuth = np.radians(35.0)     # around vertical (Z) axis
        self.elevation = np.radians(25.0)
        self.fovy = 45.0
        self.up = np.array([0, 0, 1], dtype="f4")  # Z-up to match voxel grid

    def frame_dims(self, dims: tuple[int, int, int]) -> None:
        """Center the target and choose a distance that fits the volume."""
        x, y, z = dims
        self.target = np.array([x / 2, y / 2, z / 2], dtype="f4")
        self.distance = max(x, y, z) * 2.2 + 5.0

    @property
    def eye(self) -> np.ndarray:
        ce, se = np.cos(self.elevation), np.sin(self.elevation)
        ca, sa = np.cos(self.azimuth), np.sin(self.azimuth)
        offset = np.array([ce * ca, ce * sa, se], dtype="f4") * self.distance
        return self.target + offset

    def orbit(self, d_azimuth: float, d_elevation: float) -> None:
        self.azimuth += d_azimuth
        lim = np.radians(89.0)
        self.elevation = float(np.clip(self.elevation + d_elevation, -lim, lim))

    def pan(self, dx: float, dy: float) -> None:
        eye = self.eye
        forward = self.target - eye
        forward /= np.linalg.norm(forward)
        right = np.cross(forward, self.up)
        right /= np.linalg.norm(right)
        true_up = np.cross(right, forward)
        scale = self.distance * 0.0015
        self.target = self.target + (-dx * right + dy * true_up) * scale

    def zoom(self, steps: float) -> None:
        self.distance = float(np.clip(self.distance * (0.9 ** steps), 1.0, 100000.0))

    def view_matrix(self) -> np.ndarray:
        return look_at(self.eye, self.target, self.up)

    def proj_matrix(self, aspect: float) -> np.ndarray:
        return perspective(self.fovy, aspect, 0.1, 100000.0)

    def view_proj(self, aspect: float) -> np.ndarray:
        return self.proj_matrix(aspect) @ self.view_matrix()
