"""Renderers for recipe brightness baselines and their comparison heatmaps."""
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageDraw

from .baseline_model import BrightnessBaseline
from .geometry import cells_for
from .images import continuous_palette, png_bytes
from .model import Inspection, Plane
from .render import decorate_grid, font, panel_preview


SHAPE_PALETTE = ((27, 28, 57), (70, 65, 151), (24, 132, 167), (52, 192, 167), (228, 217, 120))
# Diverging: blue = weaker than recipe, amber/red = stronger.
DEVIATION_PALETTE = ((161, 56, 123), (214, 99, 79), (246, 202, 120), (59, 175, 174), (32, 87, 158))
STATUS_COLORS = {
    "out-of-family": (240, 89, 141),
    "in-family": (52, 192, 167),
    "limited-ok": (246, 202, 120),
    "no-evidence": (120, 120, 135),
    "not-measured": (70, 70, 84),
}


@dataclass(frozen=True)
class BaselinePlane:
    width: int
    height: int
    rows: int
    cols: int


def _plane_for(baseline: BrightnessBaseline) -> Plane:
    # Cell geometry only needs the grid; a 220px-per-cell canvas keeps labels legible.
    return Plane(width=baseline.cols * 220, height=baseline.rows * 220,
                 rows=baseline.rows, cols=baseline.cols, inset=0.06)


def _hatch(draw: ImageDraw.ImageDraw, box, color=(255, 255, 255, 90), spacing=14):
    x0, y0, x1, y1 = box
    for offset in range(-int(y1 - y0), int(x1 - x0), spacing):
        draw.line((x0 + offset, y1, x0 + offset + (y1 - y0), y0), fill=color, width=2)


def baseline_canvas(baseline: BrightnessBaseline) -> bytes:
    plane = _plane_for(baseline)
    cells = cells_for(plane)
    raster = np.zeros((plane.height, plane.width, 3), dtype=np.uint8)
    values = np.array([cell.relative_median or np.nan for cell in baseline.cells], dtype=np.float64)
    finite = values[np.isfinite(values)]
    low, high = (float(finite.min()), float(finite.max())) if finite.size else (0.5, 1.5)
    if high - low < 1e-4:
        high = low + 0.1
    normalized = np.clip((values - low) / (high - low), 0, 1)
    normalized = np.nan_to_num(normalized, nan=0.0)
    colors = continuous_palette(normalized, (0.0, 1.0), SHAPE_PALETTE)
    image = Image.fromarray(raster, "RGB")
    draw = ImageDraw.Draw(image, "RGBA")
    face = font(26)
    small = font(18)
    for cell, color, ref in zip(cells, colors, baseline.cells):
        x0, y0, x1, y1 = cell.bounds
        draw.rectangle((x0, y0, x1 - 1, y1 - 1), fill=tuple(int(v) for v in color))
        if ref.evidence == "unsupported":
            draw.rectangle((x0, y0, x1 - 1, y1 - 1), fill=(45, 45, 58, 235))
            draw.text((x0 + 10, y0 + 10), "no data", fill=(220, 220, 230), font=small)
        elif ref.evidence == "limited":
            _hatch(draw, (x0, y0, x1, y1))
        label = f"n={ref.samples}"
        value = f"{ref.relative_median:.3f}" if ref.relative_median is not None else "—"
        draw.text((x0 + 12, y1 - 64), value, fill=(20, 26, 34), font=face)
        draw.text((x0 + 12, y1 - 32), label, fill=(20, 26, 34), font=small)
    decorate_grid(image, cells, labels=True)
    banner = (
        f"{baseline.name} v{baseline.version} · recipe {baseline.recipe} · "
        f"{len(baseline.sources)} reference panels · cell median / panel median"
    )
    return _compose(image, banner, _baseline_legend())


