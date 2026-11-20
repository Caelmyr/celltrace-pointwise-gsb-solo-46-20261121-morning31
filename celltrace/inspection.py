from dataclasses import asdict, replace

from .calibration import dark_matches, reference_matches
from .demo import DEMO_NOTICE, make_demo
from .findings import get_finding, list_findings, make_finding
from .geometry import default_placement, transform_for
from .images import SensorImage, decode_source, safe_name
from .model import (
    AnalysisSettings, CAPTURE_ROLES, Calibration, Capture, Inspection, Issue, Placement, Plane,
    PLACEMENT_ROLES, SENSOR_ROLES, ViewSettings, boolean, choice, file_id, integer, mapping,
    real, text, utcnow,
)
from . import surface as surface_review_module
from .store import Store, new_id, summary


MAX_INSPECTION_IMAGES = 18


def new_inspection(module: str, lot: str, operator: str, notes: str) -> Inspection:
    created = utcnow()
    return Inspection(new_id(), module, lot, operator, notes, created, created)


def edit_metadata(raw: dict) -> dict:
    return {
        "module": text(raw.get("module"), "Module serial", 80),
        "lot": text(raw.get("lot", ""), "Production lot", 80, allow_blank=True),
        "operator": text(raw.get("operator", ""), "Operator", 80, allow_blank=True),
        "notes": text(raw.get("notes", ""), "Inspection notes", 2000, allow_blank=True),
    }


def capture_metadata(role: str, name: str, source: SensorImage, raw: dict, *, synthetic=False) -> Capture:
    choice(role, "Capture role", CAPTURE_ROLES)
    current = raw.get("current_a")
    if current in (None, ""):
        current = None
    elif role in SENSOR_ROLES:
        current = real(current, "Drive current (A)", 0.001, 100)
    else:
        raise Issue("The drive current is only used for low- and high-current captures.")
    fallback = "image.tif" if source.format == "TIFF" else "image.png"
    return Capture(
        new_id(), role, safe_name(name, fallback),
        source.format, source.digest, source.byte_size,
        source.width, source.height, source.bit_depth,
        integer(raw.get("white_level") or source.maximum, "Sensor white level", 16, source.maximum),
        real(raw.get("exposure_ms", 100), "Exposure (ms)", 0.01, 600000),
        current,
        utcnow(),
        synthetic,
    )


def inspection_payload(inspection: Inspection) -> dict:
    output = inspection.to_dict()
    output["findings"] = list_findings(inspection)
    output["summary"] = summary(inspection)
    output["placements"] = {}
    for capture in inspection.captures.values():
        if capture.role in PLACEMENT_ROLES:
            configured = inspection.placements.get(capture.id)
            value = configured or default_placement(capture.width, capture.height)
            output["placements"][capture.id] = {**asdict(value), "explicit": configured is not None}
    return output


