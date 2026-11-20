from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import math
import re
from typing import Any


class Issue(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


ROLE_LABELS = {
    "high": "High current",
    "low": "Low current",
    "dark": "Dark reference",
    "flat": "Flat reference",
}
CAPTURE_ROLES = frozenset(ROLE_LABELS)
SENSOR_ROLES = frozenset(("high", "low"))
FILE_ID_RE = re.compile(r"^[0-9a-f]{32}$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
CELL_RE = re.compile(r"^R([0-9]{2})C([0-9]{2})$")
FLOW_STATES = frozenset(("open", "issued"))
FINDING_STATES = frozenset(("open", "hold", "cleared", "confirmed"))
FINDING_CATEGORIES = frozenset(("dark", "crack", "suspect", "capture", "general"))
ORIENTATIONS = frozenset(("identity", "rotate180", "mirror-x", "mirror-y"))
STRING_AXES = frozenset(("columns", "rows"))
MAX_SOURCE_PIXELS = 8_000_000
MAX_PLANE_PIXELS = 1_500_000
MAX_UPLOAD_BYTES = 22 * 1024 * 1024
MAX_PACKAGE_BYTES = 64 * 1024 * 1024
MAX_FINDINGS = 120
MAX_POLYGON_POINTS = 48
MAX_PATHS = 60
MAX_PATH_POINTS = 24
MAX_HISTORY = 80


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def mapping(value: Any, label: str) -> dict:
    if not isinstance(value, dict):
        raise Issue(f"{label} must be an object.")
    return value


def sequence(value: Any, label: str, min_size: int, max_size: int) -> list:
    if not isinstance(value, (list, tuple)) or not min_size <= len(value) <= max_size:
        raise Issue(f"{label} must contain {min_size} to {max_size} items.")
    return list(value)


def text(value: Any, label: str, max_size: int, *, allow_blank: bool = False) -> str:
    if not isinstance(value, str):
        raise Issue(f"{label} must be text.")
    clean = value.strip()
    if (not allow_blank and not clean) or len(clean) > max_size:
        raise Issue(f"{label} must contain {'0' if allow_blank else '1'} to {max_size} characters.")
    if any(ord(char) < 32 and char not in "\n\t" for char in clean):
        raise Issue(f"{label} contains control characters.")
    return clean


def integer(value: Any, label: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool):
        raise Issue(f"{label} must be an integer.")
    if isinstance(value, int):
        number = value
    elif isinstance(value, str) and re.fullmatch(r"-?[0-9]{1,12}", value.strip()):
        number = int(value)
    else:
        raise Issue(f"{label} must be an integer.")
    if not minimum <= number <= maximum:
        raise Issue(f"{label} must be between {minimum} and {maximum}.")
    return number


def real(value: Any, label: str, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise Issue(f"{label} must be a number.")
    try:
        number = float(value)
    except (ValueError, OverflowError):
        raise Issue(f"{label} must be a finite number.") from None
    if not math.isfinite(number) or not minimum <= number <= maximum:
        raise Issue(f"{label} must be between {minimum} and {maximum}.")
    return number


def choice(value: Any, label: str, choices: frozenset[str]) -> str:
    if not isinstance(value, str) or value not in choices:
        raise Issue(f"{label} must be one of {', '.join(sorted(choices))}.")
    return value


def boolean(value: Any, label: str) -> bool:
    if not isinstance(value, bool):
        raise Issue(f"{label} must be true or false.")
    return value


def file_id(value: Any, label: str = "Image ID") -> str:
    if not isinstance(value, str) or not FILE_ID_RE.fullmatch(value):
        raise Issue(f"{label} is invalid.", 404)
    return value


def sha256_id(value: Any) -> str:
    if not isinstance(value, str) or not SHA_RE.fullmatch(value):
        raise Issue("Image digest is invalid.")
    return value


def xy_list(points: Any, label: str, min_size: int, max_size: int,
            x_bounds: tuple[float, float], y_bounds: tuple[float, float]) -> tuple:
    return tuple(
        (
            real(point[0], f"{label} x", *x_bounds),
            real(point[1], f"{label} y", *y_bounds),
        )
        for point in (
            sequence(value, label, 2, 2)
            for value in sequence(points, label, min_size, max_size)
        )
    )


def cell_id(row: int, col: int) -> str:
    return f"R{row + 1:02}C{col + 1:02}"


def parse_cell(value: Any, rows: int, cols: int) -> tuple[int, int]:
    match = CELL_RE.fullmatch(value) if isinstance(value, str) else None
    if not match:
        raise Issue("A cell must use an identifier such as R01C01.")
    row, col = (int(part) - 1 for part in match.groups())
    if not (0 <= row < rows and 0 <= col < cols):
        raise Issue("That cell is outside the current grid.")
    return row, col


@dataclass(frozen=True)
class Capture:
    id: str
    role: str
    name: str
    format: str
    digest: str
    byte_size: int
    width: int
    height: int
    bit_depth: int
    white_level: int
    exposure_ms: float
    current_a: float | None
    captured_at: str
    synthetic: bool

    @classmethod
    def from_dict(cls, raw: dict):
        raw = mapping(raw, "Image")
        role = choice(raw.get("role"), "Role", CAPTURE_ROLES)
        depth = integer(raw.get("bit_depth"), "Bit depth", 8, 16)
        if depth not in (8, 16):
            raise Issue("Capture bit depth must be 8 or 16.")
        width = integer(raw.get("width"), "Image width", 32, MAX_SOURCE_PIXELS)
        height = integer(raw.get("height"), "Image height", 32, MAX_SOURCE_PIXELS)
        if width * height > MAX_SOURCE_PIXELS:
            raise Issue("Image has too many pixels.")
        current = raw.get("current_a")
        if current is not None:
            current = real(current, "Current", 0.001, 100)
        if role not in SENSOR_ROLES and current is not None:
            raise Issue("Only EL captures may carry a drive current.")
        return cls(
            file_id(raw.get("id")),
            role,
            text(raw.get("name"), "Image name", 120),
            choice(raw.get("format"), "Image format", frozenset(("PNG", "TIFF"))),
            sha256_id(raw.get("digest")),
            integer(raw.get("byte_size"), "Image size", 1, MAX_UPLOAD_BYTES),
            width,
            height,
            depth,
            integer(raw.get("white_level"), "White level", 16, (1 << depth) - 1),
            real(raw.get("exposure_ms"), "Exposure", 0.01, 600000),
            current,
            text(raw.get("captured_at"), "Capture timestamp", 100),
            boolean(raw.get("synthetic"), "Synthetic flag"),
        )


@dataclass(frozen=True)
class Plane:
    width: int = 768
    height: int = 512
    rows: int = 4
    cols: int = 6
    inset: float = 0.09
    string_axis: str = "columns"
    string_size: int = 2
    physical_width: float | None = None
    physical_height: float | None = None

    @classmethod
    def from_dict(cls, raw: dict):
        raw = mapping(raw, "Panel plane")
        width = integer(raw.get("width"), "Plane width", 96, 1800)
        height = integer(raw.get("height"), "Plane height", 96, 1800)
        if width * height > MAX_PLANE_PIXELS:
            raise Issue("The rectified plane is limited to 1.5 million pixels.")
        rows = integer(raw.get("rows"), "Cell rows", 1, 16)
        cols = integer(raw.get("cols"), "Cell columns", 1, 24)
        if width / cols < 16 or height / rows < 16:
            raise Issue("Each cell needs at least 16 plane pixels per side.")
        string_axis = choice(raw.get("string_axis", "columns"), "String axis", STRING_AXES)
        string_size = integer(
            raw.get("string_size", 1), "String width", 1, cols if string_axis == "columns" else rows
        )
        dims = [raw.get(key) for key in ("physical_width", "physical_height")]
        if all(value is None or value == "" for value in dims):
            physical = (None, None)
        else:
            physical = tuple(real(value, f"Physical {label}", 20, 12000)
                             for value, label in zip(dims, ("width", "height")))
        return cls(
            width, height, rows, cols,
            real(raw.get("inset", 0.09), "Cell inset", 0, 0.3),
            string_axis, string_size, *physical,
        )


@dataclass(frozen=True)
class Placement:
    corners: tuple[tuple[float, float], ...]
    orientation: str = "identity"

    @classmethod
    def from_dict(cls, raw: dict, width: int, height: int):
        raw = mapping(raw, "Placement")
        return cls(
            xy_list(raw.get("corners"), "Panel corners", 4, 4,
                    (-0.25 * width, 1.25 * width), (-0.25 * height, 1.25 * height)),
            choice(raw.get("orientation", "identity"), "Camera orientation", ORIENTATIONS),
        )


@dataclass(frozen=True)
class Calibration:
    dark_id: str | None = None
    flat_id: str | None = None
    flat_floor: float = 0.2

    @classmethod
    def from_dict(cls, raw: dict):
        raw = mapping(raw, "Calibration")
        dark_id = raw.get("dark_id")
        flat_id = raw.get("flat_id")
        return cls(
            file_id(dark_id, "Dark image ID") if dark_id else None,
            file_id(flat_id, "Flat image ID") if flat_id else None,
            real(raw.get("flat_floor", 0.2), "Flat field floor", 0.05, 0.8),
        )


@dataclass(frozen=True)
class ViewSettings:
    lower: float = 0.01
    upper: float = 0.7
    gamma: float = 0.8

    @classmethod
    def from_dict(cls, raw: dict):
        raw = mapping(raw, "Display settings")
        lower = real(raw.get("lower", 0.01), "Display black", 0, 1.8)
        upper = real(raw.get("upper", 0.7), "Display white", 0.001, 2)
        if upper - lower < 0.001:
            raise Issue("Display white must be above display black.")
        gamma = real(raw.get("gamma", 0.8), "Display gamma", 0.12, 4)
        return cls(lower, upper, gamma)


@dataclass(frozen=True)
class AnalysisSettings:
    dark_fraction: float = 0.59
    minimum_area: int = 18
    crack_contrast: float = 0.115
    minimum_length: int = 13
    usable_cell: float = 0.6
    current_ratio: float = 0.72
    ignore_margin: int = 2
    busbars: tuple[float, ...] = ()
    busbar_radius: int = 3

    @classmethod
    def from_dict(cls, raw: dict):
        raw = mapping(raw, "Measurement settings")
        axes = raw.get("busbars", [])
        if isinstance(axes, str):
            axes = [point.strip() for point in axes.split(",") if point.strip()]
        positions = tuple(sorted(
            real(point, "Busbar position fraction", 0.03, 0.97)
            for point in sequence(axes, "Busbar positions", 0, 8)
        ))
        if any(next_value - value < 0.045 for value, next_value in zip(positions, positions[1:])):
            raise Issue("Busbar positions must be distinct and spaced by at least 0.045.")
        return cls(
            real(raw.get("dark_fraction", 0.59), "Relative dark floor", 0.05, 0.95),
            integer(raw.get("minimum_area", 18), "Minimum dark area", 4, 3500),
            real(raw.get("crack_contrast", 0.115), "Crack contrast floor", 0.025, 0.45),
            integer(raw.get("minimum_length", 13), "Minimum line length", 4, 250),
            real(raw.get("usable_cell", 0.6), "Required usable cell fraction", 0.1, 0.98),
            real(raw.get("current_ratio", 0.72), "Relative response floor", 0.15, 0.95),
            integer(raw.get("ignore_margin", 2), "Trace clearance", 0, 8),
            positions,
            integer(raw.get("busbar_radius", 3), "Busbar half width", 1, 16),
        )


@dataclass(frozen=True)
class Finding:
    id: str
    title: str
    category: str
    status: str
    source_role: str
    points: tuple[tuple[float, float], ...]
    cell: str | None
    note: str
    anchor: dict
    created_at: str
    updated_at: str

    @classmethod
    def from_dict(cls, raw: dict):
        raw = mapping(raw, "Finding")
        cell = raw.get("cell")
        if cell is not None and (not isinstance(cell, str) or not CELL_RE.fullmatch(cell)):
            raise Issue("Finding cell ID is invalid.")
        return cls(
            file_id(raw.get("id"), "Finding ID"),
            text(raw.get("title"), "Finding title", 100),
            choice(raw.get("category"), "Finding category", FINDING_CATEGORIES),
            choice(raw.get("status"), "Finding status", FINDING_STATES),
            choice(raw.get("source_role"), "Finding source", SENSOR_ROLES),
            xy_list(raw.get("points"), "Finding polygon", 3, MAX_POLYGON_POINTS, (0, 1), (0, 1)),
            cell,
            text(raw.get("note", ""), "Finding note", 1200, allow_blank=True),
            mapping(raw.get("anchor"), "Finding source anchor"),
            text(raw.get("created_at"), "Finding creation time", 100),
            text(raw.get("updated_at"), "Finding edit time", 100),
        )


@dataclass(frozen=True)
class Path:
    id: str
    name: str
    source_role: str
    points: tuple[tuple[float, float], ...]
    width: int
    interval: float
    created_at: str
    updated_at: str

    @classmethod
    def from_dict(cls, raw: dict):
        raw = mapping(raw, "Transect path")
        points = xy_list(raw.get("points"), "Path points", 2, MAX_PATH_POINTS, (0, 1), (0, 1))
        width = integer(raw.get("width", 5), "Strip width", 1, 51)
        if width % 2 == 0:
            raise Issue("Strip width must be an odd number of pixels.")
        return cls(
            file_id(raw.get("id"), "Path ID"),
            text(raw.get("name"), "Path name", 80),
            choice(raw.get("source_role"), "Path source", SENSOR_ROLES),
            points,
            width,
            real(raw.get("interval", 1), "Sampling interval", 0.5, 24),
            text(raw.get("created_at"), "Path creation time", 100),
            text(raw.get("updated_at"), "Path edit time", 100),
        )


@dataclass
class Inspection:
    id: str
    module: str
    lot: str
    operator: str
    notes: str
    created_at: str
    updated_at: str
    state: str = "open"
    revision: int = 0
    captures: dict[str, Capture] = field(default_factory=dict)
    active: dict[str, str] = field(default_factory=dict)
    placements: dict[str, Placement] = field(default_factory=dict)
    plane: Plane = field(default_factory=Plane)
    calibration: Calibration = field(default_factory=Calibration)
    display: ViewSettings = field(default_factory=ViewSettings)
    analysis: AnalysisSettings = field(default_factory=AnalysisSettings)
    findings: list[Finding] = field(default_factory=list)
    paths: list[Path] = field(default_factory=list)
    issue: dict | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    def selected(self, role: str) -> Capture:
        choice(role, "Image role", CAPTURE_ROLES)
        capture = self.captures.get(self.active.get(role, ""))
        if capture is None:
            raise Issue(f"Select a {ROLE_LABELS[role].lower()} image first.", 409)
        return capture

    def capture(self, identifier: str) -> Capture:
        file_id(identifier)
        capture = self.captures.get(identifier)
        if capture is None:
            raise Issue("This image is not in the inspection.", 404)
        return capture

    @classmethod
    def from_dict(cls, raw: dict):
        raw = mapping(raw, "Inspection")
        captures = {}
        for key, value in mapping(raw.get("captures", {}), "Captures").items():
            capture = Capture.from_dict(value)
            if key != capture.id:
                raise Issue("The capture ID does not match its key.")
            captures[key] = capture
        active = mapping(raw.get("active", {}), "Selected captures")
        for role, identifier in active.items():
            choice(role, "Selected role", CAPTURE_ROLES)
            capture = captures.get(file_id(identifier))
            if capture is None or capture.role != role:
                raise Issue("Capture selection does not match its role.")
        placements = {}
        for key, value in mapping(raw.get("placements", {}), "Placements").items():
            capture = captures.get(key)
            if capture is None or capture.role not in SENSOR_ROLES:
                raise Issue("A placement must refer to an EL capture.")
            placements[key] = Placement.from_dict(value, capture.width, capture.height)
        findings = [Finding.from_dict(value) for value in sequence(
            raw.get("findings", []), "Findings", 0, MAX_FINDINGS
        )]
        if len({finding.id for finding in findings}) != len(findings):
            raise Issue("Finding identifiers must be unique.")
        paths = [Path.from_dict(value) for value in sequence(
            raw.get("paths", []), "Transect paths", 0, MAX_PATHS
        )]
        if len({path.id for path in paths}) != len(paths):
            raise Issue("Transect path identifiers must be unique.")
        issue = raw.get("issue")
        if issue is not None:
            issue = mapping(issue, "Issued result")
        state = choice(raw.get("state", "open"), "Inspection state", FLOW_STATES)
        if (state == "issued") != (issue is not None):
            raise Issue("Inspection state does not match its issued result.")
        return cls(
            file_id(raw.get("id"), "Inspection ID"),
            text(raw.get("module"), "Module serial", 80),
            text(raw.get("lot", ""), "Production lot", 80, allow_blank=True),
            text(raw.get("operator", ""), "Operator", 80, allow_blank=True),
            text(raw.get("notes", ""), "Inspection notes", 2000, allow_blank=True),
            text(raw.get("created_at"), "Creation time", 100),
            text(raw.get("updated_at"), "Edit time", 100),
            state,
            integer(raw.get("revision", 0), "Revision", 0, 10**9),
            captures,
            dict(active),
            placements,
            Plane.from_dict(raw.get("plane", asdict(Plane()))),
            Calibration.from_dict(raw.get("calibration", {})),
            ViewSettings.from_dict(raw.get("display", {})),
            AnalysisSettings.from_dict(raw.get("analysis", {})),
            findings,
            paths,
            issue,
        )
