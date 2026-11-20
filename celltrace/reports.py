from base64 import b64encode
from html import escape
from hashlib import sha256

from .engine import Engine
from .findings import list_findings
from .inspection import Inspections
from .lot import source as lot_source
from .model import Inspection, Issue, ROLE_LABELS, choice, text, utcnow
from .packets import PacketWriter
from .render import RenderOptions, render_layer
from . import surface as surface_module
from .store import Store, data_digest, serial_json
from .tables import csv_data


REPORT_SCHEMA = "celltrace-issued-report-v1"
DISPOSITIONS = frozenset(("release", "hold", "reinspect"))
REPORT_KINDS = ("quality", "calibration", "intensity", "dark", "cracks", "strings")


def disposition_check(inspection, findings: list[dict]) -> tuple[list[str], list[str]]:
    stops = []
    messages = []
    if "high" not in inspection.active:
        stops.append("Select a high-current image to assign the evidence plane.")
    if not inspection.operator.strip():
        stops.append("Add the reviewing operator.")
    if any(row["source_status"] != "current" for row in findings):
        stops.append("Resolve findings whose calibrated source has changed.")
    if any(row["status"] == "open" for row in findings):
        messages.append("Open findings require hold or reinspection.")
    if any(capture.synthetic for capture in inspection.captures.values()):
        messages.append("Synthetic source material. For training and demonstration.")
    if "low" not in inspection.active:
        messages.append("The inspection has no low-current companion. The response section will be empty.")
    return stops, messages


def summarize_for_issue(inspection, engine: Engine, events: list[dict]) -> dict:
    findings = list_findings(inspection)
    blockers, messages = disposition_check(inspection, findings)
    sections = {}
    if "high" in inspection.active:
        for kind in REPORT_KINDS:
            result = engine.table(inspection, "high", kind)
            sections[kind] = {"summary": result["summary"], "records": len(result["rows"])}
        if sections["intensity"]["summary"]["eligible_cells"] == 0:
            blockers.append("At least one cell needs acceptable high-current image support.")
    if "high" in inspection.active and "low" in inspection.active:
        try:
            result = engine.table(inspection, "high", "response")
        except Issue as error:
            messages.append(f"Response evidence is unavailable. {error}")
        else:
            sections["response"] = {"summary": result["summary"], "records": len(result["rows"])}
    active = {role: capture_info(inspection.captures[identifier]) for role, identifier in inspection.active.items()}
    surface_crosscheck = surface_section(engine, inspection)
    return {
        "module": inspection.module,
        "lot": inspection.lot,
        "operator": inspection.operator,
        "notes": inspection.notes,
        "inspection": inspection.id,
        "review_revision": inspection.revision,
        "state": inspection.state,
        "plane": [inspection.plane.width, inspection.plane.height],
        "grid": [inspection.plane.rows, inspection.plane.cols],
        "capture_inventory": active,
        "findings": findings,
        "sections": sections,
        "surface_crosscheck": surface_crosscheck,
        "events": events[:14],
        "blockers": blockers,
        "messages": messages,
    }


def surface_section(engine: Engine, inspection: Inspection) -> dict | None:
    review = inspection.surface_review
    if review.surface_id is None:
        return None
    try:
        result = surface_module.cross_check(engine, inspection, "high")
    except Issue:
        return None
    return {
        "surface": result["surface"],
        "edge_tolerance_px": result["edge_tolerance_px"],
        "surface_coverage": result["surface_coverage"],
        "glare_pixels": result["glare_pixels"],
        "marks": result["marks"],
        "counts": result["counts"],
        "rows": result["rows"],
        "note": result["note"],
    }


def capture_info(capture):
    return {
        "id": capture.id, "name": capture.name, "digest": capture.digest,
        "current_a": capture.current_a, "exposure_ms": capture.exposure_ms,
        "bit_depth": capture.bit_depth, "white_level": capture.white_level,
        "width": capture.width, "height": capture.height, "synthetic": capture.synthetic,
    }


def finding_rows(findings: list[dict]) -> list[dict]:
    return [
        {**finding, "capture": finding["anchor"].get("image"), "capture_digest": finding["anchor"].get("digest")}
        for finding in findings
    ]


