"""Surface photo cross-check against EL dark evidence.

The surface photo is registered to the same panel plane as the EL captures with its
own four-corner placement. Reviewer-traced contamination marks are kept in
plane-normalized coordinates so they can be compared directly with the connected dark
regions measured on the rectified EL plane. Verdicts are *evidence labels*: a mark
never rewrites a dark region, it only states how much surface support exists for it.
"""
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage

from .calibration import PanelImage
from .engine import Engine
from .geometry import (
    SamplingMap, cells_for, default_placement, plane_polygon, polygon_mask, project,
    transform_for,
)
from .images import grayscale_preview, png_bytes
from .intensity import DarkMap
from .model import (
    Inspection, Issue, Placement, Plane, SurfaceMark, SurfaceReview, ViewSettings,
    choice, file_id, integer, text, utcnow, xy_list,
)
from .render import draw_tag, font
from .store import new_id

# A dark region is called occluded only when most of it sits inside a marked outline.
OCCLUDED_FRACTION = 0.70
PARTIAL_FRACTION = 0.08
# More than this fraction of the region inside the registration rim means the alignment
# uncertainty dominates the overlap, so the evidence is held as edge-uncertain.
RIM_FRACTION = 0.35
# Below this usable surface coverage the surface photo itself cannot witness the region.
SUPPORT_FRACTION = 0.60
GLARE_FRACTION = 0.98

VERDICT_COLORS = {
    "occluded": (247, 164, 84),
    "partial": (240, 203, 111),
    "edge-uncertain": (152, 198, 241),
    "no-surface-evidence": (150, 232, 212),
    "obscured-in-surface": (196, 138, 226),
}
MARK_COLORS = {
    "smudge": (247, 164, 84),
    "streak": (240, 203, 111),
    "film": (152, 198, 241),
    "other": (183, 226, 197),
}
VERDICT_LABELS = {
    "occluded": "Occluded by surface mark",
    "partial": "Partially covered",
    "edge-uncertain": "Edge alignment uncertain",
    "no-surface-evidence": "No surface mark evidence",
    "obscured-in-surface": "Obscured in surface photo",
}


@dataclass(frozen=True)
class SurfacePlane:
    signal: np.ndarray
    usable: np.ndarray
    glare: np.ndarray
    matrix: np.ndarray
    placement: Placement


def selected_surface(inspection: Inspection):
    review = inspection.surface_review
    if review.surface_id is None:
        raise Issue("Add and register a surface photo before running the cross-check.", 409)
    capture = inspection.captures.get(review.surface_id)
    if capture is None or capture.role != "surface":
        raise Issue("The registered surface photo is no longer available.", 409)
    return capture


def surface_placement(inspection: Inspection, capture) -> Placement:
    return inspection.placements.get(capture.id) or default_placement(capture.width, capture.height)


def rectify_surface(engine: Engine, inspection: Inspection, capture) -> SurfacePlane:
    placement = surface_placement(inspection, capture)
    transform_for(inspection.plane, placement)
    source = engine.read_source(capture)
    sampling = SamplingMap.for_plane(inspection.plane, placement, capture.width, capture.height)
    signal = sampling.samples(source.pixels.astype(np.float32) / capture.white_level)
    full = np.ones((capture.height, capture.width), dtype=bool)
    usable = sampling.usable(full)
    glare_native = source.pixels >= np.rint(capture.white_level * GLARE_FRACTION)
    glare = sampling.present(glare_native) & usable
    signal[~usable] = np.nan
    return SurfacePlane(signal, usable, glare, sampling.matrix, placement)


def mark_mask(inspection: Inspection, mark: SurfaceMark) -> np.ndarray:
    shape = plane_polygon(mark.points, inspection.plane)
    return polygon_mask(shape, inspection.plane.width, inspection.plane.height)


def marks_union(inspection: Inspection) -> np.ndarray:
    raster = np.zeros((inspection.plane.height, inspection.plane.width), dtype=bool)
    for mark in inspection.surface_review.marks:
        raster |= mark_mask(inspection, mark)
    return raster


def registration_rim(marks: np.ndarray, tolerance: int) -> np.ndarray:
    """Plane pixels within `tolerance` of a mark boundary but outside the mark itself."""
    if tolerance <= 0 or not marks.any():
        return np.zeros_like(marks)
    dilated = ndimage.binary_dilation(marks, iterations=tolerance)
    return dilated & ~marks


