from contextlib import contextmanager
import json

from .baseline_model import BASELINE_SCHEMA, BrightnessBaseline
from .model import Issue, file_id
from .store import Store, data_digest, new_id, serial_json


class BaselineStore:
    """Immutable, versioned brightness baselines stored beside the inspections."""

    def __init__(self, store: Store):
        self.store = store
        with store.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS baselines (
                    id TEXT PRIMARY KEY,
                    recipe TEXT NOT NULL,
                    name TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    state TEXT NOT NULL CHECK(state IN ('active','retired')),
                    updated TEXT NOT NULL,
                    digest TEXT NOT NULL,
                    body BLOB NOT NULL,
                    UNIQUE(recipe, version)
                );
                CREATE INDEX IF NOT EXISTS baseline_recipe ON baselines(recipe COLLATE NOCASE, version DESC);
                CREATE INDEX IF NOT EXISTS baseline_active ON baselines(state, recipe);
                CREATE TABLE IF NOT EXISTS baseline_events (
                    serial INTEGER PRIMARY KEY AUTOINCREMENT,
                    baseline TEXT NOT NULL REFERENCES baselines(id) ON DELETE CASCADE,
                    at TEXT NOT NULL,
                    action TEXT NOT NULL,
                    details BLOB NOT NULL
                );
                CREATE INDEX IF NOT EXISTS baseline_event_order ON baseline_events(baseline, serial DESC);
            """)

    @contextmanager
    def write(self):
        with self.store.write() as db:
            yield db

    def get(self, identifier: str) -> BrightnessBaseline:
        file_id(identifier, "Baseline ID")
        with self.store.connection() as db:
            row = db.execute("SELECT body FROM baselines WHERE id=?", (identifier,)).fetchone()
        if row is None:
            raise Issue("This brightness baseline does not exist.", 404)
        return BrightnessBaseline.from_dict(json.loads(row["body"]))

    def list_baselines(self, recipe: str = "", *, include_retired: bool = False) -> list[dict]:
        args = []
        conditions = []
        if recipe:
            conditions.append("recipe = ? COLLATE NOCASE")
            args.append(recipe)
        if not include_retired:
            conditions.append("state = 'active'")
        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        args = [*args, 100]
        with self.store.connection() as db:
            rows = db.execute(
                f"SELECT body FROM baselines{where} ORDER BY recipe ASC, version DESC LIMIT ?", args
            ).fetchall()
        return [summarize(BrightnessBaseline.from_dict(json.loads(row["body"]))) for row in rows]

    def recipes(self) -> list[dict]:
        with self.store.connection() as db:
            rows = db.execute("""
                SELECT recipe,
                       SUM(CASE WHEN state='active' THEN 1 ELSE 0 END) AS active_versions,
                       COUNT(*) AS versions,
                       MAX(version) AS latest_version,
                       MAX(updated) AS updated
                FROM baselines GROUP BY recipe ORDER BY recipe
            """).fetchall()
        return [dict(row) for row in rows]
    def active_for_recipe(self, recipe: str) -> BrightnessBaseline | None:
        with self.store.connection() as db:
            row = db.execute(
                "SELECT body FROM baselines WHERE recipe=? COLLATE NOCASE AND state='active' "
                "ORDER BY version DESC LIMIT 1",
                (recipe,),
            ).fetchone()
        return BrightnessBaseline.from_dict(json.loads(row["body"])) if row else None

    def next_version(self, recipe: str) -> int:
        with self.store.connection() as db:
            row = db.execute(
                "SELECT COALESCE(MAX(version), 0) AS latest FROM baselines WHERE recipe=? COLLATE NOCASE",
                (recipe,),
            ).fetchone()
        return int(row["latest"]) + 1

    def save_new(self, baseline: BrightnessBaseline, action: str, details: dict) -> BrightnessBaseline:
        body = baseline.to_dict()
        digest = data_digest(body)
        body["digest"] = digest
        checked = BrightnessBaseline.from_dict(body)
        with self.write() as db:
            if db.execute("SELECT 1 FROM baselines WHERE id=?", (checked.id,)).fetchone():
                raise Issue("This baseline already exists.", 409)
            if db.execute(
                "SELECT 1 FROM baselines WHERE recipe=? AND version=?", (checked.recipe, checked.version)
            ).fetchone():
                raise Issue("That recipe already has this baseline version.", 409)
            db.execute(
                "INSERT INTO baselines(id, recipe, name, version, state, updated, digest, body) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (checked.id, checked.recipe, checked.name, checked.version, checked.state,
                 checked.updated_at, digest, serial_json(checked.to_dict())),
            )
            self.log(db, checked, action, details)
        return checked

    def set_state(self, identifier: str, state: str, details: dict) -> BrightnessBaseline:
        current = self.get(identifier)
        from dataclasses import replace
        updated = replace(current, state=state).touch()
        body = updated.to_dict()
        body["digest"] = data_digest(body)
        checked = BrightnessBaseline.from_dict(body)
        with self.write() as db:
            db.execute("UPDATE baselines SET state=?, updated=?, digest=?, body=? WHERE id=?",
                       (checked.state, checked.updated_at, body["digest"],
                        serial_json(checked.to_dict()), identifier))
            self.log(db, checked, "baseline retired" if state == "retired" else "baseline reactivated", details)
        return checked

    def events(self, identifier: str, limit: int = 80) -> list[dict]:
        file_id(identifier, "Baseline ID")
        with self.store.connection() as db:
            rows = db.execute(
                "SELECT at, action, details FROM baseline_events WHERE baseline=? "
                "ORDER BY serial DESC LIMIT ?",
                (identifier, limit),
            ).fetchall()
        return [
            {"at": row["at"], "action": row["action"], "details": json.loads(row["details"])}
            for row in rows
        ]

    def log(self, db, baseline: BrightnessBaseline, action: str, details: dict):
        db.execute(
            "INSERT INTO baseline_events(baseline, at, action, details) VALUES (?, ?, ?, ?)",
            (baseline.id, baseline.updated_at, action, serial_json(details)),
        )

    @staticmethod
    def new_id() -> str:
        return new_id()


def summarize(baseline: BrightnessBaseline) -> dict:
    firm = sum(1 for cell in baseline.cells if cell.evidence == "firm")
    limited = sum(1 for cell in baseline.cells if cell.evidence == "limited")
    unsupported = sum(1 for cell in baseline.cells if cell.evidence == "unsupported")
    return {
        "id": baseline.id,
        "name": baseline.name,
        "recipe": baseline.recipe,
        "version": baseline.version,
        "state": baseline.state,
        "rows": baseline.rows,
        "cols": baseline.cols,
        "grid": [baseline.rows, baseline.cols],
        "sample_count": len(baseline.sources),
        "cell_count": len(baseline.cells),
        "firm_cells": firm,
        "limited_cells": limited,
        "unsupported_cells": unsupported,
        "z_limit": baseline.z_limit,
        "low_ratio": baseline.low_ratio,
        "global_dim_ratio": baseline.global_dim_ratio,
        "acquisition": baseline.acquisition,
        "created_at": baseline.created_at,
        "updated_at": baseline.updated_at,
        "digest": baseline.digest,
        "schema": BASELINE_SCHEMA,
        "note": baseline.note,
    }
