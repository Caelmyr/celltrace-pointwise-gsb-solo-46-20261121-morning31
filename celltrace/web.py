from dataclasses import asdict
from hashlib import sha256
from pathlib import Path

from flask import Flask, Response, jsonify, request, send_from_directory
from werkzeug.exceptions import BadRequest, HTTPException, RequestEntityTooLarge

from .banding import capture_reference_profile, profile_summary, reference_band_image
from .baseline import build_baseline, compare_inspection
from .baseline_render import baseline_canvas, comparison_canvas, overlay_canvas
from .baseline_store import BaselineStore, summarize as baseline_summary
from .engine import EL_LAYERS, MEASUREMENT_PLANES, Engine
from .evidence import evidence_response
from .exchange import create_exchange, import_exchange, preview_exchange
from .geometry import cells_for
from .images import png_bytes
from .inspection import Inspections, inspection_payload
from .lot import LotReview
from .model import (
    CAPTURE_ROLES, MAX_PACKAGE_BYTES, MAX_UPLOAD_BYTES, SENSOR_ROLES, Issue,
    choice, integer, mapping, parse_cell, sequence, text,
)
from .pair_views import full_window, pair_result, region_from_body
from .placement_check import paint_placement_diagnostics, placement_report, placement_rows
from .plots import reference_plot, transect_plot
from .raster_export import float_plane, grid_contact_sheet, write_cell_bundle
from .render import (
    RenderOptions, cell_crop, placement_preview, polygon_preview, render_layer, source_preview,
)
from .reference_review import compare_canvas, compare_references, reference_result
from .reports import issue_report, summarize_for_issue
from .store import Store
from .tables import csv_data


ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"


def json_body():
    value = request.get_json()
    return mapping(value, "Request")


def checked_revision(value):
    return integer(value, "Revision", 0, 10**9)


def request_file(limit: int):
    document = request.files.get("file")
    if document is None or not document.filename:
        raise Issue("Select a file first.")
    content = document.stream.read(limit + 1)
    if not content or len(content) > limit:
        raise Issue("The selected file exceeds the upload limit or is empty.", 413)
    return document.filename, content


def plain_filename(value: str) -> str:
    fallback = "".join(char if char.isascii() and (char.isalnum() or char in " ._-") else "_" for char in value)
    return fallback[:130].strip(" .") or "celltrace-file"


def content_response(data: bytes, mime: str, download: str | None = None) -> Response:
    response = Response(data, mimetype=mime)
    if download:
        response.headers["Content-Disposition"] = f'attachment; filename="{plain_filename(download)}"'
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


def on(key: str, default=False) -> bool:
    return request.args.get(key, "1" if default else "0") == "1"


def get_role():
    return choice(request.args.get("role", "high"), "EL capture role", SENSOR_ROLES)


def file_version(item):
    response = jsonify(inspection_payload(item))
    response.headers["ETag"] = f'"{item.id}-{item.revision}"'
    return response