def classify_region(mask: np.ndarray, marks: np.ndarray, rim: np.ndarray,
                    surface: SurfacePlane) -> tuple[str, dict]:
    pixels = int(mask.sum())
    covered = int((mask & marks).sum())
    rim_hit = int((mask & rim).sum())
    usable = int((mask & surface.usable).sum())
    glare_hit = int((mask & surface.glare).sum())
    cover_fraction = round(covered / pixels, 5) if pixels else 0.0
    rim_fraction = round(rim_hit / pixels, 5) if pixels else 0.0
    support_fraction = round(usable / pixels, 5) if pixels else 0.0
    glare_fraction = round(glare_hit / pixels, 5) if pixels else 0.0
    if support_fraction < SUPPORT_FRACTION or glare_fraction > 1 - SUPPORT_FRACTION:
        verdict = "obscured-in-surface"
    elif cover_fraction >= OCCLUDED_FRACTION and rim_fraction < RIM_FRACTION:
        verdict = "occluded"
    elif rim_fraction >= RIM_FRACTION and cover_fraction < OCCLUDED_FRACTION:
        verdict = "edge-uncertain"
    elif cover_fraction >= PARTIAL_FRACTION:
        verdict = "partial"
    else:
        verdict = "no-surface-evidence"
    return verdict, {
        "pixels": pixels,
        "covered_pixels": covered,
        "rim_pixels": rim_hit,
        "surface_usable_pixels": usable,
        "glare_pixels": glare_hit,
        "cover_fraction": cover_fraction,
        "rim_fraction": rim_fraction,
        "support_fraction": support_fraction,
        "glare_fraction": glare_fraction,
    }


def region_mask(record: dict, plane: Plane) -> np.ndarray:
    x0, y0, x1, y1 = record["bounds"]
    raster = np.zeros((plane.height, plane.width), dtype=bool)
    raster[y0:y1, x0:x1] = True
    return raster


def exact_dark_masks(dark: DarkMap, plane: Plane, cells) -> dict[str, np.ndarray]:
    """Reconstruct the exact connected-component mask behind each dark region id.

    Region ids use the labels from the per-cell threshold image, which are not compact
    after minimum-area filtering, so components are matched by their recorded cell and
    bounding box rather than by re-labeling the union mask.
    """
    masks: dict[str, list[np.ndarray]] = {}
    for cell in cells:
        view = cell.view
        labeled, count = ndimage.label(dark.mask[view], structure=np.ones((3, 3), dtype=bool))
        if count == 0:
            continue
        x_origin, y_origin = cell.interior[0], cell.interior[1]
        for marker in range(1, count + 1):
            component = labeled == marker
            rows = np.any(component, axis=1)
            cols = np.any(component, axis=0)
            y0, y1 = np.flatnonzero(rows)[[0, -1]]
            x0, x1 = np.flatnonzero(cols)[[0, -1]]
            bounds = (x_origin + int(x0), y_origin + int(y0),
                      x_origin + int(x1) + 1, y_origin + int(y1) + 1)
            raster = np.zeros((plane.height, plane.width), dtype=bool)
            raster[view] = component
            masks.setdefault(f"{cell.id}|{bounds}", []).append(raster)
    return masks


def cross_check(engine: Engine, inspection: Inspection, role: str) -> dict:
    choice(role, "EL capture role", frozenset(("high", "low")))
    capture = selected_surface(inspection)
    review = inspection.surface_review
    surface = rectify_surface(engine, inspection, capture)
    dark: DarkMap = engine.dark(inspection, engine.panel_context(inspection, role))
    marks = marks_union(inspection)
    rim = registration_rim(marks, review.edge_tolerance_px)
    exact_masks = exact_dark_masks(dark, inspection.plane, cells_for(inspection.plane))
    rows = []
    for record in dark.records:
        candidates = exact_masks.get(f"{record['cell']}|{tuple(record['bounds'])}")
        if candidates:
            mask = next((item for item in candidates if int(item.sum()) == record["pixels"]), candidates[0])
        else:
            mask = region_mask(record, inspection.plane)
        automatic, metrics = classify_region(mask, marks, rim, surface)
        override = review.verdicts.get(record["id"], "auto")
        verdict = automatic if override == "auto" else override
        rows.append({
            "dark_region": record["id"],
            "cell": record["cell"],
            "bounds": record["bounds"],
            "center": record["center"],
            "dark_pixels": record["pixels"],
            "verdict": verdict,
            "automatic_verdict": automatic,
            "manual_override": override != "auto",
            **metrics,
        })
    rows.sort(key=lambda row: (row["verdict"], -row["dark_pixels"], row["dark_region"]))
    touching = [
        mark.id for mark in review.marks
        if int((mark_mask(inspection, mark) & dark.mask).sum()) > 0
    ]
    counts = {label: sum(1 for row in rows if row["verdict"] == label)
              for label in VERDICT_COLORS}
    return {
        "surface": {
            "id": capture.id, "name": capture.name, "digest": capture.digest,
            "width": capture.width, "height": capture.height,
            "placement_explicit": inspection.placements.get(capture.id) is not None,
        },
        "role": role,
        "edge_tolerance_px": review.edge_tolerance_px,
        "plane": [inspection.plane.width, inspection.plane.height],
        "revision": inspection.revision,
        "marks": [
            {
                "id": mark.id, "title": mark.title, "kind": mark.kind,
                "points": list(mark.points), "note": mark.note,
                "touches_dark": mark.id in touching,
                "updated_at": mark.updated_at,
            }
            for mark in review.marks
        ],
        "surface_coverage": round(float(surface.usable.sum() / surface.usable.size), 5),
        "glare_pixels": int(surface.glare.sum()),
        "rows": rows,
        "counts": counts,
        "note": review.note,
    }


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #

