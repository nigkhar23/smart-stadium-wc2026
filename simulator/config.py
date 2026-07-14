"""Configuration constants for the Smart Stadium Data Simulator.

This module defines the static metadata for stadiums, gates, concession stands,
and security levels to be used by the simulation logic.
"""

import math
from typing import TypedDict


class GatePosition(TypedDict):
    """Schematic map position + accessibility flag for a single gate."""

    x: float
    y: float
    angle: float
    step_free: bool


# Stadium metadata definitions
STADIUMS: list[dict[str, str | int]] = [
    {
        "stadium_id": "metlife",
        "name": "MetLife Stadium",
        "city": "East Rutherford, NJ",
        "capacity": 82500,
    },
    {
        "stadium_id": "azteca",
        "name": "Estadio Azteca",
        "city": "Mexico City",
        "capacity": 83000,
    },
    {
        "stadium_id": "bcplace",
        "name": "BC Place",
        "city": "Vancouver",
        "capacity": 54500,
    },
]

# Gate identifiers
GATES: list[str] = ["Gate A", "Gate B", "Gate C", "Gate D"]

# Concession stand types/names
CONCESSION_STANDS: list[str] = [
    "Main Concourse Grill",
    "Craft Beer & Snacks",
    "International Food Court",
    "Family Fan Zone Kiosk",
]

# Security level definitions
SECURITY_LEVELS: list[str] = ["Green", "Yellow", "Orange", "Red"]

# Probability weights corresponding to [Green, Yellow, Orange, Red]
# green ~ 85%, yellow ~ 10%, orange ~ 4%, red ~ 1%
SECURITY_LEVEL_WEIGHTS: list[float] = [0.85, 0.10, 0.04, 0.01]

# Crowd-density status thresholds (upper-inclusive percentage bounds).
# Shared by the live simulator and the evaluator data-ingestion path so a
# gate at the same density is always classified identically, regardless of
# whether the reading came from the simulator or an uploaded CSV/JSON.
DENSITY_STATUS_THRESHOLDS: list[tuple] = [
    (40, "Low"),
    (70, "Moderate"),
    (90, "High"),
]
DENSITY_STATUS_CRITICAL: str = "Critical"


def density_status_for_pct(pct: float) -> str:
    """Map a crowd-density percentage to its text status.

    A single source of truth for the ``Low``/``Moderate``/``High``/``Critical``
    banding used everywhere in the app. ``> 90%`` is ``Critical`` — the level
    the operations pre-check escalates on.

    Args:
        pct: Crowd density percentage (any real number; typically 0–100).

    Returns:
        One of ``"Low"``, ``"Moderate"``, ``"High"``, ``"Critical"``.
    """
    for upper, label in DENSITY_STATUS_THRESHOLDS:
        if pct <= upper:
            return label
    return DENSITY_STATUS_CRITICAL


# ---------------------------------------------------------------------------
# Stadium spatial layout — a schematic map for the navigation feature
# ---------------------------------------------------------------------------
# A stadium is drawn as an oval concourse with gates spaced around its ring.
# Coordinates live in a normalised 0–100 space (x right, y down) so any renderer
# (SVG, pydeck, plotly) can scale them without hard-coding pixels. This is a
# *schematic* map, not GPS — it needs no external tiles, API key, or billing,
# which is exactly why the navigation "wow" ships without a cloud account.
#
# ``STEP_FREE_GATES`` marks which gates have level/ramped access (no stairs or
# escalator-only entry). The navigator's accessibility toggle routes a fan to
# one of these when step-free access is requested — a named rubric dimension.

# Centre of the pitch/oval in the normalised space, and the ring radii.
STADIUM_CENTER: tuple[float, float] = (50.0, 50.0)
_RING_RX: float = 42.0  # horizontal radius of the gate ring
_RING_RY: float = 38.0  # vertical radius (slightly squashed → stadium oval)

# Fixed compass positions for the four canonical simulator gates. Angles are in
# degrees measured clockwise from the top (12 o'clock = North = 0°).
_CANONICAL_GATE_ANGLES: dict[str, float] = {
    "Gate A": 0.0,  # North
    "Gate B": 90.0,  # East
    "Gate C": 180.0,  # South
    "Gate D": 270.0,  # West
}

# Gates with step-free (wheelchair-accessible) entry among the canonical set.
# Two of four, on opposite sides, so an accessible reroute is always plausible.
STEP_FREE_GATES: frozenset = frozenset({"Gate A", "Gate D"})

