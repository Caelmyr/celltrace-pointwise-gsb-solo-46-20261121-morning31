from dataclasses import dataclass, replace
from math import atan2, degrees, hypot

import numpy as np
from scipy import ndimage

from .calibration import PanelImage
from .geometry import Cell, millimeter_scale
from .images import distribution
from .model import (
    MAX_PATH_POINTS, MAX_PATHS, Inspection, Issue, Path, Plane, SENSOR_ROLES,
    choice, file_id, integer, real, text, utcnow, xy_list,
)
from .store import new_id


MAX_TRANSECT_SAMPLES = 2048
MAX_SHARED_WINDOW = 4  # half-width multiples searched around a corner for band overlap


@dataclass(frozen=True)
class Transect:
    points: tuple[tuple[float, float], ...]
    width: int
    interval: float

    @classmethod
    def from_dict(cls, payload: dict):
        points = xy_list(payload.get("points"), "Transect points", 2, MAX_PATH_POINTS, (0, 1), (0, 1))
        width = integer(payload.get("width", 5), "Strip width", 1, 51)
        interval = real(payload.get("interval", 1), "Sampling interval", 0.5, 24)
        if width % 2 == 0:
            raise Issue("Strip width must be an odd number of pixels.")
        return cls(points, width, interval)


@dataclass(frozen=True)
class Segment:
    unit: tuple[float, float]
    length: float
    offset: float
    interval: float
    angle: float


@dataclass(frozen=True)
class Strip:
    x: np.ndarray
    y: np.ndarray
    centers: np.ndarray
    distances: np.ndarray
    segment_ids: np.ndarray
    segment_distances: np.ndarray
    segments: list[Segment]
    vertices: np.ndarray


def prepare_strip(transect: Transect, plane: Plane) -> Strip:
    vertices = np.asarray(transect.points, dtype=np.float64) * (plane.width, plane.height)
    legs = []
    for start, end in zip(vertices, vertices[1:]):
        length = hypot(*(end - start))
        if length < 4:
            raise Issue("Each transect segment must be at least four plane pixels long.")
        legs.append(length)
    samples = 1 + sum(int(np.ceil(length / transect.interval)) for length in legs)
    if samples > MAX_TRANSECT_SAMPLES:
        raise Issue(f"A transect can contain at most {MAX_TRANSECT_SAMPLES} positions. Increase the sampling interval.")
    offsets = np.arange(transect.width, dtype=np.float64) - transect.width // 2
    centers, distances, seg_dists, x_blocks, y_blocks = [], [], [], [], []
    segment_ids = []
    segments = []
    arc = 0.0
    for index, ((start, end), length) in enumerate(zip(zip(vertices, vertices[1:]), legs)):
        dx, dy = end - start
        unit = dx / length, dy / length
        steps = int(np.ceil(length / transect.interval))
        local = np.linspace(0, length, steps + 1)
        if index:
            local = local[1:]
        block = start + local[:, None] * unit
        centers.append(block)
        distances.append(arc + local)
        seg_dists.append(local)
        segment_ids.extend([index] * len(local))
        x_blocks.append(block[:, 0, None] - unit[1] * offsets)
        y_blocks.append(block[:, 1, None] + unit[0] * offsets)
        segments.append(Segment(unit, length, arc, float(length / steps), degrees(atan2(unit[1], unit[0]))))
        arc += length
    return Strip(
        np.vstack(x_blocks),
        np.vstack(y_blocks),
        np.vstack(centers),
        np.concatenate(distances),
        np.asarray(segment_ids),
        np.concatenate(seg_dists),
        segments,
        vertices,
    )


def sample_strip(values: np.ndarray, support: np.ndarray, strip: Strip) -> tuple[np.ndarray, np.ndarray]:
    corrected = np.where(support, values, 0)
    offsets = strip.y - 0.5, strip.x - 0.5
    weights = ndimage.map_coordinates(
        support.astype(np.float32), offsets, order=1, mode="constant", cval=0, prefilter=False,
    )
    accepted = weights > 0.9999
    signal = ndimage.map_coordinates(
        corrected, offsets, order=1, mode="constant", cval=0, prefilter=False,
    )
    signal[~accepted] = np.nan
    return signal, accepted


def strip_cell_indices(cells: list[Cell], centers: np.ndarray, plane: Plane) -> list[str | None]:
    result = [None] * centers.shape[0]
    for cell in cells:
        x0, y0, x1, y1 = cell.bounds
        visible = (
            (centers[:, 0] >= x0) & (centers[:, 0] < x1)
            & (centers[:, 1] >= y0) & (centers[:, 1] < y1)
        )
        for index in np.flatnonzero(visible):
            result[index] = cell.id
    return result