def surface_preview(surface: SurfacePlane, plane: Plane) -> Image.Image:
    display = ViewSettings(0.0, max(0.03, float(np.nanpercentile(surface.signal[surface.usable], 99))), 1.0)
    return grayscale_preview(surface.signal, surface.usable, display.lower, display.upper, display.gamma)


def paint_marks(image: Image.Image, inspection: Inspection, *, numbered: bool = True):
    painter = ImageDraw.Draw(image, "RGBA")
    face = font(11)
    for index, mark in enumerate(inspection.surface_review.marks, start=1):
        color = MARK_COLORS[mark.kind]
        points = [tuple(point) for point in plane_polygon(mark.points, inspection.plane)]
        painter.polygon(points, fill=(*color, 46))
        painter.line(points + [points[0]], fill=(*color, 225), width=2, joint="curve")
        if numbered:
            draw_tag(painter, (max(2, points[0][0]), max(0, points[0][1] - 14)),
                     f"S{index}", color, face)


def paint_support(image: Image.Image, surface: SurfacePlane):
    pixels = np.array(image, dtype=np.uint8)
    outside = ~surface.usable
    glare = surface.glare
    pixels[outside] = np.rint(pixels[outside] * 0.45 + np.array((58, 49, 84)) * 0.55).astype(np.uint8)
    pixels[glare] = np.rint(pixels[glare] * 0.35 + np.array((196, 138, 226)) * 0.65).astype(np.uint8)
    return Image.fromarray(pixels, mode="RGB")


def plane_native(engine: Engine, inspection: Inspection) -> tuple[Image.Image, SurfacePlane]:
    capture = selected_surface(inspection)
    surface = rectify_surface(engine, inspection, capture)
    image = surface_preview(surface, inspection.plane)
    image = paint_support(image, surface)
    paint_marks(image, inspection)
    return image, surface


def native_surface_marked(engine: Engine, inspection: Inspection) -> bytes:
    """The surface photo in its own sensor pixels with marks projected back onto it."""
    capture = selected_surface(inspection)
    surface = rectify_surface(engine, inspection, capture)
    base = engine.read_source(capture)
    values = base.pixels.astype(np.float32) / capture.white_level
    preview = grayscale_preview(
        values, np.ones(values.shape, dtype=bool), 0.0,
        max(0.03, float(np.percentile(values, 99))), 1.0,
    )
    painter = ImageDraw.Draw(preview, "RGBA")
    face = font(13)
    for index, mark in enumerate(inspection.surface_review.marks, start=1):
        polygon = plane_polygon(mark.points, inspection.plane)
        image_x, image_y = project(surface.matrix, polygon[:, 0], polygon[:, 1])
        points = list(zip(image_x, image_y))
        color = MARK_COLORS[mark.kind]
        painter.polygon(points, fill=(*color, 48))
        painter.line(points + [points[0]], fill=(*color, 235), width=3, joint="curve")
        draw_tag(painter, (max(3, points[0][0]), max(2, points[0][1] - 18)),
                 f"S{index} {mark.title[:18]}", color, face)
    corners = surface.placement.corners
    painter.line(list(corners) + [corners[0]], fill=(119, 247, 209, 210), width=2, joint="curve")
    return png_bytes(preview)


def cross_check_image(engine: Engine, inspection: Inspection, role: str,
                      panel: PanelImage) -> bytes:
    """Rectified EL plane with dark regions recolored by their surface verdict."""
    result = cross_check(engine, inspection, role)
    image = grayscale_preview(
        panel.signal, panel.valid, inspection.display.lower, inspection.display.upper,
        inspection.display.gamma,
    )
    painter = ImageDraw.Draw(image, "RGBA")
    face = font(10)
    for row in result["rows"]:
        x0, y0, x1, y1 = row["bounds"]
        color = VERDICT_COLORS[row["verdict"]]
        painter.rectangle((x0, y0, x1 - 1, y1 - 1), outline=(*color, 235), width=2)
        if x1 - x0 >= 30 and y1 - y0 >= 18:
            draw_tag(painter, (x0 + 3, y0 + 2), row["dark_region"].split("-")[-1], color, face)
    # Mark outlines are drawn subtly so the reviewer sees what drove each verdict.
    for mark in inspection.surface_review.marks:
        points = [tuple(point) for point in plane_polygon(mark.points, inspection.plane)]
        color = MARK_COLORS[mark.kind]
        painter.line(points + [points[0]], fill=(*color, 150), width=1, joint="curve")
    return png_bytes(image)


