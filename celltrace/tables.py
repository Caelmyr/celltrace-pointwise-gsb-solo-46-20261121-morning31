import csv
from io import StringIO
import json

from .model import Issue


COLUMNS = {
    "quality": (
        "cell", "string", "expected_pixels", "usable_pixels", "clipped_pixels", "reference_gap_pixels",
        "outside_pixels", "near_floor_pixels", "usable_fraction", "local_contrast_p90", "median", "flags",
    ),
    "intensity": (
        "cell", "string", "interior", "support", "reliable", "median", "p10", "p90",
        "active_pixels", "mad", "relative_panel",
    ),
    "dark": (
        "id", "cell", "bounds", "center", "pixels", "cell_fraction", "area_mm2", "mean_signal", "depth",
        "reference", "open_boundary",
    ),
    "cracks": (
        "id", "cell", "bounds", "center", "length_px", "length_mm", "mean_width_px", "median_contrast",
        "peak_contrast", "axis_degrees", "endpoints", "junctions", "paths",
    ),
    "response": (
        "cell", "string", "expected_pixels", "common_pixels", "stable_pixels", "support", "ratio", "ratio_p10",
        "ratio_p90", "relative", "weak_fraction", "assessment",
    ),
    "strings": (
        "string", "cells", "coverage", "median_relative", "p90_p10_span", "largest_step", "low_runs", "flags", "readings",
    ),
    "transect": ("index", "x", "y", "distance", "distance_mm", "cell", "usable", "support", "signal", "spread"),
    "lot": ("module", "lot", "state", "measurement", "median", "usable_cells", "total_cells", "crack_count", "dark_count"),
    "lot-cells": (
        "module", "lot", "cell", "pool", "median", "support", "median_relative", "capture", "digest", "issue_date",
    ),
    "findings": ("id", "title", "category", "status", "source_role", "cell", "note", "source_status",
                 "capture_digest", "capture", "anchor", "points", "created_at", "updated_at"),
    "calibration": ("metric", "value"),
    "placement": ("marker", "axis", "expected", "detected", "offset", "start", "end", "data",
                  "coverage", "contrast", "status"),
    "reference": ("region", "bounds", "pixels", "common", "lost", "gained", "increased",
                  "decreased", "old_median", "new_median", "median_delta", "peak_delta"),
    "baseline-cells": (
        "cell", "row", "col", "evidence", "samples", "rejected", "relative_median", "relative_mad",
        "low", "high", "absolute_median", "source_modules",
    ),
    "baseline-sources": (
        "inspection_id", "module", "lot", "capture_id", "digest", "issued_at", "current_a",
        "exposure_ms", "bit_depth", "white_level", "dark_id", "flat_id", "panel_median",
        "eligible_cells", "synthetic",
    ),
    "baseline-comparison": (
        "cell", "row", "col", "status", "evidence", "reference_samples", "observed_median",
        "observed_relative", "reference_relative_median", "reference_relative_mad",
        "absolute_ratio", "z", "support", "reference_modules", "reasons",
    ),
}


def plain_value(value):
    if value is None:
        return ""
    if isinstance(value, (list, dict, tuple, bool)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def csv_data(kind: str, rows: list[dict]) -> bytes:
    columns = COLUMNS.get(kind)
    if columns is None:
        raise Issue("This table has no CSV definition.", 404)
    buffer = StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(columns)
    for row in rows:
        writer.writerow(plain_value(row.get(column)) for column in columns)
    return buffer.getvalue().encode("utf-8-sig")
