"""Evaluator data-ingestion path.

The live app is normally driven by :mod:`simulator.data_simulator`, but the
challenge is functionally evaluated by a jury who bring *their own* real data.
This module lets an evaluator upload a CSV or JSON file and have the entire app
(fan assistant + operations dashboard) run against it, exactly as it runs
against the simulator — no code path is special-cased for "demo data".

Two responsibilities:

1. **Parse** a forgiving range of CSV / JSON shapes into the internal snapshot
   schema (:func:`parse_csv`, :func:`parse_json`, :func:`build_snapshot_from_upload`).
2. **Normalize + validate** any snapshot-shaped dict into a guaranteed-clean
   snapshot (:func:`normalize_snapshot`) — clamping ranges, deriving
   ``density_status`` from the shared banding rule, and coercing types — so the
   downstream brain/UI can trust every field regardless of how messy the upload
   was.

Design principle: *be liberal in what you accept*. Evaluators will not know our
exact column names, so we alias generously and collect human-readable warnings
rather than hard-failing on the first surprise. A file is rejected outright only
when it contains no recognisable gate or concession data at all.
"""

from __future__ import annotations

import csv
import datetime
import io
import json
import re
from typing import Any

from simulator.config import (
    SECURITY_LEVELS,
    density_status_for_pct,
)


class SnapshotIngestionError(ValueError):
    """Raised when an uploaded file cannot be interpreted as stadium data."""


# ---------------------------------------------------------------------------
# Column aliases — map the many names an evaluator might use onto our fields.
# All comparisons are done on a normalised key (lower-cased, spaces/hyphens ->
# underscores), so "Crowd Density (%)" and "crowd-density" both resolve here.
# ---------------------------------------------------------------------------

_GATE_ID_ALIASES = ("gate_id", "gate", "gate_name", "gatename", "gate_no", "gate_number")
_DENSITY_ALIASES = (
    "crowd_density_pct",
    "crowd_density",
    "density_pct",
    "density",
    "occupancy_pct",
    "occupancy",
    "crowd_density_%",
    "crowd_density_percent",
    "fill_pct",
    "fill",
)
_ENTRIES_ALIASES = (
    "entries_last_5min",
    "entries",
    "entry_count",
    "entries_5min",
    "entries_5m",
    "throughput",
    "entries_last_5_min",
)
_STAND_ALIASES = (
    "stand_name",
    "stand",
    "concession",
    "concession_name",
    "concession_stand",
    "vendor",
    "outlet",
    "kiosk",
)
_WAIT_ALIASES = (
    "avg_wait_time_min",
    "avg_wait_time",
    "wait_time",
    "wait",
    "avg_wait",
    "wait_min",
    "wait_minutes",
    "wait_time_min",
    "avg_wait_min",
    "queue_wait",
)
_QUEUE_ALIASES = (
    "queue_length",
    "queue",
    "queue_len",
    "people_in_queue",
    "queue_size",
    "line_length",
    "queue_count",
)
_STATUS_ALIASES = ("status", "state", "operational_status")
_TYPE_ALIASES = ("entity_type", "type", "category", "kind", "entity")

# Security fields an uploaded row/column might carry.
_ALERT_LEVEL_ALIASES = (
    "alert_level",
    "security_level",
    "security_alert_level",
    "threat_level",
    "level",
)
_INCIDENTS_ALIASES = (
    "active_incidents",
    "incidents",
    "incident_count",
    "num_incidents",
)
_NOTES_ALIASES = ("notes", "note", "security_notes", "remarks", "comment")

# Values in a discriminator column that mean "this row is a gate / concession /
# security summary".
_GATE_TYPE_VALUES = {"gate", "gates", "entrance", "entry", "turnstile"}
_CONCESSION_TYPE_VALUES = {
    "concession",
    "concessions",
    "stand",
    "food",
    "vendor",
    "kiosk",
    "outlet",
}
_SECURITY_TYPE_VALUES = {"security", "alert", "incident", "safety"}

# Concession wait (minutes) at/above which a stand is flagged "High Demand" —
# mirrors the simulator's own status banding so uploaded and simulated data
# render identically.
_HIGH_DEMAND_WAIT_MIN = 15.0