def build_app(data_dir: str) -> Flask:
    app = Flask(__name__, static_folder=str(STATIC), static_url_path="/static")
    app.config.update(
        MAX_CONTENT_LENGTH=66 * 1024 * 1024,
        MAX_FORM_MEMORY_SIZE=512 * 1024,
        MAX_FORM_PARTS=24,
        JSON_SORT_KEYS=False,
    )
    app.json.sort_keys = False
    store = Store(data_dir)
    engine = Engine(store)
    service = Inspections(store)
    lot = LotReview(store, engine)
    baselines = BaselineStore(store)

    @app.errorhandler(Issue)
    def error_response(error):
        return jsonify(error=str(error)), error.status

    @app.errorhandler(RequestEntityTooLarge)
    def oversized(error):
        return jsonify(error="The request exceeds the upload limit."), 413

    @app.errorhandler(HTTPException)
    def http_error(error):
        if error.code == 415:
            return jsonify(error="This endpoint expects a JSON object."), 415
        if isinstance(error, BadRequest):
            return jsonify(error="The request body could not be parsed."), 400
        return jsonify(error=error.description), error.code

    @app.after_request
    def protect_local_views(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; "
            "script-src 'self'; connect-src 'self'; object-src 'none'; frame-ancestors 'none'"
        )
        if request.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/")
    def index():
        return send_from_directory(STATIC, "index.html")

    @app.get("/api/health")
    def health():
        return jsonify(status="ok" if store.ping() else "unavailable", product="CellTrace EL")

    @app.get("/api/inspections")
    def list_cases():
        query = text(request.args.get("q", ""), "Search", 80, allow_blank=True)
        label = text(request.args.get("lot", ""), "Production lot", 80, allow_blank=True)
        return jsonify(store.list(query, label))

    @app.post("/api/inspections")
    def new():
        return file_version(service.create(json_body()))

    @app.post("/api/demo/<variant>")
    def demo(variant):
        with engine.slots:
            return file_version(service.demo(variant))

    @app.get("/api/inspections/<identifier>")
    def inspect(identifier):
        return file_version(store.get(identifier))

    @app.get("/api/inspections/<identifier>/events")
    def history(identifier):
        inspection = store.get(identifier)
        return jsonify(inspection=inspection.id, events=store.events(inspection.id))

    @app.post("/api/inspections/<identifier>/copy")
    def copy_case(identifier):
        return file_version(service.clone(identifier, json_body()))

    @app.patch("/api/inspections/<identifier>/<operation>")
    def edit(identifier, operation):
        request_data = json_body()
        return file_version(service.change(identifier, request_data.get("revision"), operation, request_data))

    @app.post("/api/inspections/<identifier>/captures")
    def upload(identifier):
        name, raw = request_file(MAX_UPLOAD_BYTES)
        role = choice(request.form.get("role", "high"), "Image role", CAPTURE_ROLES)
        with engine.slots:
            saved = service.upload(identifier, request.form.get("revision"), role, name, raw, request.form)
        return file_version(saved)

    @app.delete("/api/inspections/<identifier>/captures/<capture_id>")
    def detach_image(identifier, capture_id):
        return file_version(service.remove_capture(identifier, json_body().get("revision"), capture_id))

    @app.get("/api/inspections/<identifier>/captures/<capture_id>/<view>")
    def capture(identifier, capture_id, view):
        choice(view, "Original view", frozenset(("raw", "preview", "corners", "stats", "thumbnail")))
        item = store.get(identifier)
        chosen = item.capture(capture_id)
        if view == "raw":
            raw = store.blobs.read(chosen.digest)
            if sha256(raw).hexdigest() != chosen.digest:
                raise Issue("Capture bytes failed verification.", 409)
            return content_response(raw, "image/png" if chosen.format == "PNG" else "image/tiff", chosen.name)
        with engine.slots:
            if view == "stats":
                return jsonify(engine.capture_stats(item, capture_id))
            if view == "corners":
                image = placement_preview(engine, item, chosen)
            elif view == "thumbnail":
                image = source_preview(engine, chosen, long_edge=260, highlight=True)
            else:
                image = source_preview(engine, chosen, long_edge=1500, highlight=True)
            return content_response(png_bytes(image), "image/png")

    @app.get("/api/inspections/<identifier>/captures/<capture_id>/reference/<axis>/<view>")
    def reference(identifier, capture_id, axis, view):
        choice(axis, "Sensor axis", frozenset(("rows", "columns")))
        choice(view, "Reference evidence view", frozenset(("data", "image", "plot")))
        item = store.get(identifier)
        frame = item.capture(capture_id)
        with engine.slots:
            key = ("reference-banding", frame.role, frame.digest, frame.white_level, axis)
            result = engine.measurements.get(
                key, lambda: capture_reference_profile(frame, engine.read_source(frame).pixels, axis),
            )
            if view == "image":
                return content_response(reference_band_image(engine, frame, result), "image/png")
            model = profile_summary(result)
            if view == "plot":
                return content_response(reference_plot(model), "image/png")
            return jsonify(model)

    @app.get("/api/inspections/<identifier>/reference-comparison/<view>")
    def reference_comparison(identifier, view):
        choice(view, "Reference comparison result", frozenset(("data", "image", "csv")))
        item = store.get(identifier)
        bins = integer(request.args.get("tiles", "6"), "Grid size", 2, 12)
        prior = request.args.get("before")
        next_image = request.args.get("after")
        with engine.slots:
            output, sources = compare_references(engine, item, prior, next_image, bins)
            if view == "image":
                return content_response(compare_canvas(output, sources), "image/png")
        if view == "csv":
            return content_response(csv_data("reference", output.tiles), "text/csv", f"{item.module}-reference.csv")
        return jsonify(reference_result(output, sources, bins))

    @app.route("/api/inspections/<identifier>/pair/<fmt>", methods=("GET", "POST"))
    def pair(identifier, fmt):
        item = store.get(identifier)
        choice(fmt, "Paired view result", frozenset(("png", "data")))
        body = json_body() if request.method == "POST" else None
        window = region_from_body(item, body) if body is not None else full_window(item)
        mode = choice(request.args.get("mode", "side"), "Paired view", frozenset(("side", "checker", "wipe")))
        contrast = choice(request.args.get("contrast", "shared"), "Image scaling", frozenset(("shared", "individual")))
        with engine.slots:
            values = pair_result(
                engine, item, window, fmt, mode, contrast,
                request.args.get("gamma", "0.8"), request.args.get("wipe", "0.5"),
                request.args.get("tile", "0.16"), on("grid", True),
            )
        if fmt == "data":
            return jsonify(values)
        return content_response(values, "image/png")

    @app.get("/api/inspections/<identifier>/layer/<kind>.png")
    def layer(identifier, kind):
        item = store.get(identifier)
        selected = request.args.get("cell")
        if selected:
            parse_cell(selected, item.plane.rows, item.plane.cols)
        options = RenderOptions(on("grid"), on("labels"), on("findings"), selected)
        with engine.slots:
            output = render_layer(item, engine, get_role(), kind, options)
        filename = f"{item.module}-{kind}.png" if on("download") else None
        return content_response(output, "image/png", filename)

    @app.get("/api/inspections/<identifier>/measure/<kind>")
    def measure(identifier, kind):
        choice(kind, "Measurement", MEASUREMENT_PLANES)
        item = store.get(identifier)
        with engine.slots:
            values = engine.table(item, get_role(), kind)
        if request.args.get("format") == "csv":
            rows = values["rows"]
            if kind == "calibration":
                rows = [{"metric": key, "value": value} for key, value in values["summary"].items()]
            return content_response(csv_data(kind, rows), "text/csv", f"{item.module}-{kind}.csv")
        return jsonify(values)

    @app.get("/api/inspections/<identifier>/placement-check")
    def inspect_placement(identifier):
        item = store.get(identifier)
        with engine.slots:
            ctx = engine.panel_context(item, get_role())
            check, report = placement_report(ctx, item.plane, item.revision)
            if request.args.get("format") == "csv":
                content = csv_data("placement", placement_rows(check))
                return content_response(content, "text/csv", f"{item.module}-placement.csv")
            if request.args.get("format") == "png":
                content = png_bytes(paint_placement_diagnostics(ctx.panel, item.display, check))
                return content_response(content, "image/png")
        return jsonify(report)

    @app.get("/api/inspections/<identifier>/pixel")
    def pixel(identifier):
        item = store.get(identifier)
        with engine.slots:
            result = engine.pixel(item, get_role(), request.args.get("x"), request.args.get("y"), on("pair"))
        return jsonify(result)

    @app.get("/api/inspections/<identifier>/cells/<cell_id>")
    def cell(identifier, cell_id):
        item = store.get(identifier)
        with engine.slots:
            result = engine.cell_details(item, get_role(), cell_id)
        return jsonify(result)

    @app.get("/api/inspections/<identifier>/cells/<cell_id>/<kind>.png")
    def photo_cell(identifier, cell_id, kind):
        choice(kind, "Cell layer", EL_LAYERS)
        item = store.get(identifier)
        row, col = parse_cell(cell_id, item.plane.rows, item.plane.cols)
        selected = cells_for(item.plane)[row * item.plane.cols + col]
        with engine.slots:
            image = cell_crop(item, engine, get_role(), selected, kind)
        return content_response(png_bytes(image), "image/png")

    @app.post("/api/inspections/<identifier>/polygon")
    def polygon(identifier):
        item = store.get(identifier)
        raw = json_body()
        role = choice(raw.get("role", "high"), "EL capture role", SENSOR_ROLES)
        with engine.slots:
            report, mask, context = engine.polygon(item, role, raw.get("points"), raw.get("finding"))
            if raw.get("format") == "png":
                return content_response(
                    png_bytes(polygon_preview(context, mask, item.display)), "image/png",
                    f"{item.module}-region.png",
                )
        return jsonify(report)

    @app.post("/api/inspections/<identifier>/transect")
    def transect(identifier):
        item = store.get(identifier)
        raw = json_body()
        role = choice(raw.get("role", "high"), "EL capture role", SENSOR_ROLES)
        with engine.slots:
            report = engine.transect(item, role, raw)
        if raw.get("format") == "csv":
            return content_response(csv_data("transect", report["rows"]), "text/csv", f"{item.module}-transect.csv")
        if raw.get("format") == "png":
            return content_response(transect_plot(report), "image/png", f"{item.module}-transect.png")
        return jsonify(report)

    @app.post("/api/inspections/<identifier>/findings")
    def annotate(identifier):
        values = json_body()
        return file_version(service.finding(identifier, values.get("revision"), values))

    @app.put("/api/inspections/<identifier>/findings/<finding_id>")
    def edit_finding(identifier, finding_id):
        values = json_body()
        return file_version(service.finding(identifier, values.get("revision"), values, finding_id))

    @app.delete("/api/inspections/<identifier>/findings/<finding_id>")
    def withdraw_finding(identifier, finding_id):
        return file_version(service.remove_finding(identifier, json_body().get("revision"), finding_id))

    @app.get("/api/inspections/<identifier>/findings/<finding_id>/<view>")
    def finding_source(identifier, finding_id, view):
        choice(view, "Evidence view", frozenset(("data", "plane", "source", "packet")))
        item = store.get(identifier)
        with engine.slots:
            data = evidence_response(store, engine, item, finding_id, view)
        if view == "data":
            return jsonify(data)
        content, mime = data
        name = f"{item.module}-finding.zip" if view == "packet" else None
        return content_response(content, mime, name)

    @app.get("/api/inspections/<identifier>/export/<kind>")
    def image_export(identifier, kind):
        choice(kind, "Export type", frozenset(("plane", "cells", "sheet", "exchange")))
        item = store.get(identifier)
        role = get_role()
        picks = sequence(request.args.getlist("cell"), "Selected cells", 0, 70)
        with engine.slots:
            if kind == "sheet":
                content = grid_contact_sheet(engine, item, role, picks)
                return content_response(content, "image/png", f"{item.module}-cells.png")
            if kind == "exchange":
                content = create_exchange(store, item.id)
            elif kind == "plane":
                content = float_plane(engine, item, role)
            else:
                content = write_cell_bundle(engine, item, role, picks)
        return content_response(content, "application/zip", f"{item.module}-{kind}.zip")

    @app.post("/api/import/<operation>")
    def recover(operation):
        choice(operation, "Exchange operation", frozenset(("preview", "restore")))
        _, raw = request_file(MAX_PACKAGE_BYTES)
        label = request.form.get("module")
        with engine.slots:
            if operation == "preview":
                return jsonify(preview_exchange(raw))
            restored = import_exchange(store, raw, label)
        return file_version(restored)

    @app.get("/api/inspections/<identifier>/report")
    def review_report(identifier):
        item, events = store.bundle(identifier)
        if item.issue:
            return jsonify(issued=item.issue)
        with engine.slots:
            return jsonify(summarize_for_issue(item, engine, events))

    @app.post("/api/inspections/<identifier>/report")
    def approve_report(identifier):
        values = json_body()
        with engine.slots:
            item = issue_report(store, engine, service, identifier, values.get("revision"), values)
        return file_version(item)

    @app.get("/api/inspections/<identifier>/report/<kind>")
    def downloaded_report(identifier, kind):
        choice(kind, "Issued report view", frozenset(("archive", "html", "cover")))
        item = store.get(identifier)
        if item.issue is None:
            raise Issue("Issue the report to access the archived result.", 409)
        digest = item.issue[kind]
        content = store.blobs.read(digest)
        if sha256(content).hexdigest() != digest:
            raise Issue("The report attachment failed verification.", 409)
        packaging = {
            "archive": ("application/zip", f"{item.module}-report.zip"),
            "html": ("text/html", f"{item.module}-report.html" if on("download") else None),
            "cover": ("image/png", None),
        }
        mime, name = packaging[kind]
        return content_response(content, mime, name)

    @app.get("/api/lots/<kind>")
    def lot_review(kind):
        label = request.args.get("lot")
        choice(kind, "Lot review view", frozenset(("summary", "csv", "cells", "sheet")))
        with engine.slots:
            if kind == "summary":
                return jsonify(lot.overview(label))
            if kind == "sheet":
                return content_response(lot.contact_sheet(label), "image/png", f"{label}-contact-sheet.png")
            effect = "lot" if kind == "csv" else "lot-cells"
            return content_response(lot.sheets(label, effect), "text/csv", f"{label}-{effect}.csv")

    # ------------------------------------------------------------------ brightness baselines
    @app.get("/api/baselines/recipes")
    def baseline_recipes():
        return jsonify(recipes=baselines.recipes())

    @app.get("/api/baselines")
    def baseline_list():
        recipe = text(request.args.get("recipe", ""), "Recipe", 80, allow_blank=True)
        include_retired = on("retired")
        return jsonify(baselines.list_baselines(recipe, include_retired=include_retired))

    @app.post("/api/baselines")
    def baseline_create():
        body = json_body()
        with engine.slots:
            baseline, details = build_baseline(
                baselines, engine,
                recipe=body.get("recipe", ""),
                name=body.get("name", ""),
                inspection_ids=body.get("inspection_ids", []),
                note=body.get("note", ""),
                z_limit=body.get("z_limit", 3.0),
                low_ratio=body.get("low_ratio", 0.82),
                global_dim_ratio=body.get("global_dim_ratio", 0.85),
            )
        response = jsonify({**baseline_summary(baseline), "build": details})
        response.headers["ETag"] = f'"{baseline.id}-{baseline.version}"'
        return response

    @app.get("/api/baselines/<identifier>")
    def baseline_detail(identifier):
        baseline = baselines.get(identifier)
        return jsonify({
            **baseline.to_dict(),
            "summary": baseline_summary(baseline),
            "events": baselines.events(identifier),
        })

    @app.get("/api/baselines/<identifier>/<view>")
    def baseline_views(identifier, view):
        choice(view, "Baseline view", frozenset(("image", "cells", "sources", "csv")))
        baseline = baselines.get(identifier)
        if view == "image":
            return content_response(baseline_canvas(baseline), "image/png",
                                    f"{baseline.recipe}-baseline-v{baseline.version}.png")
        if view == "csv":
            kind = "baseline-cells" if request.args.get("table") == "cells" else "baseline-sources"
            rows = [cell for cell in baseline.to_dict()["cells"]] if kind == "baseline-cells" \
                else [source for source in baseline.to_dict()["sources"]]
            return content_response(csv_data(kind, rows), "text/csv",
                                    f"{baseline.recipe}-{kind}.csv")
        if view == "cells":
            return jsonify(rows=baseline.to_dict()["cells"])
        return jsonify(rows=baseline.to_dict()["sources"])

    @app.post("/api/baselines/<identifier>/retire")
    def baseline_retire(identifier):
        baseline = baselines.set_state(identifier, "retired", {"recipe": baselines.get(identifier).recipe})
        return jsonify(baseline_summary(baseline))

    @app.post("/api/baselines/<identifier>/reactivate")
    def baseline_reactivate(identifier):
        baseline = baselines.set_state(identifier, "active", {"recipe": baselines.get(identifier).recipe})
        return jsonify(baseline_summary(baseline))

    @app.get("/api/inspections/<identifier>/baseline-comparison/<view>")
    def baseline_comparison(identifier, view):
        choice(view, "Baseline comparison view", frozenset(("data", "image", "overlay", "csv")))
        item = store.get(identifier)
        explicit = text(request.args.get("baseline", ""), "Baseline", 32, allow_blank=True)
        if explicit:
            baseline = baselines.get(explicit)
        else:
            recipe = text(request.args.get("recipe", item.lot), "Recipe", 80)
            baseline = baselines.active_for_recipe(recipe)
            if baseline is None:
                raise Issue(f"No active brightness baseline exists for recipe {recipe}.", 404)
        with engine.slots:
            result = compare_inspection(engine, baseline, item)
            if view == "image":
                content = comparison_canvas(baseline, result)
                return content_response(content, "image/png",
                                        f"{item.module}-baseline-v{baseline.version}.png")
            if view == "overlay":
                content = overlay_canvas(item, engine, result)
                return content_response(content, "image/png",
                                        f"{item.module}-baseline-overlay.png")
        if view == "csv":
            return content_response(
                csv_data("baseline-comparison", result["rows"]), "text/csv",
                f"{item.module}-baseline-comparison.csv",
            )
        return jsonify(result)

    return app