class Inspections:
    def __init__(self, store: Store):
        self.store = store

    def create(self, raw: dict) -> Inspection:
        item = new_inspection(**edit_metadata(raw))
        return self.store.add(item, "created", {"module": item.module})

    def demo(self, variant: str) -> Inspection:
        choice(variant, "Synthetic sample", frozenset(("standard", "stress")))
        plane, images = make_demo(variant)
        module = "EL-24017" if variant == "standard" else "EL-24029"
        item = new_inspection(module, "DEMO-EL04", "Training", DEMO_NOTICE)
        item.plane = plane
        item.analysis = replace(item.analysis, busbars=(0.28, 0.72))
        for frame in images:
            source = decode_source(frame.content)
            capture = capture_metadata(frame.role, frame.name, source, {
                "exposure_ms": frame.exposure_ms, "current_a": frame.current_a,
            }, synthetic=True)
            self.store.blobs.put(frame.content)
            item.captures[capture.id] = capture
            item.active[capture.role] = capture.id
            if frame.corners is not None:
                item.placements[capture.id] = Placement(frame.corners, frame.orientation)
        item.calibration = Calibration(item.active["dark"], item.active["flat"])
        item = self.seed_demo_surface(item)
        return self.store.add(item, "synthetic fixture loaded", {"sample": variant, "images": len(images)})

    def seed_demo_surface(self, item: Inspection) -> Inspection:
        """Pre-trace contamination on the synthetic surface photo for training."""
        from .model import SurfaceMark, SurfaceReview
        surface_capture = next((capture for capture in item.captures.values()
                                if capture.role == "surface"), None)
        if surface_capture is None:
            return item
        now = utcnow()
        width, height = item.plane.width, item.plane.height
        # Plane-cell coordinates of the seeded smudges, expressed as normalized polygons.
        outlines = [
            ("Elongated handling smudge", "smudge", (
                (2.30, 1.18), (2.66, 1.14), (3.06, 1.34), (3.02, 1.62),
                (2.62, 1.68), (2.32, 1.50),
            )),
            ("Localized film patch", "film", (
                (1.24, 2.42), (1.62, 2.40), (1.66, 2.92), (1.28, 2.96),
            )),
            # Only clips the left side of the R01C05 dark patch, so the verdict stays partial.
            ("Corner contact streak", "streak", (
                (4.40, 0.46), (4.72, 0.44), (4.74, 0.66), (4.42, 0.68),
            )),
        ]
        marks = []
        for index, (title, kind, polygon) in enumerate(outlines, start=1):
            points = tuple((col / item.plane.cols, row / item.plane.rows) for col, row in polygon)
            marks.append(SurfaceMark(new_id(), title, kind, points, "", now, now))
        item.surface_review = SurfaceReview(surface_capture.id, 4, tuple(marks), {}, "", now)
        return item

    def checked(self, identifier: str, expected, *, editable=True) -> Inspection:
        expected = integer(expected, "Revision", 0, 10**9)
        item = self.store.get(identifier)
        if item.revision != expected:
            raise Issue("The inspection changed. Reload before applying more changes.", 409)
        if editable and item.state == "issued":
            raise Issue("This inspection is issued. Make a working copy to continue review.", 409)
        return item

    def change(self, identifier: str, expected, operation: str, raw: dict) -> Inspection:
        item = self.checked(identifier, expected)
        if operation == "metadata":
            body = edit_metadata(raw)
            item = replace(item, **body)
            message, details = "module details updated", {"module": item.module}
        elif operation == "plane":
            item.plane = Plane.from_dict(raw)
            for placement in item.placements.values():
                transform_for(item.plane, placement)
            message, details = "panel plane updated", asdict(item.plane)
        elif operation == "placement":
            capture = item.capture(raw.get("capture"))
            if capture.role not in PLACEMENT_ROLES:
                raise Issue("Only an EL frame or a surface photo can receive a panel placement.")
            placement = Placement.from_dict(raw, capture.width, capture.height)
            transform_for(item.plane, placement)
            item.placements[capture.id] = placement
            message, details = "image placement updated", {"capture": capture.id, **asdict(placement)}
        elif operation in (
            "surface-config", "surface-mark-add", "surface-mark-edit",
            "surface-mark-remove", "surface-verdict",
        ):
            if operation == "surface-config":
                review, message, details = surface_review_module.configure(item, raw)
            elif operation == "surface-mark-add":
                review, message, details = surface_review_module.add_mark(item, raw)
            elif operation == "surface-mark-edit":
                review, message, details = surface_review_module.edit_mark(item, raw.get("mark"), raw)
            elif operation == "surface-mark-remove":
                review, message, details = surface_review_module.remove_mark(item, raw.get("mark"))
            else:
                review, message, details = surface_review_module.set_verdict(item, raw)
            item.surface_review = review
        elif operation == "calibration":
            item.calibration = Calibration.from_dict(raw)
            details = check_calibration(item)
            message = "references selected"
        elif operation == "display":
            item.display = ViewSettings.from_dict(raw)
            message, details = "display levels updated", asdict(item.display)
        elif operation == "analysis":
            item.analysis = AnalysisSettings.from_dict(raw)
            message, details = "measurement thresholds updated", asdict(item.analysis)
        elif operation == "selection":
            capture = item.capture(raw.get("capture"))
            item.active[capture.role] = capture.id
            message, details = "capture selected", {"role": capture.role, "capture": capture.id}
        elif operation == "capture-metadata":
            capture = item.capture(raw.get("capture"))
            if capture_applies_to_findings(item, capture.id):
                raise Issue("Review evidence uses this capture. Upload a new capture entry with corrected metadata.", 409)
            current = raw.get("current_a")
            current = real(current, "Drive current", 0.001, 100) if current not in ("", None) else None
            if capture.role not in SENSOR_ROLES and current is not None:
                raise Issue("Only EL captures can carry a drive current.")
            item.captures[capture.id] = replace(
                capture,
                exposure_ms=real(raw.get("exposure_ms"), "Exposure (ms)", 0.01, 600000),
                current_a=current,
                white_level=integer(raw.get("white_level"), "Sensor white level", 16, (1 << capture.bit_depth) - 1),
            )
            message, details = "capture metadata corrected", {"capture": capture.id}
        else:
            raise Issue("This inspection operation is not available.", 404)
        return self.store.save(item, item.revision, message, details)

    def upload(self, identifier: str, expected, role: str, name: str, content: bytes, raw: dict) -> Inspection:
        item = self.checked(identifier, expected)
        if len(item.captures) >= MAX_INSPECTION_IMAGES:
            raise Issue(f"An inspection can hold up to {MAX_INSPECTION_IMAGES} originals.", 409)
        source = decode_source(content)
        capture = capture_metadata(role, name, source, raw)
        if any(
            existing.role == capture.role and existing.digest == capture.digest
            and existing.exposure_ms == capture.exposure_ms and existing.current_a == capture.current_a
            and existing.white_level == capture.white_level
            for existing in item.captures.values()
        ):
            raise Issue("That image and its acquisition settings are already present in this role.", 409)
        self.store.blobs.put(content)
        item.captures[capture.id] = capture
        if raw.get("select", "true") == "true" or role not in item.active:
            item.active[role] = capture.id
        if role == "surface" and item.surface_review.surface_id is None:
            item.surface_review = replace(item.surface_review, surface_id=capture.id, updated_at=utcnow())
        return self.store.save(item, item.revision, "original imported", {
            "capture": capture.id, "role": capture.role, "digest": capture.digest,
            "size": [capture.width, capture.height], "format": capture.format,
        })

    def remove_capture(self, identifier: str, expected, capture_id: str) -> Inspection:
        item = self.checked(identifier, expected)
        capture = item.capture(capture_id)
        if capture_applies_to_findings(item, capture.id):
            raise Issue("Review evidence uses this capture. Keep its original for traceability.", 409)
        if item.surface_review.surface_id == capture.id:
            raise Issue("The surface cross-check uses this photo. Clear the surface review first.", 409)
        if capture.id in (item.calibration.dark_id, item.calibration.flat_id):
            raise Issue("Clear the selected calibration reference before removing this capture.", 409)
        del item.captures[capture.id]
        item.placements.pop(capture.id, None)
        if capture.role in item.active and item.active.get(capture.role) == capture.id:
            candidates = [entry for entry in item.captures.values() if entry.role == capture.role]
            if candidates:
                replacement = max(candidates, key=lambda entry: (entry.captured_at, entry.id))
                item.active[capture.role] = replacement.id
            else:
                del item.active[capture.role]
        return self.store.save(item, item.revision, "original detached", {"capture": capture.id})

    def clone(self, source_id: str, raw: dict):
        source = self.store.get(source_id)
        preview = f"{source.module} review copy"[:80]
        requested_module = text(raw.get("module", preview), "Module serial", 80)
        cloned = replace(
            source,
            id=new_id(), state="open", issue=None, module=requested_module,
            revision=0, created_at=utcnow(), updated_at=utcnow(),
        )
        return self.store.add(cloned, "working copy created", {
            "source": source.id, "source_revision": source.revision, "source_state": source.state,
        })

    def finding(self, identifier: str, expected, raw: dict, finding_id=None) -> Inspection:
        item = self.checked(identifier, expected)
        entry = make_finding(item, raw, finding_id)
        if finding_id is None:
            item.findings.append(entry)
        else:
            item.findings = [entry if value.id == finding_id else value for value in item.findings]
        return self.store.save(item, item.revision, "finding updated" if finding_id else "finding recorded", {
            "finding": entry.id, "status": entry.status, "category": entry.category, "cell": entry.cell,
        })

    def remove_finding(self, identifier: str, expected, finding_id) -> Inspection:
        item = self.checked(identifier, expected)
        removed = get_finding(item, finding_id)
        item.findings = [entry for entry in item.findings if entry.id != removed.id]
        return self.store.save(item, item.revision, "finding withdrawn", {"finding": removed.id})


def capture_applies_to_findings(inspection: Inspection, identifier: str) -> bool:
    for finding in inspection.findings:
        if finding.anchor.get("image") == identifier:
            return True
        reference = finding.anchor.get("calibration", {})
        if identifier in (reference.get("dark_id"), reference.get("flat_id")):
            return True
    return False


def check_calibration(inspection: Inspection) -> dict:
    notes = []
    for role in ("dark", "flat"):
        key = getattr(inspection.calibration, f"{role}_id")
        if key is None:
            continue
        capture = inspection.capture(key)
        if capture.role != role:
            raise Issue(f"Select an original in the {role} reference role.")
        for device_role in ("high", "low"):
            active = inspection.captures.get(inspection.active.get(device_role))
            if active is None:
                continue
            mismatch = dark_matches if role == "dark" else reference_matches
            problem = mismatch(active, capture)
            if problem:
                notes.append({"role": device_role, "reference": role, "reason": problem})
    return {"references": asdict(inspection.calibration), "compatibility": notes}