# ---------------------------------------------------------------------------
# Small parsing helpers
# ---------------------------------------------------------------------------


def _norm_key(key: str) -> str:
    """Normalise a column header for alias lookup.

    Lower-cases, drops parenthesised units and stray punctuation, and collapses
    whitespace/hyphens to single underscores, so headers like
    ``"Crowd Density (%)"`` and ``"crowd-density"`` both resolve to
    ``"crowd_density"``.
    """
    text = str(key).strip().lower()
    # Drop characters that only ever appear as decoration in a header.
    for ch in "()[]{}%.:":
        text = text.replace(ch, " ")
    text = text.replace("-", " ").replace("/", " ")
    # Collapse any run of whitespace/underscores into a single underscore.
    parts = [p for p in text.replace("_", " ").split() if p]
    return "_".join(parts)


def _first_present(row: dict[str, Any], aliases: tuple[str, ...]) -> Any | None:
    """Return the first value in *row* whose (normalised) key is in *aliases*."""
    for alias in aliases:
        if alias in row and str(row[alias]).strip() != "":
            return row[alias]
    return None


def _to_float(value: Any, default: float = 0.0) -> float:
    """Coerce a possibly-dirty cell ("28 mins", "85%", "1,024", "30 minutes") to a float.

    Extracts the first signed decimal number in the string, so surrounding units
    and words ("min", "mins", "minutes", "%", "people", "approx") are irrelevant.
    Thousands separators are removed first. This is unit-order-independent — the
    naive approach of stripping unit substrings mis-parsed plural forms like
    "28 mins" (stripping "min" left "28 s"), silently zeroing real wait times.
    """
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().lower()
    if text in ("", "n/a", "na", "none", "null", "-"):
        return default
    # Drop thousands separators, then grab the first signed decimal number.
    match = re.search(r"[-+]?\d*\.?\d+", text.replace(",", ""))
    if match is None:
        return default
    try:
        return float(match.group())
    except ValueError:
        return default


def _to_int(value: Any, default: int = 0) -> int:
    """Coerce a possibly-dirty cell to a non-negative-friendly int (via float)."""
    return int(round(_to_float(value, float(default))))


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


# ---------------------------------------------------------------------------
# Snapshot normalisation — the trust boundary for all upstream data
# ---------------------------------------------------------------------------


def _normalize_gates(raw: dict[str, Any], warn: list[str]) -> list[dict[str, Any]]:
    """Clean the gate rows: coerce/clamp density and recompute status."""
    gates_out: list[dict[str, Any]] = []
    for i, gate in enumerate(raw.get("gates", []) or []):
        if not isinstance(gate, dict):
            warn.append(f"Skipped gate row {i}: not an object.")
            continue
        pct = int(_clamp(_to_float(gate.get("crowd_density_pct"), 0.0), 0, 100))
        gates_out.append(
            {
                "gate_id": str(gate.get("gate_id") or f"Gate {i + 1}"),
                "crowd_density_pct": pct,
                # Recompute so status and percentage can never disagree.
                "density_status": density_status_for_pct(pct),
                "entries_last_5min": max(0, _to_int(gate.get("entries_last_5min"), 0)),
            }
        )
    return gates_out


def _normalize_concessions(raw: dict[str, Any], warn: list[str]) -> list[dict[str, Any]]:
    """Clean the concession rows: coerce wait/queue and derive a status if absent."""
    concessions_out: list[dict[str, Any]] = []
    for i, stand in enumerate(raw.get("concessions", []) or []):
        if not isinstance(stand, dict):
            warn.append(f"Skipped concession row {i}: not an object.")
            continue
        wait = round(max(0.0, _to_float(stand.get("avg_wait_time_min"), 0.0)), 1)
        status = stand.get("status")
        if not status:
            status = "High Demand" if wait >= _HIGH_DEMAND_WAIT_MIN else "Open"
        concessions_out.append(
            {
                "stand_name": str(stand.get("stand_name") or f"Stand {i + 1}"),
                "avg_wait_time_min": wait,
                "queue_length": max(0, _to_int(stand.get("queue_length"), 0)),
                "status": str(status),
            }
        )
    return concessions_out


