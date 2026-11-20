"""Recipe-level brightness baselines built from confirmed-passing high-current inspections.

The baseline deliberately keeps two complementary views per cell position:

* absolute level statistics on calibrated signal, so a uniformly dim panel cannot hide behind
  its own mean, and
* panel-relative level statistics, which describe the normal brightness *shape* of the recipe
  (for example the two columns beside the junction box that always read lower).

Construction rejects individual outlier samples per position with a robust z score, so one
abnormal reference panel cannot move the limits. Positions seen by too few panels are kept but
marked as limited evidence, and the panels actually contributing to each position are listed.
"""
from dataclasses import dataclass

import numpy as np

from .baseline_model import (
    MAX_BASELINE_SAMPLES, MIN_FIRM_SAMPLES, OUTLIER_Z, BaselineCell, BaselineSource, BrightnessBaseline,
)
from .engine import Engine
from .model import Inspection, Issue, cell_id, real, sequence, text, utcnow
from .baseline_store import BaselineStore


@dataclass(frozen=True)
class SampleReadings:
    inspection: Inspection
    capture_id: str
    digest: str
    rows: list[dict]
    panel_median: float
    eligible_cells: int


def validate_grid(inspection: Inspection, rows: int, cols: int):
    if (inspection.plane.rows, inspection.plane.cols) != (rows, cols):
        raise Issue(
            f"{inspection.module} uses a {inspection.plane.rows}×{inspection.plane.cols} grid; "
            f"the baseline needs {rows}×{cols}."
        )


