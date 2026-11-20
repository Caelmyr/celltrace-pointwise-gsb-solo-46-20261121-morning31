from dataclasses import asdict, dataclass, field

from .model import Issue, choice, integer, mapping, real, sequence, text, utcnow


BASELINE_SCHEMA = "celltrace-brightness-baseline-v1"
BASELINE_STATES = frozenset(("active", "retired"))
# A position needs this many accepted samples before its limits are treated as firm evidence.
MIN_FIRM_SAMPLES = 3
# Robust z score beyond which a sample is treated as an outlier at construction time.
OUTLIER_Z = 3.5
# Default comparison limits, overridable per baseline.
DEFAULT_Z_LIMIT = 3.0
DEFAULT_LOW_RATIO = 0.82
DEFAULT_GLOBAL_DIM = 0.85
MAX_BASELINE_SAMPLES = 60
MAX_BASELINE_NAME = 100
MAX_RECIPE = 80


@dataclass(frozen=True)
class BaselineSource:
    inspection_id: str
    module: str
    lot: str
    capture_id: str
    digest: str
    issued_at: str
    current_a: float | None
    exposure_ms: float
    bit_depth: int
    white_level: int
    dark_id: str | None
    flat_id: str | None
    panel_median: float
    eligible_cells: int
    synthetic: bool

    @classmethod
    def from_dict(cls, raw: dict):
        raw = mapping(raw, "Baseline source")
        current = raw.get("current_a")
        return cls(
            text(raw.get("inspection_id"), "Source inspection", 32),
            text(raw.get("module"), "Source module", 80),
            text(raw.get("lot", ""), "Source lot", 80, allow_blank=True),
            text(raw.get("capture_id"), "Source capture", 32),
            text(raw.get("digest"), "Source digest", 64),
            text(raw.get("issued_at"), "Source issue time", 100),
            real(current, "Source current", 0.001, 100) if current is not None else None,
            real(raw.get("exposure_ms"), "Source exposure", 0.01, 600000),
            integer(raw.get("bit_depth"), "Source bit depth", 8, 16),
            integer(raw.get("white_level"), "Source white level", 16, 65535),
            raw.get("dark_id"),
            raw.get("flat_id"),
            real(raw.get("panel_median"), "Source panel median", 1e-6, 10.0),
            integer(raw.get("eligible_cells"), "Source eligible cells", 1, 400),
            bool(raw.get("synthetic")),
        )


@dataclass(frozen=True)
class BaselineCell:
    cell: str
    row: int
    col: int
    samples: int
    median: float | None
    mad: float | None
    low: float | None
    high: float | None
    relative_median: float | None
    relative_mad: float | None
    absolute_median: float | None
    evidence: str
    rejected: int
    source_modules: tuple[str, ...] = field(default_factory=tuple)

    @classmethod
    def from_dict(cls, raw: dict):
        raw = mapping(raw, "Baseline cell")
        samples = integer(raw.get("samples"), "Cell sample count", 0, MAX_BASELINE_SAMPLES)
        level = raw.get("evidence", "limited" if samples < MIN_FIRM_SAMPLES else "firm")
        if level not in ("firm", "limited", "unsupported"):
            raise Issue("Baseline cell evidence level is invalid.")
        optionals = ("median", "mad", "low", "high", "relative_median", "relative_mad", "absolute_median")
        values = {}
        for key in optionals:
            value = raw.get(key)
            values[key] = real(value, f"Baseline {key}", 0.0, 10.0) if value is not None else None
        modules = sequence(raw.get("source_modules", []), "Baseline source modules", 0, MAX_BASELINE_SAMPLES)
        return cls(
            text(raw.get("cell"), "Cell ID", 8),
            integer(raw.get("row"), "Cell row", 0, 15),
            integer(raw.get("col"), "Cell column", 0, 23),
            samples, values["median"], values["mad"], values["low"], values["high"],
            values["relative_median"], values["relative_mad"], values["absolute_median"],
            level, integer(raw.get("rejected", 0), "Rejected samples", 0, MAX_BASELINE_SAMPLES),
            tuple(text(item, "Source module name", 80) for item in modules),
        )


@dataclass(frozen=True)
class BrightnessBaseline:
    id: str
    name: str
    recipe: str
    version: int
    state: str
    rows: int
    cols: int
    z_limit: float
    low_ratio: float
    global_dim_ratio: float
    created_at: str
    updated_at: str
    cells: tuple[BaselineCell, ...]
    sources: tuple[BaselineSource, ...]
    acquisition: dict
    schema: str = BASELINE_SCHEMA
    digest: str | None = None
    note: str = ""

    @classmethod
    def from_dict(cls, raw: dict):
        raw = mapping(raw, "Brightness baseline")
        if raw.get("schema") != BASELINE_SCHEMA:
            raise Issue("This brightness baseline uses an unsupported schema.")
        cells = tuple(
            BaselineCell.from_dict(item)
            for item in sequence(raw.get("cells", []), "Baseline cells", 1, 384)
        )
        sources = tuple(
            BaselineSource.from_dict(item)
            for item in sequence(raw.get("sources", []), "Baseline sources", 1, MAX_BASELINE_SAMPLES)
        )
        acquisition = mapping(raw.get("acquisition", {}), "Baseline acquisition settings")
        return cls(
            text(raw.get("id"), "Baseline ID", 32),
            text(raw.get("name"), "Baseline name", MAX_BASELINE_NAME),
            text(raw.get("recipe"), "Recipe", MAX_RECIPE),
            integer(raw.get("version"), "Baseline version", 1, 10**6),
            choice(raw.get("state"), "Baseline state", BASELINE_STATES),
            integer(raw.get("rows"), "Baseline rows", 1, 16),
            integer(raw.get("cols"), "Baseline columns", 1, 24),
            real(raw.get("z_limit", DEFAULT_Z_LIMIT), "Baseline z limit", 1.0, 8.0),
            real(raw.get("low_ratio", DEFAULT_LOW_RATIO), "Baseline low ratio", 0.3, 1.0),
            real(raw.get("global_dim_ratio", DEFAULT_GLOBAL_DIM), "Global dim ratio", 0.3, 1.0),
            text(raw.get("created_at"), "Baseline created time", 100),
            text(raw.get("updated_at"), "Baseline updated time", 100),
            cells, sources, acquisition,
            BASELINE_SCHEMA,
            raw.get("digest"),
            text(raw.get("note", ""), "Baseline note", 2000, allow_blank=True),
        )

    def to_dict(self) -> dict:
        return asdict(self)

    def cell_map(self) -> dict[str, BaselineCell]:
        return {entry.cell: entry for entry in self.cells}

    def touch(self) -> "BrightnessBaseline":
        from dataclasses import replace
        return replace(self, updated_at=utcnow())
