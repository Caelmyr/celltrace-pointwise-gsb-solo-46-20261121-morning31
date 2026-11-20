from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage

from .geometry import homography, project
from .images import png_bytes
from .model import Plane


DEMO_NOTICE = "Synthetic electroluminescence training fixture. Values and anomalies are generated."


@dataclass(frozen=True)
class DemoCapture:
    role: str
    name: str
    content: bytes
    corners: tuple | None
    orientation: str
    exposure_ms: float
    current_a: float | None


@dataclass
class Scene:
    high: np.ndarray
    low: np.ndarray


def demo_plane() -> Plane:
    return Plane(
        width=840, height=540, rows=4, cols=6, inset=0.07, string_axis="columns", string_size=2,
        physical_width=1722, physical_height=1134,
    )


def panel_scene(plane: Plane, rng, variant: str) -> Scene:
    y, x = np.mgrid[:plane.height, :plane.width].astype(np.float32)
    column = x / (plane.width / plane.cols)
    row = y / (plane.height / plane.rows)
    u = column % 1
    v = row % 1
    tile = (np.floor(row).astype(int), np.floor(column).astype(int))
    bevel = np.minimum.reduce((u + v, 1 - u + v, u + 1 - v, 2 - u - v))
    active = (u >= 0.037) & (u <= 0.963) & (v >= 0.038) & (v <= 0.962) & (bevel >= 0.145)
    field = np.full((plane.height, plane.width), 0.009, dtype=np.float32)
    strength = rng.uniform(0.41, 0.61, (plane.rows, plane.cols))
    strength[2, 4:6] *= 0.66
    strength[3, 5] *= 0.79
    silky = 0.017 * np.sin(21 * u + 37 * v) + 0.010 * np.cos(35 * v - 10 * u)
    weave = ndimage.gaussian_filter(rng.normal(size=field.shape).astype(np.float32), 4.0)
    local = strength[tile] + 0.045 * (u - 0.5) - 0.054 * (v - 0.5) + silky + 0.17 * weave
    field[active] = local[active]
    fingers = np.sin(np.pi * v * 53) ** 24
    field[active] += (0.027 * fingers[active]).astype(np.float32)
    bars = active & ((abs(u - 0.28) < 0.011) | (abs(u - 0.72) < 0.011))
    field[bars] *= 0.72
    contacts = bars & ((abs(v - 0.19) < 0.03) | (abs(v - 0.82) < 0.03))
    field[contacts] *= 1.21
    soft_edge = np.exp(-7 * np.minimum.reduce((u, 1 - u, v, 1 - v))) * 0.1
    field[active] *= 1 - soft_edge[active]
    dark_shapes(field, column, row, variant)
    low = low_current(field, column, row, active, variant)
    noise = rng.normal(0, 0.005, field.shape)
    field[active] += noise[active]
    low[active] += noise[active] * 0.51
    return Scene(field, low)


def dark_shapes(field: np.ndarray, columns: np.ndarray, rows: np.ndarray, variant: str):
    depressions = [
        (2.63, 1.47, 0.25, 0.18, 0.68),
        (1.43, 2.68, 0.19, 0.28, 0.67),
        (4.76, 0.63, 0.33, 0.11, 0.77),
        (0.12, 3.62, 0.20, 0.24, 0.65),
        (3.46, 3.49, 0.12, 0.10, 0.67),
    ]
    if variant == "stress":
        depressions.extend(((5.45, 1.47, 0.32, 0.34, 0.77), (2.16, 2.19, 0.13, 0.29, 0.71)))
    for cx, cy, rx, ry, depth in depressions:
        ellipse = ((columns - cx) / rx) ** 2 + ((rows - cy) / ry) ** 2
        field *= 1 - depth * np.exp(-ellipse**1.45).astype(np.float32)
    traces = [
        [(0.14, 0.23), (0.22, 0.37), (0.39, 0.43), (0.52, 0.59), (0.76, 0.66), (0.94, 0.69)],
        [(3.10, 1.70), (3.23, 1.6), (3.38, 1.49), (3.63, 1.42), (3.88, 1.13)],
        [(3.38, 1.49), (3.58, 1.52), (3.90, 1.80)],
        [(4.13, 2.30), (4.28, 2.38), (4.54, 2.58), (4.70, 2.80), (4.83, 2.91)],
        [(2.21, 2.27), (2.33, 2.34), (2.51, 2.66), (2.67, 2.93)],
        [(5.07, 3.38), (5.24, 3.56), (5.29, 3.76), (5.51, 3.89)],
    ]
    mark_traces(field, columns, rows, traces)


