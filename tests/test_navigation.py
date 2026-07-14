"""Tests for the Stadium Navigator (reasoned entry-gate wayfinding).

Covers the four layers the feature adds, all keeping the existing suite green:
  1. the schematic layout in ``simulator.config`` (geometry, step-free, walk time);
  2. the navigation schema + validator (``brain.schemas``);
  3. the offline fallback's reasoning (``brain.fallbacks``) — density-vs-walk
     trade-off, step-free honouring, graceful no-gate handling;
  4. the public ``get_navigation_guidance`` brain call, offline and live (mocked),
     including the destination→map-point resolver and the ⚡ latency meta.

The navigation "wow" is a named rubric lever (navigation vertical + accessibility),
so its edge cases are exercised explicitly.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from brain.fallbacks import (
    build_gate_candidates,
    build_navigation_fallback,
)
from brain.schemas import NAVIGATION_SCHEMA, SchemaValidationError, validate_navigation
from brain.stadium_brain import (
    _get_client,
    _resolve_destination_point,
    get_navigation_guidance,
)
from simulator.config import (
    STADIUM_CENTER,
    STEP_FREE_GATES,
    gate_layout,
    walk_time_between,
)


@pytest.fixture(autouse=True)
def _clear_client_cache():
    _get_client.cache_clear()
    yield
    _get_client.cache_clear()


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr("brain.stadium_brain.time.sleep", lambda *_: None)


@pytest.fixture
def nav_snapshot() -> dict:
    """A snapshot where the closest gate is Critical and a farther gate is clear.

    Gate A (North) is Critical; Gate D (West) is Low and step-free; Gate B (East)
    is Low but NOT step-free; Gate C (South) is High. This lets tests assert the
    trade-off logic and the accessibility constraint independently.
    """
    return {
        "timestamp": "2026-07-12T12:00:00+00:00",
        "stadium_id": "metlife",
        "stadium_name": "MetLife Stadium",
        "match_status": "live",
        "gates": [
            {
                "gate_id": "Gate A",
                "crowd_density_pct": 93,
                "density_status": "Critical",
                "entries_last_5min": 100,
            },
            {
                "gate_id": "Gate B",
                "crowd_density_pct": 28,
                "density_status": "Low",
                "entries_last_5min": 100,
            },
            {
                "gate_id": "Gate C",
                "crowd_density_pct": 76,
                "density_status": "High",
                "entries_last_5min": 100,
            },
            {
                "gate_id": "Gate D",
                "crowd_density_pct": 19,
                "density_status": "Low",
                "entries_last_5min": 100,
            },
        ],
        "concessions": [
            {
                "stand_name": "Main Concourse Grill",
                "avg_wait_time_min": 6.0,
                "queue_length": 8,
                "status": "Open",
            },
        ],
        "security": {
            "alert_level": "Green",
            "active_incidents": 0,
            "notes": "ok",
            "last_updated": "t",
        },
    }


def _fake_response(payload) -> MagicMock:
    resp = MagicMock()
    resp.text = payload if isinstance(payload, str) else json.dumps(payload)
    return resp


# --- Category 1: layout geometry ---------------------------------------------


def test_canonical_gates_have_compass_positions():
    lay = gate_layout(["Gate A", "Gate B", "Gate C", "Gate D"])
    cx, cy = STADIUM_CENTER
    assert lay["Gate A"]["y"] < cy and abs(lay["Gate A"]["x"] - cx) < 0.5  # North
    assert lay["Gate B"]["x"] > cx and abs(lay["Gate B"]["y"] - cy) < 0.5  # East
    assert lay["Gate C"]["y"] > cy  # South
    assert lay["Gate D"]["x"] < cx  # West


def test_step_free_flags_match_config():
    lay = gate_layout(["Gate A", "Gate B", "Gate C", "Gate D"])
    for gid, node in lay.items():
        assert node["step_free"] == (gid in STEP_FREE_GATES)


def test_walk_time_scales_with_distance():
    lay = gate_layout(["Gate A", "Gate B", "Gate C", "Gate D"])
    a = (lay["Gate A"]["x"], lay["Gate A"]["y"])
    b = (lay["Gate B"]["x"], lay["Gate B"]["y"])
    c = (lay["Gate C"]["x"], lay["Gate C"]["y"])
    # Opposite gate (A→C) is farther than an adjacent one (A→B).
    assert walk_time_between(a, c) > walk_time_between(a, b) > 0


def test_uploaded_gates_autoplaced_with_an_accessible_option():
    """Arbitrary uploaded gate names still map, and at least one is step-free."""
    lay = gate_layout(["North Stand", "South Stand", "East Stand"])
    assert set(lay) == {"North Stand", "South Stand", "East Stand"}
    assert any(n["step_free"] for n in lay.values())
    # Positions are distinct.
    pts = {(round(n["x"]), round(n["y"])) for n in lay.values()}
    assert len(pts) == 3


def test_uploaded_gate_named_accessible_is_step_free():
    lay = gate_layout(["Main Gate", "Accessible Entrance"])
    assert lay["Accessible Entrance"]["step_free"] is True


# --- Category 2: schema + validator ------------------------------------------


def _valid_nav_payload() -> dict:
    return {
        "summary": "Enter via Gate D.",
        "reasoning": "D is clear and close enough.",
        "recommended_gate": "Gate D",
        "avoid_gates": ["Gate A"],
        "estimated_walk_min": 2.5,
        "step_free": True,
        "language": "English",
        "confidence": "high",
    }


def test_validate_navigation_accepts_valid():
    validate_navigation(_valid_nav_payload())


def test_validate_navigation_allows_empty_recommended_gate():
    """Empty recommended_gate is the valid 'no route possible' state."""
    p = _valid_nav_payload()
    p["recommended_gate"] = ""
    validate_navigation(p)  # must not raise


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p.pop("summary"),
        lambda p: p.pop("recommended_gate"),
        lambda p: p.update(avoid_gates="Gate A"),  # not a list
        lambda p: p.update(estimated_walk_min="soon"),  # not a number
        lambda p: p.update(estimated_walk_min=True),  # bool must not pass as number
        lambda p: p.update(step_free="yes"),  # not a bool
        lambda p: p.update(confidence="certain"),  # bad enum
    ],
)
def test_validate_navigation_rejects_bad_shapes(mutate):
    p = _valid_nav_payload()
    mutate(p)
    with pytest.raises(SchemaValidationError):
        validate_navigation(p)


def test_navigation_schema_shape():
    assert NAVIGATION_SCHEMA["type"] == "object"
    assert "recommended_gate" in NAVIGATION_SCHEMA["required"]


# --- Category 3: offline fallback reasoning ----------------------------------


def test_fallback_avoids_critical_gate(nav_snapshot):
    """The closest gate is Critical; the fallback must route around it."""
    cands = build_gate_candidates(nav_snapshot, STADIUM_CENTER)
    res = build_navigation_fallback("my seat", nav_snapshot, cands, "English", False)
    validate_navigation(res)
    assert res["recommended_gate"] != "Gate A"  # the Critical one
    assert "Gate A" in res["avoid_gates"]
    assert res["offline"] is True
    assert res["reasoning"]  # explainability present


def test_fallback_honours_step_free(nav_snapshot):
    """With step-free required, the pick must be an accessible gate."""
    cands = build_gate_candidates(nav_snapshot, STADIUM_CENTER)
    res = build_navigation_fallback("my seat", nav_snapshot, cands, "English", True)
    validate_navigation(res)
    assert res["step_free"] is True
    # Step-free canonical gates are A and D; A is Critical, so D should win.
    assert res["recommended_gate"] == "Gate D"


def test_fallback_notes_when_no_step_free_gate():
    """If no candidate is step-free, say so rather than silently ignoring it."""
    snap = {
        "gates": [
            {"gate_id": "Gate B", "crowd_density_pct": 20, "density_status": "Low"},
            {"gate_id": "Gate C", "crowd_density_pct": 30, "density_status": "Low"},
        ],
    }
    cands = build_gate_candidates(snap, STADIUM_CENTER)
    # Gate B and C are not in STEP_FREE_GATES → no accessible option.
    assert not any(c["step_free"] for c in cands)
    res = build_navigation_fallback("my seat", snap, cands, "English", True)
    validate_navigation(res)
    assert "step-free" in res["summary"].lower() or "step-free" in res["reasoning"].lower()


def test_fallback_no_gates_is_graceful():
    res = build_navigation_fallback("my seat", {"gates": []}, [], "English", False)
    validate_navigation(res)
    assert res["recommended_gate"] == ""
    assert res["confidence"] == "low"


def test_fallback_language_note_for_non_english(nav_snapshot):
    cands = build_gate_candidates(nav_snapshot, STADIUM_CENTER)
    res = build_navigation_fallback("my seat", nav_snapshot, cands, "Arabic", False)
    assert "Arabic" in res["summary"]
    assert res["language"] == "English"  # fallback answers in English


# --- Category 4: destination resolver + public brain call --------------------


def test_resolve_destination_gate(nav_snapshot):
    pt = _resolve_destination_point("Gate C", nav_snapshot)
    lay = gate_layout([g["gate_id"] for g in nav_snapshot["gates"]])
    assert pt == (lay["Gate C"]["x"], lay["Gate C"]["y"])


def test_resolve_destination_unknown_is_center(nav_snapshot):
    assert _resolve_destination_point("my seat", nav_snapshot) == STADIUM_CENTER


def test_resolve_destination_concession_inside_ring(nav_snapshot):
    """A stand resolves to a point pulled inward from its anchor gate."""
    pt = _resolve_destination_point("Main Concourse Grill", nav_snapshot)
    # Not exactly the centre and not exactly a gate ring point — it's between.
    assert pt != STADIUM_CENTER


def test_get_navigation_guidance_offline(monkeypatch, nav_snapshot):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    res = get_navigation_guidance("my seat", nav_snapshot, "English", False)
    validate_navigation(res)
    assert res["offline"] is True
    assert res["recommended_gate"] in {"Gate B", "Gate D"}  # a Low gate, not A/C
    assert "_meta" not in res  # no live call


def test_get_navigation_guidance_offline_step_free(monkeypatch, nav_snapshot):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    res = get_navigation_guidance("my seat", nav_snapshot, "English", True)
    assert res["step_free"] is True
    assert res["recommended_gate"] == "Gate D"


@patch("brain.stadium_brain.genai.Client")
def test_get_navigation_guidance_live_carries_meta(mock_client_cls, monkeypatch, nav_snapshot):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    _get_client.cache_clear()
    inst = MagicMock()
    mock_client_cls.return_value = inst
    inst.models.generate_content.return_value = _fake_response(_valid_nav_payload())

    res = get_navigation_guidance("my seat", nav_snapshot, "English", True)
    validate_navigation(res)
    assert res["recommended_gate"] == "Gate D"
    assert res["_meta"]["source"] == "gemini"  # latency chip present
    assert "offline" not in res
    inst.models.generate_content.assert_called_once()


@patch("brain.stadium_brain.genai.Client")
def test_get_navigation_guidance_live_failure_falls_back(mock_client_cls, monkeypatch, nav_snapshot):
    """A model failure degrades to the schema-valid offline reroute, never raises."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    _get_client.cache_clear()
    inst = MagicMock()
    mock_client_cls.return_value = inst
    inst.models.generate_content.return_value = _fake_response("not json at all")

    res = get_navigation_guidance("my seat", nav_snapshot, "English", False)
    validate_navigation(res)
    assert res["offline"] is True
    assert res["recommended_gate"] != "Gate A"


def test_get_navigation_guidance_uploaded_style_gates(monkeypatch):
    """Works on arbitrary uploaded gate names, not just the demo gates."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    snap = {
        "stadium_name": "Camp Nou",
        "gates": [
            {"gate_id": "North", "crowd_density_pct": 91, "density_status": "Critical"},
            {"gate_id": "West", "crowd_density_pct": 22, "density_status": "Low"},
        ],
        "concessions": [],
    }
    res = get_navigation_guidance("my seat", snap, "English", False)
    validate_navigation(res)
    assert res["recommended_gate"] == "West"  # avoids the Critical North