def shared_stations(strip: Strip, width: int) -> list[set[int]]:
    """Extra segment indices whose lateral band intersects each station's band.

    Stations near a corner are candidates; two stations from different segments
    share strip pixels when their lateral sampling segments cross within the
    half width of both.
    """
    count = strip.centers.shape[0]
    extra: list[set[int]] = [set() for _ in range(count)]
    half = width // 2
    if half < 1 or len(strip.segments) < 2:
        return extra
    windows = {}
    for vertex in range(1, len(strip.segments)):
        ux1, uy1 = strip.segments[vertex - 1].unit
        ux2, uy2 = strip.segments[vertex].unit
        turn = ux1 * uy2 - uy1 * ux2
        if abs(turn) < 1e-3:
            continue  # collinear continuation or a full reversal has no corner wedge
        align = ux1 * ux2 + uy1 * uy2
        windows[vertex] = half if align >= 0 else min(half / (1 + align), MAX_SHARED_WINDOW * half)
    if not windows:
        return extra
    at_vertex = {}
    for vertex, window in windows.items():
        before, after = strip.segments[vertex - 1], strip.segments[vertex]
        near = (
            ((strip.segment_ids == vertex - 1) & (before.length - strip.segment_distances <= window + 1e-9))
            | ((strip.segment_ids == vertex) & (strip.segment_distances <= window + 1e-9))
        )
        at_vertex[vertex] = np.flatnonzero(near)
    groups = corner_groups(strip, windows)
    for group in groups:
        pool = np.unique(np.concatenate([at_vertex[vertex] for vertex in group]))
        if pool.size < 2:
            continue
        for first, second in band_crossings(strip, pool, half):
            extra[first].add(int(strip.segment_ids[second]))
            extra[second].add(int(strip.segment_ids[first]))
    return extra


def corner_groups(strip: Strip, windows: dict[int, float]) -> list[list[int]]:
    """Join corner vertices whose overlap windows reach across the segment between them."""
    groups = []
    for vertex in sorted(windows):
        if (
            groups and vertex - 1 in windows
            and strip.segments[vertex - 1].length <= windows[vertex - 1] + windows[vertex]
        ):
            groups[-1].append(vertex)
        else:
            groups.append([vertex])
    return groups


def band_crossings(strip: Strip, pool: np.ndarray, half: int):
    centers = strip.centers[pool]
    seg_of = strip.segment_ids[pool]
    ux = np.array([strip.segments[seg].unit[0] for seg in seg_of])
    uy = np.array([strip.segments[seg].unit[1] for seg in seg_of])
    nx, ny = -uy, ux
    cross = nx[:, None] * ny[None, :] - ny[:, None] * nx[None, :]
    wx = centers[None, :, 0] - centers[:, None, 0]
    wy = centers[None, :, 1] - centers[:, None, 1]
    with np.errstate(divide="ignore", invalid="ignore"):
        alpha = (wx * ny[None, :] - wy * nx[None, :]) / cross
        beta = (wx * ny[:, None] - wy * nx[:, None]) / cross
    hit = (
        (seg_of[:, None] != seg_of[None, :])
        & (np.abs(cross) > 1e-9)
        & (np.abs(alpha) <= half + 1e-9)
        & (np.abs(beta) <= half + 1e-9)
    )
    rows, cols = np.nonzero(hit)
    return zip((int(pool[index]) for index in rows), (int(pool[index]) for index in cols))