def read_sample(engine: Engine, inspection: Inspection) -> SampleReadings:
    if "high" not in inspection.active:
        raise Issue(f"{inspection.module} has no selected high-current capture.")
    table = engine.table(inspection, "high", "intensity")
    rows = table["rows"]
    panel_median = table["summary"]["median"]
    eligible = table["summary"]["eligible_cells"]
    if panel_median is None or panel_median <= 0:
        raise Issue(f"{inspection.module} has no usable high-current cell readings.")
    if eligible < max(1, len(rows) // 2):
        raise Issue(f"{inspection.module} has too few supported cells ({eligible}) to anchor a baseline.")
    capture = inspection.selected("high")
    return SampleReadings(inspection, capture.id, capture.digest, rows, float(panel_median), int(eligible))


def source_from_sample(sample: SampleReadings, inspection: Inspection) -> BaselineSource:
    capture = inspection.capture(sample.capture_id)
    return BaselineSource(
        inspection_id=inspection.id,
        module=inspection.module,
        lot=inspection.lot,
        capture_id=capture.id,
        digest=capture.digest,
        issued_at=inspection.issue["at"] if inspection.issue else inspection.updated_at,
        current_a=capture.current_a,
        exposure_ms=capture.exposure_ms,
        bit_depth=capture.bit_depth,
        white_level=capture.white_level,
        dark_id=inspection.calibration.dark_id,
        flat_id=inspection.calibration.flat_id,
        panel_median=round(sample.panel_median, 6),
        eligible_cells=sample.eligible_cells,
        synthetic=capture.synthetic,
    )


def acquisition_settings(samples: list[SampleReadings], inspections: dict[str, Inspection]) -> dict:
    currents = sorted({inspections[s.inspection.id].capture(s.capture_id).current_a
                       for s in samples if inspections[s.inspection.id].capture(s.capture_id).current_a is not None})
    exposures = sorted({round(inspections[s.inspection.id].capture(s.capture_id).exposure_ms, 4)
                        for s in samples})
    depths = sorted({inspections[s.inspection.id].capture(s.capture_id).bit_depth for s in samples})
    whites = sorted({inspections[s.inspection.id].capture(s.capture_id).white_level for s in samples})
    return {
        "current_a": currents,
        "exposure_ms": exposures,
        "bit_depth": depths,
        "white_level": whites,
        "dark_reference_used": any(inspections[s.inspection.id].calibration.dark_id for s in samples),
        "flat_reference_used": any(inspections[s.inspection.id].calibration.flat_id for s in samples),
        "role": "high",
        "signal": "calibrated panel cell medians (fraction of declared white level)",
    }


def _robust_stats(values: list[float]) -> tuple[float, float]:
    array = np.asarray(values, dtype=np.float64)
    median = float(np.median(array))
    mad = float(np.median(np.abs(array - median)))
    return median, mad


def build_cells(
    rows: int, cols: int, samples: list[SampleReadings], modules: list[str]
) -> tuple[list[BaselineCell], dict]:
    cells: list[BaselineCell] = []
    rejection_notes = []
    total_rejected = 0
    for r in range(rows):
        for c in range(cols):
            identifier = cell_id(r, c)
            absolute = []
            relative = []
            contributors = []
            for sample, module in zip(samples, modules):
                row = next((item for item in sample.rows if item["cell"] == identifier), None)
                if row is None or row["median"] is None or not row["reliable"]:
                    continue
                median = float(row["median"])
                absolute.append(median)
                relative.append(median / sample.panel_median)
                contributors.append(module)
            if not absolute:
                cells.append(BaselineCell(
                    cell=identifier, row=r, col=c, samples=0, median=None, mad=None, low=None,
                    high=None, relative_median=None, relative_mad=None, absolute_median=None,
                    evidence="unsupported", rejected=0, source_modules=(),
                ))
                continue
            abs_median, abs_mad = _robust_stats(absolute)
            rel_median, rel_mad = _robust_stats(relative)
            # Robust outlier rejection on the panel-relative shape (scale 1.4826 makes MAD a sigma estimate).
            kept_abs, kept_rel, kept_modules = [], [], []
            rejected = 0
            sigma = max(rel_mad * 1.4826, 1e-4)
            for value_abs, value_rel, module in zip(absolute, relative, contributors):
                if abs(value_rel - rel_median) / sigma > OUTLIER_Z and len(absolute) >= MIN_FIRM_SAMPLES:
                    rejected += 1
                    rejection_notes.append({
                        "cell": identifier, "module": module,
                        "relative": round(value_rel, 4), "relative_median": round(rel_median, 4),
                    })
                    continue
                kept_abs.append(value_abs)
                kept_rel.append(value_rel)
                kept_modules.append(module)
            if kept_abs:
                abs_median, abs_mad = _robust_stats(kept_abs)
                rel_median, rel_mad = _robust_stats(kept_rel)
            total_rejected += rejected
            evidence = "firm" if len(kept_abs) >= MIN_FIRM_SAMPLES else "limited"
            cells.append(BaselineCell(
                cell=identifier, row=r, col=c,
                samples=len(kept_abs),
                median=round(rel_median, 6),
                mad=round(rel_mad, 6),
                low=round(float(min(kept_rel)), 6),
                high=round(float(max(kept_rel)), 6),
                relative_median=round(rel_median, 6),
                relative_mad=round(rel_mad, 6),
                absolute_median=round(abs_median, 6),
                evidence=evidence,
                rejected=rejected,
                source_modules=tuple(kept_modules),
            ))
    diagnostics = {
        "outlier_z": OUTLIER_Z,
        "rejected_samples": total_rejected,
        "rejections": rejection_notes[:40],
        "firm_positions": sum(cell.evidence == "firm" for cell in cells),
        "limited_positions": sum(cell.evidence == "limited" for cell in cells),
        "unsupported_positions": sum(cell.evidence == "unsupported" for cell in cells),
    }
    return cells, diagnostics


def build_baseline(
    store: BaselineStore,
    engine: Engine,
    *,
    recipe: str,
    name: str,
    inspection_ids: list[str],
    note: str,
    z_limit: float,
    low_ratio: float,
    global_dim_ratio: float,
) -> tuple[BrightnessBaseline, dict]:
    recipe = text(recipe, "Recipe name", 80)
    name = text(name, "Baseline name", 100)
    note = text(note, "Baseline note", 2000, allow_blank=True)
    ids = sequence(inspection_ids, "Reference inspections", 2, MAX_BASELINE_SAMPLES)
    z_limit = real(z_limit, "Position z limit", 1.0, 8.0)
    low_ratio = real(low_ratio, "Position low ratio", 0.3, 1.0)
    global_dim_ratio = real(global_dim_ratio, "Global dim ratio", 0.3, 1.0)

    inspections: dict[str, Inspection] = {}
    seen = set()
    for identifier in ids:
        if identifier in seen:
            raise Issue("Each reference inspection can only be added once.")
        seen.add(identifier)
        inspection = store.store.get(identifier)
        if inspection.state != "issued":
            raise Issue(
                f"{inspection.module} is not an issued, confirmed-passing inspection. "
                "Issue its report before using it as a brightness reference."
            )
        inspections[identifier] = inspection

    samples = [read_sample(engine, item) for item in inspections.values()]
    grid_owner = next(iter(inspections.values()))
    rows, cols = grid_owner.plane.rows, grid_owner.plane.cols
    for inspection in inspections.values():
        validate_grid(inspection, rows, cols)

    modules = [inspections[s.inspection.id].module for s in samples]
    cells, diagnostics = build_cells(rows, cols, samples, modules)
    if not any(cell.samples > 0 for cell in cells):
        raise Issue("The selected inspections share no supported cell positions.")
    if diagnostics["firm_positions"] == 0:
        raise Issue(
            f"Every position is backed by fewer than {MIN_FIRM_SAMPLES} panels. "
            "Add more confirmed inspections before publishing a baseline."
        )

    version = store.next_version(recipe)
    now = utcnow()
    baseline = BrightnessBaseline(
        id=store.new_id(),
        name=name,
        recipe=recipe,
        version=version,
        state="active",
        rows=rows,
        cols=cols,
        z_limit=z_limit,
        low_ratio=low_ratio,
        global_dim_ratio=global_dim_ratio,
        created_at=now,
        updated_at=now,
        cells=tuple(cells),
        sources=tuple(source_from_sample(sample, inspections[sample.inspection.id]) for sample in samples),
        acquisition=acquisition_settings(samples, inspections),
        note=note,
    )
    details = {
        "recipe": recipe, "version": version, "samples": len(samples),
        "modules": modules, "diagnostics": diagnostics,
    }
    saved = store.save_new(baseline, "brightness baseline published", details)
    return saved, details


# ---------------------------------------------------------------------------------------------
# Comparison of a new inspection against a published baseline
# ---------------------------------------------------------------------------------------------

def _status(relative: float, cell: BaselineCell, z_limit: float, low_ratio: float) -> tuple[str, list[str]]:
    reasons = []
    if cell.evidence == "unsupported" or cell.relative_median is None:
        return "no-evidence", ["No supported reference readings at this position."]
    sigma = max((cell.relative_mad or 0.0) * 1.4826, 1e-4)
    z = (relative - cell.relative_median) / sigma
    if relative < cell.relative_median * low_ratio:
        reasons.append(f"relative level {relative:.3f} under low-ratio floor {cell.relative_median * low_ratio:.3f}")
    if z < -z_limit:
        reasons.append(f"robust z {z:.2f} below limit -{z_limit:.1f}")
    if reasons:
        return "out-of-family", reasons
    if cell.evidence == "limited":
        return "limited-ok", [f"Limited evidence: {cell.samples} reference panel(s) at this position."]
    return "in-family", []


def compare_inspection(
    engine: Engine, baseline: BrightnessBaseline, inspection: Inspection
) -> dict:
    validate_grid(inspection, baseline.rows, baseline.cols)
    sample = read_sample(engine, inspection)
    cell_index = {row["cell"]: row for row in sample.rows}
    reference = baseline.cell_map()

    results = []
    out_of_family = 0
    limited = 0
    no_evidence = 0
    row_buckets: dict[int, list[dict]] = {}
    col_buckets: dict[int, list[dict]] = {}

    for cell in baseline.cells:
        observed = cell_index.get(cell.cell)
        if observed is None or observed["median"] is None or not observed["reliable"]:
            status = "not-measured"
            reasons = ["The new panel has no supported reading at this position."]
            relative = None
            absolute = None
            z = None
        else:
            median = float(observed["median"])
            relative = median / sample.panel_median
            absolute = median / cell.absolute_median if cell.absolute_median else None
            sigma = max((cell.relative_mad or 0.0) * 1.4826, 1e-4)
            z = round((relative - (cell.relative_median or relative)) / sigma, 3)
            status, reasons = _status(relative, cell, baseline.z_limit, baseline.low_ratio)
        entry = {
            "cell": cell.cell,
            "row": cell.row,
            "col": cell.col,
            "status": status,
            "evidence": cell.evidence,
            "reference_samples": cell.samples,
            "reference_modules": list(cell.source_modules),
            "observed_median": observed["median"] if observed else None,
            "observed_relative": round(relative, 6) if relative is not None else None,
            "reference_relative_median": cell.relative_median,
            "reference_relative_mad": cell.relative_mad,
            "absolute_ratio": round(absolute, 6) if absolute is not None else None,
            "z": z,
            "support": observed["support"] if observed else None,
            "reasons": reasons,
        }
        results.append(entry)
        row_buckets.setdefault(cell.row, []).append(entry)
        col_buckets.setdefault(cell.col, []).append(entry)
        if status == "out-of-family":
            out_of_family += 1
        elif status in ("limited-ok",):
            limited += 1
        elif status == "no-evidence":
            no_evidence += 1

    # Absolute panel-level check: preserves the fact that the whole panel is dimmer than the recipe.
    reference_abs = [cell.absolute_median for cell in baseline.cells
                     if cell.absolute_median is not None and cell.evidence != "unsupported"]
    panel_absolute = float(sample.panel_median / float(np.median(reference_abs))) if reference_abs else None
    global_dim = panel_absolute is not None and panel_absolute < baseline.global_dim_ratio

    # Row/column bands: a whole row uniformly weak must surface even when shape looks plausible.
    bands = []
    for axis_name, buckets in (("row", row_buckets), ("column", col_buckets)):
        for index, entries in sorted(buckets.items()):
            measured = [e for e in entries if e["absolute_ratio"] is not None]
            if len(measured) < max(2, len(entries) // 2):
                continue
            band_ratio = float(np.median([e["absolute_ratio"] for e in measured]))
            shape_ratio = float(np.median([
                e["observed_relative"] / e["reference_relative_median"]
                for e in measured if e["reference_relative_median"]
            ]))
            weak_fraction = sum(e["status"] == "out-of-family" for e in measured) / len(measured)
            flagged = band_ratio < baseline.global_dim_ratio and weak_fraction >= 0.5
            if flagged:
                bands.append({
                    "axis": axis_name,
                    "index": index + 1,
                    "label": f"{'R' if axis_name == 'row' else 'C'}{index + 1:02d}",
                    "absolute_ratio": round(band_ratio, 4),
                    "shape_ratio": round(shape_ratio, 4),
                    "weak_fraction": round(weak_fraction, 3),
                    "cells": [e["cell"] for e in measured],
                })

    messages = []
    if global_dim:
        messages.append(
            f"Whole-panel brightness is {panel_absolute:.3f} of the recipe reference "
            f"(global dim ratio {baseline.global_dim_ratio:.2f}). This is an absolute level change, "
            "not normalized away."
        )
    for band in bands:
        messages.append(
            f"{band['label']} {band['axis']} is uniformly weak: {band['weak_fraction'] * 100:.0f}% of its "
            f"cells out of family, absolute ratio {band['absolute_ratio']:.3f}."
        )
    if limited:
        messages.append(f"{limited} position(s) rely on limited evidence; sample panels are listed per cell.")
    if no_evidence:
        messages.append(f"{no_evidence} position(s) have no supported reference readings.")

    return {
        "baseline": {
            "id": baseline.id, "name": baseline.name, "recipe": baseline.recipe,
            "version": baseline.version, "digest": baseline.digest,
            "z_limit": baseline.z_limit, "low_ratio": baseline.low_ratio,
            "global_dim_ratio": baseline.global_dim_ratio,
        },
        "inspection": inspection.id,
        "module": inspection.module,
        "grid": [baseline.rows, baseline.cols],
        "panel": {
            "observed_median": round(sample.panel_median, 6),
            "absolute_ratio": round(panel_absolute, 6) if panel_absolute is not None else None,
            "global_dim": bool(global_dim),
            "eligible_cells": sample.eligible_cells,
        },
        "counts": {
            "cells": len(results),
            "out_of_family": out_of_family,
            "limited_evidence": limited,
            "no_evidence": no_evidence,
            "not_measured": sum(e["status"] == "not-measured" for e in results),
            "in_family": sum(e["status"] == "in-family" for e in results),
        },
        "bands": bands,
        "messages": messages,
        "rows": results,
        "acquisition": baseline.acquisition,
        "compared_at": utcnow(),
    }