def report_documents(inspection, engine: Engine, preview: dict) -> tuple[dict, dict]:
    media = {
        "panel": render_layer(inspection, engine, "high", "panel", RenderOptions(True, True, True)),
        "quality": render_layer(inspection, engine, "high", "quality", RenderOptions(True, False)),
        "dark": render_layer(inspection, engine, "high", "dark", RenderOptions(True, False)),
        "cracks": render_layer(inspection, engine, "high", "cracks", RenderOptions(True, False)),
    }
    if "response" in preview["sections"]:
        media["response"] = render_layer(inspection, engine, "high", "response", RenderOptions(True, False))
    if preview.get("surface_crosscheck"):
        context = engine.panel_context(inspection, "high")
        media["surface"] = surface_module.cross_check_image(
            engine, inspection, "high", context.panel,
        )
    tables = {}
    for kind in preview["sections"]:
        measurement = engine.table(inspection, "high", kind)
        rows = measurement["rows"]
        if kind == "calibration":
            rows = [{"metric": name, "value": value} for name, value in measurement["summary"].items()]
        tables[kind] = csv_data(kind, rows)
    tables["findings"] = csv_data("findings", finding_rows(preview["findings"]))
    surface_review = preview.get("surface_crosscheck")
    if surface_review:
        tables["surface"] = csv_data("surface", [
            {key: value for key, value in row.items() if key != "bounds"}
            for row in surface_review["rows"]
        ])
    return media, tables


def issue_report(store: Store, engine: Engine, service: Inspections, identifier: str, expected, raw: dict):
    item = service.checked(identifier, expected)
    disposition = choice(raw.get("disposition"), "Inspection result", DISPOSITIONS)
    note = text(raw.get("decision_note", ""), "Decision note", 1400, allow_blank=True)
    preview = summarize_for_issue(item, engine, store.events(identifier))
    blockers = preview["blockers"]
    if disposition == "release" and any(finding.status in ("open", "hold") for finding in item.findings):
        blockers.append("Close outstanding findings or choose hold or reinspection.")
    if disposition != "release" and not note:
        blockers.append("Record a reason for hold or reinspection.")
    if blockers:
        raise Issue(" ".join(blockers), 409)
    media, tables = report_documents(item, engine, preview)
    current = utcnow()
    media_digests = {name: sha256(content).hexdigest() for name, content in media.items()}
    table_digests = {name: sha256(content).hexdigest() for name, content in tables.items()}
    payload = {
        "schema": REPORT_SCHEMA,
        "report": f"{item.id[:10]}-{item.revision + 1:03}",
        "issued_at": current,
        "disposition": disposition,
        "decision_note": note,
        "inspection": preview,
        "images": media_digests,
        "tables": table_digests,
        "config_digest": data_digest(item.to_dict()),
    }
    lot_readings = issued_lot_snapshot(item, engine, preview)
    lot_digest = store.blobs.put(serial_json(lot_readings))
    payload["lot_snapshot"] = lot_digest
    html = printable(payload, media)
    with PacketWriter() as packet:
        for name, content in media.items():
            packet.add(f"views/{name}.png", content, "image/png")
        for name, content in tables.items():
            packet.add(f"tables/{name}.csv", content, "text/csv")
        packet.add("report.html", html, "text/html")
        packet.add("views/lot-readings.json", serial_json(lot_readings), "application/json")
        archive = packet.finish(payload)
    archive_id = store.blobs.put(archive)
    html_id = store.blobs.put(html)
    for name, content in media.items():
        if store.blobs.put(content) != media_digests[name]:
            raise Issue("A report image failed its storage digest check.")
    item.issue = {
        "id": payload["report"], "at": current, "review_revision": item.revision,
        "disposition": disposition, "decision_note": note, "archive": archive_id, "html": html_id,
        "cover": media_digests["panel"], "media": media_digests, "table_digests": table_digests,
        "lot_snapshot": lot_digest,
    }
    item.state = "issued"
    return store.save(item, item.revision, "report issued", {
        "report": item.issue["id"], "disposition": disposition, "archive": archive_id,
    })