def comparison_canvas(baseline: BrightnessBaseline, comparison: dict) -> bytes:
    plane = _plane_for(baseline)
    cells = cells_for(plane)
    image = Image.new("RGB", (plane.width, plane.height), (18, 24, 32))
    draw = ImageDraw.Draw(image, "RGBA")
    face = font(24)
    small = font(17)
    by_cell = {row["cell"]: row for row in comparison["rows"]}
    # Symmetric log-ish scale around 1.0 for absolute ratios.
    for cell in cells:
        x0, y0, x1, y1 = cell.bounds
        row = by_cell.get(cell.id)
        color = STATUS_COLORS[row["status"] if row else "not-measured"]
        draw.rectangle((x0, y0, x1 - 1, y1 - 1), fill=(*color, 235))
        if row and row["evidence"] == "limited" and row["status"] not in ("not-measured", "no-evidence"):
            _hatch(draw, (x0, y0, x1, y1))
        if row and row["status"] == "not-measured":
            draw.text((x0 + 10, y0 + 44), "not measured", fill=(225, 225, 232), font=small)
        ratio = row["absolute_ratio"] if row else None
        z = row["z"] if row else None
        draw.text((x0 + 10, y1 - 88), f"abs {ratio:.2f}" if ratio else "abs —",
                  fill=(20, 24, 32), font=small)
        draw.text((x0 + 10, y1 - 60), f"z {z:.1f}" if z is not None else "z —",
                  fill=(20, 24, 32), font=small)
        draw.text((x0 + 10, y1 - 32), f"n={row['reference_samples'] if row else 0}",
                  fill=(20, 24, 32), font=small)
    decorate_grid(image, cells, labels=True)
    panel = comparison["panel"]
    banner = (
        f"{comparison['module']} vs {baseline.name} v{baseline.version} · "
        f"panel absolute ratio {panel['absolute_ratio']:.3f}"
        + (" · GLOBAL DIM" if panel["global_dim"] else "")
    )
    legend = [
        ("#f0598d", "Out of family"), ("#34c0a7", "In family"), ("#f6ca78", "Limited evidence (hatched)"),
        ("#787887", "No reference data"), ("#464654", "Not measured"),
    ]
    return _compose(image, banner, legend)


def overlay_canvas(inspection: Inspection, engine, comparison: dict) -> bytes:
    context = engine.panel_context(inspection, "high")
    base = panel_preview(context.panel, inspection.display).convert("RGB")
    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    by_cell = {row["cell"]: row for row in comparison["rows"]}
    for cell in context.cells:
        row = by_cell.get(cell.id)
        if not row or row["status"] not in ("out-of-family", "limited-ok", "no-evidence"):
            continue
        x0, y0, x1, y1 = cell.bounds
        color = STATUS_COLORS[row["status"]]
        draw.rectangle((x0, y0, x1 - 1, y1 - 1), outline=(*color, 255), width=4)
        if row["status"] == "out-of-family":
            draw.rectangle((x0, y0, x1 - 1, y1 - 1), fill=(*color, 70))
    combined = Image.alpha_composite(base.convert("RGBA"), overlay).convert("RGB")
    decorate_grid(combined, context.cells, labels=True)
    banner = f"{inspection.module} · out-of-family cells outlined against the high-current panel"
    return _compose(combined, banner, [
        ("#f0598d", "Out of family"), ("#f6ca78", "Limited evidence"), ("#787887", "No reference data"),
    ])


def _baseline_legend():
    return [
        ("#34c0a7", "Brighter relative level"), ("#464197", "Darker relative level"),
        ("#f6ca78", "Hatched: limited evidence (<3 panels)"), ("#2d2d3a", "No supported data"),
    ]


def _compose(image: Image.Image, banner: str, legend: list[tuple[str, str]]) -> bytes:
    margin = 24
    header = 70
    legend_height = 44
    width = image.width + margin * 2
    height = image.height + header + legend_height + margin * 2
    canvas = Image.new("RGB", (width, height), (14, 20, 27))
    painter = ImageDraw.Draw(canvas)
    painter.text((margin, 18), banner, fill=(228, 238, 232), font=font(22))
    canvas.paste(image, (margin, header))
    x = margin
    for color, label in legend:
        painter.rectangle((x, header + image.height + 14, x + 22, header + image.height + 36),
                          fill=tuple(int(color[i:i + 2], 16) for i in (1, 3, 5)))
        painter.text((x + 30, header + image.height + 14), label, fill=(200, 214, 210), font=font(16))
        x += 30 + int(painter.textlength(label, font=font(16))) + 34
    return png_bytes(canvas)