def _normalize_security(raw: dict[str, Any], warn: list[str], now_iso: str) -> dict[str, Any]:
    """Clean the security block: accept a dict or bare string level, default Green."""
    raw_sec = raw.get("security", {})
    if isinstance(raw_sec, str):
        raw_sec = {"alert_level": raw_sec}
    if not isinstance(raw_sec, dict):
        raw_sec = {}
    level = str(raw_sec.get("alert_level", "Green")).strip().capitalize()
    if level not in SECURITY_LEVELS:
        if raw_sec.get("alert_level"):
            warn.append(f"Unknown security alert_level '{raw_sec.get('alert_level')}'; defaulted to 'Green'.")
        level = "Green"
    return {
        "alert_level": level,
        "last_updated": str(raw_sec.get("last_updated") or now_iso),
        "active_incidents": max(0, _to_int(raw_sec.get("active_incidents"), 0)),
        "notes": str(raw_sec.get("notes") or "Imported from uploaded dataset"),
    }


def normalize_snapshot(
    raw: dict[str, Any],
    warnings: list[str] | None = None,
    default_stadium_id: str = "uploaded",
    default_stadium_name: str = "Uploaded Dataset",
) -> dict[str, Any]:
    """Coerce any snapshot-shaped dict into a guaranteed-clean snapshot.

    Every numeric field is coerced and range-clamped; every ``density_status``
    is *recomputed* from ``crowd_density_pct`` via the shared banding rule
    (uploaded status labels are ignored so they can never disagree with the
    percentage the app charts). Unknown security levels fall back to ``Green``.
    The gate/concession/security cleaning is delegated to focused helpers.

    Args:
        raw: A dict that at least loosely resembles a snapshot.
        warnings: Optional list to append human-readable normalisation notes to.
        default_stadium_id / default_stadium_name: Used when the upload omits them.

    Returns:
        A snapshot dict safe for the brain and UI to consume, tagged
        ``source="upload"``.
    """
    warn = warnings if warnings is not None else []
    now_iso = datetime.datetime.now(datetime.UTC).isoformat()

    gates_out = _normalize_gates(raw, warn)
    concessions_out = _normalize_concessions(raw, warn)
    security_out = _normalize_security(raw, warn, now_iso)

    if not gates_out and not concessions_out:
        raise SnapshotIngestionError(
            "No gate or concession data could be read from the file. Expected "
            "gate rows (with a gate id + density) and/or concession rows "
            "(with a stand name + wait time)."
        )

    return {
        "timestamp": str(raw.get("timestamp") or now_iso),
        "stadium_id": str(raw.get("stadium_id") or default_stadium_id),
        "stadium_name": str(raw.get("stadium_name") or default_stadium_name),
        "match_status": str(raw.get("match_status") or "uploaded"),
        "gates": gates_out,
        "concessions": concessions_out,
        "security": security_out,
        "source": "upload",
    }


# ---------------------------------------------------------------------------
# CSV parsing
# ---------------------------------------------------------------------------


def _row_looks_like_gate(row: dict[str, Any]) -> bool:
    return _first_present(row, _GATE_ID_ALIASES) is not None and _first_present(row, _DENSITY_ALIASES) is not None


def _row_looks_like_concession(row: dict[str, Any]) -> bool:
    return _first_present(row, _STAND_ALIASES) is not None and _first_present(row, _WAIT_ALIASES) is not None


def _row_looks_like_security(row: dict[str, Any]) -> bool:
    return _first_present(row, _ALERT_LEVEL_ALIASES) is not None