def issued_lot_snapshot(item, engine, preview):
    sections = preview["sections"]
    intensity = sections["intensity"]["summary"]
    return {
        "source": lot_source(item, sections["calibration"]["summary"]),
        "measurements": engine.table(item, "high", "intensity")["rows"],
        "summary": {
            "measurement": "issued evidence snapshot",
            "median": intensity["median"],
            "usable_cells": intensity["eligible_cells"],
            "total_cells": intensity["cells"],
            "crack_count": sections["cracks"]["summary"]["count"],
            "dark_count": sections["dark"]["summary"]["count"],
        },
    }


def printable(payload: dict, media: dict[str, bytes]) -> bytes:
    report = payload["inspection"]
    metadata = [
        ("Lot", report["lot"]),
        ("Operator", report["operator"]),
        ("Issued UTC", payload["issued_at"]),
        ("Review revision", report["review_revision"]),
        ("Plane", " × ".join(map(str, report["plane"])) + " px"),
        ("Cells", " × ".join(map(str, report["grid"]))),
    ]
    decisions = [
        ("Result", payload["disposition"].replace("_", " ").title()),
        ("Decision note", payload["decision_note"]),
        ("Review notes", report["notes"]),
    ]
    files = [
        [ROLE_LABELS[role], cap["name"], f'{cap["width"]} × {cap["height"]}',
         f'{cap["bit_depth"]} bit', f'{cap["exposure_ms"]} ms', shown(cap["current_a"]), cap["digest"]]
        for role, cap in report["capture_inventory"].items()
    ]
    findings = [
        [row["title"], row["cell"], row["category"], row["status"], row["source_role"], row["note"]]
        for row in report["findings"]
    ]
    surface = report.get("surface_crosscheck")
    surface_media = media["surface"] if surface and "surface" in media else None
    surface_rows = []
    if surface:
        surface_rows = [
            [row["dark_region"], row["cell"], row["dark_pixels"],
             f'{row["cover_fraction"]:.3f}', f'{row["support_fraction"]:.3f}',
             row["verdict"], "yes" if row["manual_override"] else "auto"]
            for row in surface["rows"]
        ]
    highlights = report_highlights(report["sections"])
    origins = [
        [row["revision"], row["at"], row["action"]]
        for row in report["events"]
    ]
    pictures = ""
    for kind, raw in media.items():
        image = b64encode(raw).decode("ascii")
        pictures += f'<figure><img alt="{escape(image_title(kind))}" src="data:image/png;base64,{image}"><figcaption>{escape(image_title(kind))}</figcaption></figure>'
    body = f"""
      <header>
        <p class="eyebrow">CellTrace EL / Inspection record</p>
        <h1>{escape(report["module"])}</h1>
        <p>Record {escape(payload["report"])}</p>
      </header>
      {definition_list(metadata)}
      <section><h2>Disposition</h2>{definition_list(decisions)}{warnings(report["messages"])}</section>
      <section><h2>Image evidence</h2><div class="figures">{pictures}</div></section>
      <section><h2>Measurements</h2>{definition_list(highlights)}</section>
      <section><h2>Reviewed findings</h2>
       {html_table(("Observation", "Cell", "Category", "State", "Source", "Note"), findings)}
      </section>
      {surface_html(surface, surface_media, surface_rows)}
      <section><h2>Capture lineage</h2>
       {html_table(("Role", "Image", "Native size", "Samples", "Exposure", "Current A", "SHA-256"), files)}
      </section>
      <section><h2>Recent edits</h2>{html_table(("Revision", "UTC", "Event"), origins)}</section>
    """
    document = f"""<!doctype html><html lang="en"><meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>CellTrace EL {escape(report["module"])}</title>
    <style>{REPORT_CSS}</style><body><main>{body}</main></body></html>"""
    return document.encode()


def image_title(kind):
    return {
        "panel": "Grid with reviewed findings", "quality": "Usable coverage and image-quality flags",
        "dark": "Connected dark evidence", "cracks": "Thin dark-line candidates",
        "response": "Paired-current relative response",
        "surface": "EL dark regions labeled by surface cross-check verdict",
    }[kind]