def analyze_transect(panel: PanelImage, plane: Plane, cells: list[Cell], transect: Transect) -> dict:
    strip = prepare_strip(transect, plane)
    values, support = sample_strip(panel.signal, panel.valid, strip)
    counts = support.sum(axis=1)
    dominant = counts >= max(1, (transect.width + 1) // 2)
    across = strip_cell_indices(cells, strip.centers, plane)
    shared = shared_stations(strip, transect.width)
    scale = millimeter_scale(plane)
    mm_units = [
        hypot(seg.unit[0] * scale[0], seg.unit[1] * scale[1]) if scale else None
        for seg in strip.segments
    ]
    mm_offsets = [0.0]
    for seg, unit in zip(strip.segments, mm_units):
        mm_offsets.append(mm_offsets[-1] + (seg.length * unit if unit is not None else 0.0))
    measurements = []
    for index, (point, cell) in enumerate(zip(strip.centers, across)):
        seg = int(strip.segment_ids[index])
        chosen = values[index, support[index]]
        value = float(np.median(chosen)) if dominant[index] else None
        seg_distance = float(strip.segment_distances[index])
        measurements.append({
            "index": index + 1,
            "x": round(float(point[0]), 3), "y": round(float(point[1]), 3),
            "distance": round(float(strip.distances[index]), 3),
            "distance_mm": round(mm_offsets[seg] + seg_distance * mm_units[seg], 3)
                           if mm_units[seg] is not None else None,
            "segment": seg + 1,
            "segment_distance": round(seg_distance, 3),
            "cell": cell,
            "usable": int(counts[index]),
            "support": round(float(counts[index] / transect.width), 4),
            "signal": round(value, 6) if value is not None else None,
            "spread": round(float(np.ptp(chosen)), 6) if value is not None else None,
            "shared": bool(shared[index]),
            "segments": sorted({seg + 1, *(partner + 1 for partner in shared[index])}),
        })
    trace_values = np.array([row["signal"] if row["signal"] is not None else np.nan for row in measurements])
    single = len(strip.segments) == 1
    span = {
        "cells": list(dict.fromkeys(cell for cell in across if cell is not None)),
        "data_positions": int(dominant.sum()),
        "positions": int(values.shape[0]),
        "width": transect.width,
        "angle": round(strip.segments[0].angle, 3) if single else None,
        "pixels_traveled": round(float(strip.distances[-1]), 3),
        "mm_traveled": measurements[-1]["distance_mm"],
        "interval": round(strip.segments[0].interval, 3) if single else transect.interval,
        "shared_positions": sum(1 for partners in shared if partners),
        "signal": distribution(trace_values),
        "missing_intervals": unavailable_intervals(measurements),
        "deepest_valleys": notable_valleys(measurements, trace_values),
        "segments": segment_summaries(strip, measurements, mm_units),
        "overlaps": overlap_zones(measurements),
    }
    return {"summary": span, "rows": measurements}


def segment_summaries(strip: Strip, measurements: list[dict], mm_units: list) -> list[dict]:
    summaries = []
    for seg_index, seg in enumerate(strip.segments):
        rows = [row for row in measurements if row["segment"] == seg_index + 1]
        values = np.array([row["signal"] if row["signal"] is not None else np.nan for row in rows])
        summaries.append({
            "segment": seg_index + 1,
            "stations": len(rows),
            "first_index": rows[0]["index"],
            "last_index": rows[-1]["index"],
            "data_positions": sum(1 for row in rows if row["signal"] is not None),
            "shared": sum(1 for row in rows if row["shared"]),
            "cells": list(dict.fromkeys(row["cell"] for row in rows if row["cell"] is not None)),
            "distance": [rows[0]["distance"], rows[-1]["distance"]],
            "distance_mm": [rows[0]["distance_mm"], rows[-1]["distance_mm"]]
                           if mm_units[seg_index] is not None else None,
            "pixels": round(seg.length, 3),
            "mm": round(seg.length * mm_units[seg_index], 3) if mm_units[seg_index] is not None else None,
            "angle": round(seg.angle, 3),
            "interval": round(seg.interval, 3),
            "signal": distribution(values),
        })
    return summaries


def overlap_zones(measurements: list[dict]) -> list[dict]:
    zones = []
    current = None
    for row in measurements:
        if row["shared"]:
            if current is None:
                current = {"segments": set(), "positions": 0, "first": row["index"], "start": row["distance"]}
            current["segments"].update(row["segments"])
            current["positions"] += 1
            current["last"] = row["index"]
            current["end"] = row["distance"]
            continue
        if current is not None:
            zones.append({**current, "segments": sorted(current["segments"])})
            current = None
    if current is not None:
        zones.append({**current, "segments": sorted(current["segments"])})
    return zones


def unavailable_intervals(measurements: list[dict]) -> list[dict]:
    first = None
    intervals = []
    for index, row in enumerate(measurements):
        if row["signal"] is None:
            if first is None:
                first = index
            continue
        if first is not None:
            intervals.append(interval(first, index - 1, measurements))
            first = None
    if first is not None:
        intervals.append(interval(first, len(measurements) - 1, measurements))
    return intervals


def interval(start: int, end: int, data: list[dict]) -> dict:
    return {"start": data[start]["distance"], "end": data[end]["distance"], "positions": end - start + 1}


def notable_valleys(measurements: list[dict], signal: np.ndarray) -> list[dict]:
    if signal.size < 7:
        return []
    valleys = []
    for index in range(3, signal.size - 3):
        values = signal[index - 3:index + 4]
        if not np.isfinite(values).all():
            continue
        current = values[3]
        if current > values[2] or current >= values[4]:
            continue
        reference = min(np.max(values[:3]), np.max(values[4:]))
        drop = reference - current
        if drop >= 0.02:
            valleys.append({
                "distance": measurements[index]["distance"],
                "cell": measurements[index]["cell"],
                "signal": round(float(current), 5),
                "contrast": round(float(drop), 5),
            })
    return sorted(valleys, key=lambda valley: -valley["contrast"])[:12]


def get_path(inspection: Inspection, path_id: str) -> Path:
    file_id(path_id, "Path ID")
    for path in inspection.paths:
        if path.id == path_id:
            return path
    raise Issue("This transect path does not exist.", 404)


def make_path(inspection: Inspection, raw: dict, path_id: str | None = None) -> Path:
    saved = get_path(inspection, path_id) if path_id is not None else None
    if saved is None and len(inspection.paths) >= MAX_PATHS:
        raise Issue("This inspection contains the maximum number of transect paths.", 409)
    name = text(raw.get("name"), "Path name", 80)
    role = choice(raw.get("source_role"), "Path source", SENSOR_ROLES)
    transect = Transect.from_dict(raw)
    prepare_strip(transect, inspection.plane)
    modified = utcnow()
    geometry = (role, transect.points, transect.width, transect.interval)
    if saved is not None and (saved.source_role, saved.points, saved.width, saved.interval) == geometry:
        return replace(saved, name=name, updated_at=modified)
    return Path(
        path_id or new_id(),
        name,
        role,
        transect.points,
        transect.width,
        transect.interval,
        saved.created_at if saved is not None else modified,
        modified,
    )