def _classify_row(row: dict[str, Any]) -> str | None:
    """Decide whether a CSV row is a ``gate``, ``concession``, ``security`` (or ``None``)."""
    disc = _first_present(row, _TYPE_ALIASES)
    if disc is not None:
        token = str(disc).strip().lower()
        if token in _GATE_TYPE_VALUES:
            return "gate"
        if token in _CONCESSION_TYPE_VALUES:
            return "concession"
        if token in _SECURITY_TYPE_VALUES:
            return "security"
    # No usable discriminator — infer from which columns are populated. Prefer a
    # concession match when both a wait time and a density are present but only a
    # stand name is available, and vice versa.
    is_gate = _row_looks_like_gate(row)
    is_conc = _row_looks_like_concession(row)
    if is_gate and not is_conc:
        return "gate"
    if is_conc and not is_gate:
        return "concession"
    if is_gate and is_conc:
        # Ambiguous single row carrying both — treat as a gate (density-primary).
        return "gate"
    # A row with only a security alert level (and no gate/concession signal).
    if _row_looks_like_security(row):
        return "security"
    return None


def parse_csv(text: str, warnings: list[str] | None = None) -> dict[str, Any]:
    """Parse CSV text into a (pre-normalisation) snapshot-shaped dict.

    Accepts either a single table mixing gate and concession rows (disambiguated
    by an ``entity_type``/``type`` column) or a table of one kind inferred from
    its columns. Column names are matched case-insensitively through a wide set
    of aliases.

    Args:
        text: Decoded CSV file contents.
        warnings: Optional list to collect per-row skip notes.

    Returns:
        A dict with ``gates`` and ``concessions`` lists (and a ``security`` block
        if any security row/column was present), ready for
        :func:`normalize_snapshot`.

    Raises:
        SnapshotIngestionError: If the CSV has no header or no usable rows.
    """
    warn = warnings if warnings is not None else []
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        raise SnapshotIngestionError("CSV file appears to be empty (no header row).")

    gates: list[dict[str, Any]] = []
    concessions: list[dict[str, Any]] = []
    security: dict[str, Any] = {}

    for lineno, raw_row in enumerate(reader, start=2):  # line 1 is the header
        # Normalise keys; drop the DictReader ``None`` key (extra columns).
        row = {_norm_key(k): v for k, v in raw_row.items() if k is not None}
        if not any(str(v).strip() for v in row.values()):
            continue  # blank line

        kind = _classify_row(row)
        if kind == "gate":
            gates.append(
                {
                    "gate_id": _first_present(row, _GATE_ID_ALIASES),
                    "crowd_density_pct": _first_present(row, _DENSITY_ALIASES),
                    "entries_last_5min": _first_present(row, _ENTRIES_ALIASES),
                }
            )
        elif kind == "concession":
            concessions.append(
                {
                    "stand_name": _first_present(row, _STAND_ALIASES),
                    "avg_wait_time_min": _first_present(row, _WAIT_ALIASES),
                    "queue_length": _first_present(row, _QUEUE_ALIASES),
                    "status": _first_present(row, _STATUS_ALIASES),
                }
            )
        elif kind == "security":
            # Last security row wins; normalisation validates the level.
            security = {
                "alert_level": _first_present(row, _ALERT_LEVEL_ALIASES),
                "active_incidents": _first_present(row, _INCIDENTS_ALIASES),
                "notes": _first_present(row, _NOTES_ALIASES),
            }
        else:
            warn.append(f"Line {lineno}: could not classify row as gate, concession, or security; skipped.")

    if not gates and not concessions:
        raise SnapshotIngestionError(
            "CSV parsed but no gate or concession rows were recognised. Ensure "
            "columns include a gate id + density, and/or a stand name + wait time."
        )

    result: dict[str, Any] = {"gates": gates, "concessions": concessions}
    if security:
        result["security"] = security
    return result


# ---------------------------------------------------------------------------
# JSON parsing
# ---------------------------------------------------------------------------