def mark_traces(field: np.ndarray, columns: np.ndarray, rows: np.ndarray, traces: list):
    mask = Image.new("L", field.shape[::-1])
    painter = ImageDraw.Draw(mask)
    x_step = columns[0, -1] / (field.shape[1] - 1)
    y_step = rows[-1, 0] / (field.shape[0] - 1)
    for line in traces:
        pixels = [(int(x / x_step), int(y / y_step)) for x, y in line]
        painter.line(pixels, fill=245, width=2, joint="curve")
    rays = np.asarray(mask).astype(np.float32) / 255
    shape = ndimage.gaussian_filter(rays, 0.49)
    field *= 1 - 0.53 * shape


def low_current(high, columns, rows, active, variant) -> np.ndarray:
    low = high * (0.48 + 0.027 * np.sin(2.1 * columns + 1.6 * rows))
    weak_cells = [(1, 3), (3, 2)]
    if variant == "stress":
        weak_cells.extend(((0, 0), (2, 5)))
    for row, col in weak_cells:
        region = (np.floor(rows) == row) & (np.floor(columns) == col) & active
        low[region] *= 0.52
    central = ((columns - 4.62) ** 2 + (rows - 3.59) ** 2) < 0.19
    low[central & active] *= 0.32
    return low


def project_to_camera(scene: np.ndarray, frame_shape: tuple[int, int], corners: tuple,
                      inverse_x: bool) -> np.ndarray:
    h, w = scene.shape
    camera = np.asarray(corners, dtype=np.float64)
    module = np.array(((0, 0), (w, 0), (w, h), (0, h)), dtype=np.float64)
    if inverse_x:
        module = module[[1, 0, 3, 2]]
    matrix = homography(camera, module)
    yi, xi = np.ogrid[0.5:frame_shape[0]:1.0, 0.5:frame_shape[1]:1.0]
    u, v = project(matrix, xi, yi)
    sensor = ndimage.map_coordinates(
        scene, (v - 0.5, u - 0.5), order=1, mode="constant", cval=0.004, prefilter=False,
    )
    return sensor.astype(np.float32)


def sensor_components(shape: tuple[int, int], rng) -> tuple[np.ndarray, np.ndarray]:
    y, x = np.mgrid[:shape[0], :shape[1]].astype(np.float32)
    x /= shape[1]
    y /= shape[0]
    x_center = (x - 0.44) / 0.71
    y_center = (y - 0.52) / 0.79
    gain = np.clip(1.02 - 0.29 * (x_center**2 + y_center**2) + 0.05 * np.sin(x * 3.7), 0.5, 1.2)
    gain *= 1 - 0.95 * np.exp(-((x - 0.083) ** 2 + (y - 0.666) ** 2) / 0.0013)
    dark = 0.012 + 0.009 * y + 0.0032 * np.cos(9 * x)
    dark += 0.007 * np.exp(-((x - 0.83) ** 2 + (y - 0.89) ** 2) / 0.006)
    dark = dark + rng.normal(0, 0.0014, shape).astype(np.float32)
    flat = dark + 0.46 * gain
    return dark, flat


def synthetic_bytes(image: np.ndarray) -> bytes:
    pixels = np.rint(np.clip(image, 0, 1) * 65535).astype(np.uint16)
    return png_bytes(Image.fromarray(pixels))