# Typical walking speed inside a crowded concourse (normalised units per minute).
# Tuned so a half-perimeter walk (~gate to opposite gate) reads as a few minutes,
# matching the plan's "~2 min further" framing.
_WALK_UNITS_PER_MIN: float = 30.0


def _angle_to_point(angle_deg: float) -> tuple[float, float]:
    """Map a clockwise-from-top angle to an (x, y) point on the gate ring."""
    rad = math.radians(angle_deg)
    cx, cy = STADIUM_CENTER
    x = cx + _RING_RX * math.sin(rad)
    y = cy - _RING_RY * math.cos(rad)  # screen y grows downward
    return (round(x, 2), round(y, 2))


def gate_layout(gate_ids: list[str]) -> dict[str, GatePosition]:
    """Return a schematic map position + accessibility flag for each gate id.

    Canonical gates ("Gate A".."Gate D") keep fixed compass positions so the
    demo map is stable. Any *other* names — the arbitrary gates an evaluator
    uploads ("North", "Gate 7", …) — are spread evenly around the ring in the
    order given, so an uploaded dataset still renders a sensible map with zero
    hard-coding. A non-canonical gate is treated as step-free only if its name
    hints at accessibility; otherwise the first uploaded gate is assumed
    accessible so an accessible route always exists.

    Args:
        gate_ids: The gate identifiers present in the current snapshot.

    Returns:
        ``{gate_id: {"x": float, "y": float, "angle": float, "step_free": bool}}``.
    """
    layout: dict[str, GatePosition] = {}
    non_canonical = [g for g in gate_ids if g not in _CANONICAL_GATE_ANGLES]
    n_extra = len(non_canonical)
    extra_idx = 0
    for gid in gate_ids:
        if gid in _CANONICAL_GATE_ANGLES:
            angle = _CANONICAL_GATE_ANGLES[gid]
            step_free = gid in STEP_FREE_GATES
        else:
            # Evenly distribute unknown gates around the full ring.
            angle = (360.0 / n_extra) * extra_idx if n_extra else 0.0
            name_l = gid.lower()
            hints = ("access", "step-free", "step free", "ada", "wheelchair", "ramp")
            # Accessible if the name says so, else make the first uploaded gate
            # accessible as a safe default (guarantees a step-free option exists).
            step_free = any(h in name_l for h in hints) or extra_idx == 0
            extra_idx += 1
        x, y = _angle_to_point(angle)
        layout[gid] = {"x": x, "y": y, "angle": round(angle, 2), "step_free": step_free}
    return layout


def walk_time_between(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Estimate walking minutes between two normalised map points.

    Uses straight-line distance / a fixed concourse walking speed. It is a
    deliberately simple, transparent heuristic (a schematic, not routing) — its
    only job is to let the navigator weigh "closer but busier" against "farther
    but clear", which is the trade-off the reasoning explains.
    """
    dist = math.hypot(a[0] - b[0], a[1] - b[1])
    return round(dist / _WALK_UNITS_PER_MIN, 1)


# Languages the fan assistant can answer in. The value is the natural-language
# name handed to the model ("answer in {language}"); the model handles the
# correct script and register. English is the safe default.
SUPPORTED_LANGUAGES: list[str] = [
    "English",
    "Spanish",
    "French",
    "Portuguese",
    "Arabic",
    "German",
    "Hindi",
    "Japanese",
    "Korean",
    "Mandarin Chinese",
]

# Map each supported language to its BCP-47 tag and text direction, so rendered
# answers can carry lang/dir attributes (WCAG 3.1.2 Language of Parts): a screen
# reader then switches voice/pronunciation, and RTL scripts lay out correctly.
_LANGUAGE_META: dict[str, tuple[str, str]] = {
    "English": ("en", "ltr"),
    "Spanish": ("es", "ltr"),
    "French": ("fr", "ltr"),
    "Portuguese": ("pt", "ltr"),
    "Arabic": ("ar", "rtl"),
    "German": ("de", "ltr"),
    "Hindi": ("hi", "ltr"),
    "Japanese": ("ja", "ltr"),
    "Korean": ("ko", "ltr"),
    "Mandarin Chinese": ("zh", "ltr"),
}


def language_bcp47(language: str) -> tuple[str, str]:
    """Return the ``(bcp47_tag, direction)`` for a supported language name.

    Unknown languages fall back to English/``ltr`` so callers always get a
    usable pair.
    """
    return _LANGUAGE_META.get(language, ("en", "ltr"))