def parse_json(text: str, warnings: list[str] | None = None) -> dict[str, Any]:
    """Parse JSON text into a (pre-normalisation) snapshot-shaped dict.

    Accepts a full snapshot object, a partial object carrying any of
    ``gates`` / ``concessions`` / ``security``, or a bare list (treated as gates
    if the items look like gates, else concessions).

    Raises:
        SnapshotIngestionError: On invalid JSON or an unusable top-level shape.
    """
    warn = warnings if warnings is not None else []
    try:
        data = json.loads(text)
    except json.JSONDecodeError as err:
        raise SnapshotIngestionError(f"Invalid JSON: {err}") from err

    # Field maps reused for both the nested-object and bare-array cases.
    gate_field_map = {
        "gate_id": _GATE_ID_ALIASES,
        "crowd_density_pct": _DENSITY_ALIASES,
        "entries_last_5min": _ENTRIES_ALIASES,
    }
    concession_field_map = {
        "stand_name": _STAND_ALIASES,
        "avg_wait_time_min": _WAIT_ALIASES,
        "queue_length": _QUEUE_ALIASES,
        "status": _STATUS_ALIASES,
    }

    # Re-key rows through the alias map so JSON is as forgiving as CSV.
    def _remap(rows: Any, field_map) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for item in rows or []:
            if not isinstance(item, dict):
                continue
            norm = {_norm_key(k): v for k, v in item.items()}
            record: dict[str, Any] = {}
            for target, aliases in field_map.items():
                record[target] = _first_present(norm, aliases)
            out.append(record)
        return out

    if isinstance(data, list):
        # A bare array may mix gate and concession objects. Classify each item on
        # its own keys (like CSV rows) rather than trusting only data[0], which
        # would misclassify the whole array when the first item is unrepresentative.
        gate_rows: list[dict[str, Any]] = []
        conc_rows: list[dict[str, Any]] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            keys = {_norm_key(k) for k in item}
            is_gate = bool(keys & set(_DENSITY_ALIASES) or keys & set(_GATE_ID_ALIASES))
            is_conc = bool(keys & set(_WAIT_ALIASES) or keys & set(_STAND_ALIASES))
            if is_gate and not is_conc:
                gate_rows.append(item)
            elif is_conc and not is_gate:
                conc_rows.append(item)
            elif is_gate and is_conc:
                gate_rows.append(item)  # density-primary tiebreak, as in CSV
            # else: unclassifiable item — skipped
        warn.append(
            f"Top-level JSON array classified per-item: {len(gate_rows)} gate(s), {len(conc_rows)} concession(s)."
        )
        return {
            "gates": _remap(gate_rows, gate_field_map),
            "concessions": _remap(conc_rows, concession_field_map),
        }

    if not isinstance(data, dict):
        raise SnapshotIngestionError("JSON must be an object (snapshot) or an array of rows.")

    gates = _remap(data.get("gates"), gate_field_map)
    concessions = _remap(data.get("concessions"), concession_field_map)

    return {
        "timestamp": data.get("timestamp"),
        "stadium_id": data.get("stadium_id"),
        "stadium_name": data.get("stadium_name"),
        "match_status": data.get("match_status"),
        "gates": gates,
        "concessions": concessions,
        "security": data.get("security", {}),
    }


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def build_snapshot_from_upload(
    data: bytes,
    filename: str,
) -> tuple[dict[str, Any], list[str]]:
    """Turn raw uploaded file bytes into a clean, normalised snapshot.

    Dispatches on the file extension (``.json`` vs ``.csv``/anything else),
    parses, then normalises. All soft issues are returned as warnings rather
    than raised, so the caller can surface them without losing a usable snapshot.

    Args:
        data: Raw bytes of the uploaded file.
        filename: Original filename (used only to pick the parser by extension).

    Returns:
        ``(snapshot, warnings)`` — a normalised snapshot and a list of notes.

    Raises:
        SnapshotIngestionError: If the file cannot be decoded or contains no
            recognisable stadium data.
    """
    warnings: list[str] = []
    try:
        text = data.decode("utf-8-sig")  # tolerate a UTF-8 BOM from Excel exports
    except UnicodeDecodeError as err:
        raise SnapshotIngestionError(f"File is not valid UTF-8 text: {err}") from err

    lower = filename.lower()
    if lower.endswith(".json"):  # noqa: SIM108 - explicit branches document the CSV default
        raw = parse_json(text, warnings)
    else:
        # Default to CSV for .csv and unknown/extension-less uploads.
        raw = parse_csv(text, warnings)

    snapshot = normalize_snapshot(raw, warnings)
    return snapshot, warnings