def surface_panel(plane: Plane, rng) -> np.ndarray:
    """A plain grayscale module-face view: diffuse gray, smudges and one glare patch."""
    y, x = np.mgrid[:plane.height, :plane.width].astype(np.float32)
    nx, ny = x / plane.width, y / plane.height
    base = 0.52 + 0.05 * np.sin(6.5 * nx + 2.1) + 0.03 * np.cos(8 * ny)
    base += ndimage.gaussian_filter(rng.normal(size=base.shape).astype(np.float32), 5.0) * 0.03
    # Faint cell separator texture so the panel geometry stays readable.
    column = nx * plane.cols
    row = ny * plane.rows
    separators = (np.abs(column - np.round(column)) < 0.006) | (np.abs(row - np.round(row)) < 0.006)
    base[separators] *= 0.92
    # Smudges in plane-cell coordinates; the long one brushes two EL dark depressions.
    smudges = [
        (2.63, 1.47, 0.30, 0.22, 0.42),
        (2.98, 1.62, 0.26, 0.16, 0.38),
        (1.43, 2.68, 0.22, 0.30, 0.40),
        (4.76, 0.63, 0.18, 0.10, 0.30),
    ]
    for cx, cy, rx, ry, depth in smudges:
        ellipse = ((column - cx) / rx) ** 2 + ((row - cy) / ry) ** 2
        base *= 1 - depth * np.exp(-ellipse ** 1.3)
    # Specular glare near the right frame and a clipped rim.
    glare = np.exp(-(((nx - 0.965) / 0.03) ** 2 + ((ny - 0.30) / 0.12) ** 2))
    base = base * (1 - glare) + glare * 1.05
    return np.clip(base, 0, 1.15).astype(np.float32)


def make_demo(variant: str = "standard") -> tuple[Plane, list[DemoCapture]]:
    seed = 7421 if variant == "standard" else 9375
    rng = np.random.default_rng(seed)
    plane = demo_plane()
    scene = panel_scene(plane, rng, variant)
    frame_shape = (600, 968)
    high_corners = ((66, 55), (884, 34), (918, 554), (44, 536))
    low_corners = ((68, 35), (918, 57), (913, 545), (47, 557))
    if variant == "stress":
        low_corners = ((-37, 45), (920, 62), (936, 538), (-26, 560))
    high = project_to_camera(scene.high, frame_shape, high_corners, False)
    low = project_to_camera(scene.low, frame_shape, low_corners, True)
    dark, flat = sensor_components(frame_shape, rng)
    gain = (flat - dark) / 0.46
    y, x = np.ogrid[:frame_shape[0], :frame_shape[1]]
    hotspot = np.exp(-(((x - 800) / 36) ** 2 + ((y - 116) / 28) ** 2)) * 0.84
    high = high * gain + dark + hotspot
    low = low * gain + dark + rng.normal(0, 0.002, low.shape).astype(np.float32)
    surface_corners = ((58, 62), (892, 44), (906, 548), (52, 530))
    if variant == "stress":
        # The surface frame crops the right edge, so part of the panel has no surface support.
        surface_corners = ((58, 62), (1018, 48), (1032, 546), (52, 530))
    surface_face = surface_panel(plane, rng)
    surface_camera = project_to_camera(surface_face, frame_shape, surface_corners, False)
    surface_camera = np.clip(surface_camera + rng.normal(0, 0.004, frame_shape).astype(np.float32), 0, 1.15)
    return plane, [
        DemoCapture("high", f"SYN-{variant}-high.png", synthetic_bytes(high), high_corners,
                    "identity", 100, 8.5),
        DemoCapture("low", f"SYN-{variant}-low.png", synthetic_bytes(low), low_corners,
                    "mirror-x", 100, 1.3),
        DemoCapture("dark", f"SYN-{variant}-dark.png", synthetic_bytes(dark), None,
                    "identity", 100, None),
        DemoCapture("flat", f"SYN-{variant}-flat.png", synthetic_bytes(flat), None,
                    "identity", 100, None),
        DemoCapture("surface", f"SYN-{variant}-surface.png", synthetic_bytes(surface_camera),
                    surface_corners, "identity", 120, None),
    ]