def surface_html(surface, media_png, rows):
    if not surface:
        return ""
    picture = ""
    if media_png is not None:
        picture = f'<figure><img alt="Surface cross-check" src="data:image/png;base64,{b64encode(media_png).decode("ascii")}"><figcaption>Surface cross-check verdicts on the EL plane</figcaption></figure>'
    counts = surface["counts"]
    summary = ", ".join(f"{label}: {counts.get(key, 0)}" for key, label in (
        ("occluded", "occluded"), ("partial", "partial"), ("edge-uncertain", "edge uncertain"),
        ("obscured-in-surface", "obscured in surface"), ("no-surface-evidence", "no surface evidence"),
    ))
    marks = ", ".join(mark["title"] for mark in surface["marks"]) or "none"
    return f"""
      <section><h2>Surface photo cross-check</h2>
       {definition_list([
           ("Surface photo", surface["surface"]["name"]),
           ("Surface coverage", f'{surface["surface_coverage"]:.1%}'),
           ("Edge tolerance", f'{surface["edge_tolerance_px"]} plane px'),
           ("Contamination marks", marks),
           ("Verdict counts", summary),
           ("Review note", surface["note"] or "—"),
       ])}
       <div class="figures">{picture}</div>
       {html_table(("Dark region", "Cell", "Pixels", "Mark cover", "Surface support", "Verdict", "Source"), rows)}
      </section>"""


def report_highlights(sections):
    metrics = (
        ("intensity", "eligible_cells", "Cells with supported intensity"),
        ("quality", "clipped_pixels", "Clipped plane pixels"),
        ("quality", "calibration_gap_pixels", "Plane pixels with invalid reference"),
        ("dark", "count", "Dark regions"),
        ("dark", "dark_fraction", "Dark fraction of measured area"),
        ("cracks", "count", "Trace candidates"),
        ("cracks", "trace_length_mm", "Candidate trace length in mm"),
        ("strings", "groups_with_flags", "Strings with review flags"),
        ("response", "weak_cells", "Cells with low relative response"),
    )
    return [
        (label, sections[kind]["summary"][metric])
        for kind, metric, label in metrics if kind in sections
    ]


def shown(value):
    return "—" if value in (None, "") else str(value)


def definition_list(values) -> str:
    body = "".join(
        f"<div><dt>{escape(key)}</dt><dd>{escape(shown(value))}</dd></div>" for key, value in values
    )
    return f'<dl>{body}</dl>'


def warnings(values) -> str:
    if not values:
        return ""
    return "<ul>" + "".join(f"<li>{escape(shown(value))}</li>" for value in values) + "</ul>"


def html_table(headers, rows) -> str:
    if not rows:
        return "<p>No entries.</p>"
    labels = "".join(f"<th>{escape(label)}</th>" for label in headers)
    body = "".join("<tr>" + "".join(f"<td>{escape(shown(value))}</td>" for value in row) + "</tr>" for row in rows)
    return f"<table><thead><tr>{labels}</tr></thead><tbody>{body}</tbody></table>"


REPORT_CSS = """
* { box-sizing: border-box; }
body { background: #edf2f1; margin: 0; color: #172c32; font: 15px/1.45 system-ui,sans-serif; }
main { max-width: 1120px; margin: 0 auto; padding: 44px; background: #fff; }
header { padding-bottom: 17px; border-bottom: 3px solid #3d8d7c; margin-bottom: 24px; }
h1 { font-size: 44px; margin: 0; }
h2 { margin: 30px 0 12px; font-size: 22px; }
.eyebrow { text-transform: uppercase; color: #398070; letter-spacing: .1em; font-weight: 700; }
dl { display: grid; grid-template-columns: repeat(auto-fit,minmax(165px,1fr)); gap: 9px; }
dl div { background: #f0f5f4; border-radius: 5px; padding: 10px; }
dt { color: #4b696b; font-size: 12px; }
dd { margin: 0; white-space: pre-wrap; overflow-wrap: anywhere; }
.figures { display: grid; grid-template-columns: repeat(2, 1fr); gap: 12px; }
figure { margin: 0; break-inside: avoid; }
figure img { width: 100%; height: auto; display: block; }
figure figcaption { padding: 8px 0; color: #4b696b; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th,td { padding: 7px; text-align: left; border-bottom: 1px solid #dce8e5; overflow-wrap: anywhere; }
th { color: #285f56; }
th:last-child,td:last-child { max-width: 180px; }
@media (max-width: 720px) { main {padding: 20px} .figures {grid-template-columns: 1fr} }
@media print { body {background: white} main {padding: 0} header {margin-bottom: 12px} h2 {break-after: avoid} }
"""
