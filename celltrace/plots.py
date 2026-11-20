from math import floor, log10

import numpy as np
from PIL import Image, ImageDraw

from .images import png_bytes
from .render import font


PLOT_BG = (22, 33, 40)
PLOT_LINE = (142, 230, 192)
PLOT_INK = (205, 223, 219)
PLOT_FAINT = (57, 83, 87)
MISSING = (116, 73, 94)
PLOT_AUX = (125, 175, 223)


def nice_ticks(lower: float, upper: float, desired=5):
    wide = upper - lower
    if wide <= 0:
        return [lower]
    raw = wide / desired
    power = 10 ** floor(log10(raw))
    normalized = raw / power
    clean = next(n for n in (1, 2, 2.5, 5, 10) if n >= normalized) * power
    start = np.ceil((lower + 1e-9) / clean) * clean
    return np.arange(start, upper + 1e-9, clean).tolist()


class Chart:
    def __init__(self, title: str, title_x: str, title_y: str, x_range: tuple, y_range: tuple):
        self.image = Image.new("RGB", (820, 310), PLOT_BG)
        self.painter = ImageDraw.Draw(self.image, "RGBA")
        self.left, self.top, self.right, self.bottom = 76, 54, 796, 242
        self.x_range = x_range
        self.y_range = y_range
        self.label_font = font(11)
        self.caption(title, title_x, title_y)
        self.axes()

    def x(self, value):
        return self.left + (self.right - self.left) * (value - self.x_range[0]) / max(
            0.000001, self.x_range[1] - self.x_range[0]
        )

    def y(self, value):
        return self.bottom - (self.bottom - self.top) * (value - self.y_range[0]) / max(
            0.000001, self.y_range[1] - self.y_range[0]
        )

    def caption(self, title, name_x, name_y):
        self.painter.text((23, 16), title, fill=PLOT_INK, font=font(19))
        self.painter.text((self.left, 37), name_y, fill=PLOT_INK, font=self.label_font)
        self.painter.text((self.right, 284), name_x, fill=PLOT_INK, font=self.label_font, anchor="ra")

    def axes(self):
        for value in nice_ticks(*self.y_range):
            pos = self.y(value)
            self.painter.line((self.left, pos, self.right, pos), fill=PLOT_FAINT, width=1)
            self.painter.text((self.left - 10, pos + 4), f"{value:.4g}", anchor="rs", font=self.label_font, fill=PLOT_INK)
        for value in nice_ticks(*self.x_range, desired=8):
            pos = self.x(value)
            self.painter.line((pos, self.top, pos, self.bottom), fill=PLOT_FAINT, width=1)
            self.painter.text((pos, self.bottom + 8), f"{value:.4g}", anchor="mt", font=self.label_font, fill=PLOT_INK)
        self.painter.rectangle((self.left, self.top, self.right, self.bottom), outline=PLOT_FAINT)

    def fill_gap(self, lower: float, upper: float):
        self.shade(lower, upper, MISSING)

    def shade(self, lower: float, upper: float, color, alpha=91):
        x0 = min(self.right, max(self.left, self.x(lower)))
        x1 = max(x0 + 1, min(self.right + 1, self.x(upper)))
        self.painter.rectangle((x0, self.top, x1, self.bottom), fill=(*color, alpha))

    def mark(self, value: float):
        pos = self.x(value)
        self.painter.line((pos, self.top, pos, self.bottom), fill=(*PLOT_AUX, 130), width=1)

    def tag(self, value: float, label: str):
        pos = self.x(value)
        self.painter.text((pos, self.top + 12), label, anchor="mm", font=self.label_font, fill=PLOT_AUX)

    def stroke(self, xs: list, ys: list, color, width=2):
        group = []
        for x, y in zip(xs, ys):
            if y is None or not np.isfinite(y):
                self.draw_group(group, color, width)
                group = []
                continue
            group.append((self.x(x), min(self.bottom, max(self.top, self.y(y)))))
        self.draw_group(group, color, width)

    def draw_group(self, group, color, width):
        if len(group) > 1:
            self.painter.line(group, fill=color, width=width)
        elif group:
            x, y = group[0]
            self.painter.ellipse((x - 2, y - 2, x + 2, y + 2), fill=color)

    def bytes(self):
        return png_bytes(self.image)


def y_limits(values, include_zero=True):
    finite = [value for value in values if value is not None and np.isfinite(value)]
    if not finite:
        return (0.0, 1.0)
    lo = min(0, min(finite)) if include_zero else min(finite)
    hi = max(finite)
    if hi <= lo:
        return lo - 0.006, lo + 0.006
    pad = max(0.0012, (hi - lo) * 0.08)
    return lo - pad, hi + pad


def transect_plot(data: dict):
    rows = data["rows"]
    summary = data["summary"]
    physical = summary["mm_traveled"] is not None
    label = "Distance (mm)" if physical else "Distance (plane px)"
    key = "distance_mm" if physical else "distance"
    xs = [row[key] for row in rows]
    ys = [row["signal"] for row in rows]
    chart = Chart("Cross-section signal", label, "Relative sensor signal", (xs[0], xs[-1]), y_limits(ys))

    def band(index):
        left = xs[index] if index == 0 else (xs[index - 1] + xs[index]) / 2
        right = xs[index] if index == len(xs) - 1 else (xs[index] + xs[index + 1]) / 2
        return left, right

    for index, row in enumerate(rows):
        if row["shared"]:
            chart.shade(*band(index), PLOT_AUX)
    for index, row in enumerate(rows):
        if row["signal"] is None:
            chart.fill_gap(*band(index))
    segments = summary.get("segments", [])
    if len(segments) > 1:
        for segment in segments[1:]:
            chart.mark(rows[segment["first_index"] - 1][key])
        for segment in segments:
            start = rows[segment["first_index"] - 1][key]
            end = rows[segment["last_index"] - 1][key]
            chart.tag((start + end) / 2, f"S{segment['segment']}")
    chart.stroke(xs, ys, PLOT_LINE)
    return chart.bytes()


def reference_plot(report: dict):
    rows = report["rows"]
    xs = [row["index"] for row in rows]
    ys = [row["level"] for row in rows]
    smooth = [row["trend"] for row in rows]
    chart = Chart(
        "Reference sensor profile", "Native pixel index", f"Median normalized {report['axis']}",
        (xs[0], xs[-1]), y_limits(ys, include_zero=False),
    )
    for row in report["summary"]["outlying_runs"]:
        chart.fill_gap(row["from"], row["through"] + 1)
    chart.stroke(xs, ys, PLOT_LINE)
    chart.stroke(xs, smooth, PLOT_AUX, 1)
    return chart.bytes()