# --------------------------------------------------------------------------- #
# Review mutation helpers (used by the inspection service)
# --------------------------------------------------------------------------- #

def configure(inspection: Inspection, raw: dict) -> tuple[SurfaceReview, str, dict]:
    surface_id = raw.get("surface_id") or None
    if surface_id is not None:
        capture = inspection.capture(file_id(surface_id, "Surface image ID"))
        if capture.role != "surface":
            raise Issue("Select a surface photo for the cross-check.")
    tolerance = integer(raw.get("edge_tolerance_px", inspection.surface_review.edge_tolerance_px),
                        "Surface edge tolerance", 0, 40)
    note = text(raw.get("note", inspection.surface_review.note),
                "Surface review note", 2000, allow_blank=True)
    review = SurfaceReview(
        surface_id, tolerance, inspection.surface_review.marks,
        inspection.surface_review.verdicts, note, utcnow(),
    )
    return review, "surface review configured", {
        "surface": surface_id, "edge_tolerance_px": tolerance,
    }


def parse_mark(raw: dict) -> dict:
    return {
        "title": text(raw.get("title"), "Surface mark title", 100),
        "kind": choice(raw.get("kind", "smudge"), "Surface mark kind",
                       frozenset(MARK_COLORS)),
        "points": xy_list(raw.get("points"), "Surface mark polygon", 3, 48, (0, 1), (0, 1)),
        "note": text(raw.get("note", ""), "Surface mark note", 1200, allow_blank=True),
    }


def add_mark(inspection: Inspection, raw: dict) -> tuple[SurfaceReview, str, dict]:
    selected_surface(inspection)
    values = parse_mark(raw)
    if len(inspection.surface_review.marks) >= 60:
        raise Issue("A surface review holds up to 60 marks.", 409)
    mark = SurfaceMark(new_id(), values["title"], values["kind"], values["points"],
                       values["note"], utcnow(), utcnow())
    review = SurfaceReview(
        inspection.surface_review.surface_id, inspection.surface_review.edge_tolerance_px,
        (*inspection.surface_review.marks, mark), inspection.surface_review.verdicts,
        inspection.surface_review.note, utcnow(),
    )
    return review, "surface mark recorded", {"mark": mark.id, "kind": mark.kind}


def edit_mark(inspection: Inspection, mark_id: str, raw: dict) -> tuple[SurfaceReview, str, dict]:
    mark_id = file_id(mark_id, "Surface mark ID")
    current = next((item for item in inspection.surface_review.marks if item.id == mark_id), None)
    if current is None:
        raise Issue("This surface mark does not exist.", 404)
    values = parse_mark(raw)
    updated = SurfaceMark(current.id, values["title"], values["kind"], values["points"],
                          values["note"], current.created_at, utcnow())
    marks = tuple(updated if item.id == mark_id else item
                  for item in inspection.surface_review.marks)
    review = SurfaceReview(
        inspection.surface_review.surface_id, inspection.surface_review.edge_tolerance_px,
        marks, inspection.surface_review.verdicts, inspection.surface_review.note, utcnow(),
    )
    return review, "surface mark updated", {"mark": mark_id}


def remove_mark(inspection: Inspection, mark_id: str) -> tuple[SurfaceReview, str, dict]:
    mark_id = file_id(mark_id, "Surface mark ID")
    marks = tuple(item for item in inspection.surface_review.marks if item.id != mark_id)
    if len(marks) == len(inspection.surface_review.marks):
        raise Issue("This surface mark does not exist.", 404)
    review = SurfaceReview(
        inspection.surface_review.surface_id, inspection.surface_review.edge_tolerance_px,
        marks, inspection.surface_review.verdicts, inspection.surface_review.note, utcnow(),
    )
    return review, "surface mark withdrawn", {"mark": mark_id}


def set_verdict(inspection: Inspection, raw: dict) -> tuple[SurfaceReview, str, dict]:
    region = text(raw.get("dark_region"), "Dark region reference", 80)
    verdict = choice(raw.get("verdict", "auto"), "Surface verdict",
                     frozenset(("auto",)) | frozenset(VERDICT_COLORS))
    verdicts = dict(inspection.surface_review.verdicts)
    if verdict == "auto":
        verdicts.pop(region, None)
    else:
        verdicts[region] = verdict
    review = SurfaceReview(
        inspection.surface_review.surface_id, inspection.surface_review.edge_tolerance_px,
        inspection.surface_review.marks, verdicts, inspection.surface_review.note, utcnow(),
    )
    return review, "surface verdict saved", {"dark_region": region, "verdict": verdict}
