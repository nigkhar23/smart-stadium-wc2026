"""Exhaustive tests for the response validators.

Every ``raise SchemaValidationError`` branch in :mod:`brain.schemas` is exercised
here — the validators are the trust boundary between the LLM and the UI, so each
rejection path (missing key, wrong type, bad enum, malformed nested item) is a
behaviour worth pinning, not just a line to cover. A valid payload for each
schema is asserted to pass so the "happy path" can't silently regress either.
"""

import pytest

from brain.schemas import (
    SchemaValidationError,
    validate_fan_assistant,
    validate_navigation,
    validate_ops_alert,
)

# ---------------------------------------------------------------------------
# Valid baselines — mutated per-test to isolate each failure branch.
# ---------------------------------------------------------------------------


def _valid_fan() -> dict:
    return {
        "answer": "Head to Gate A.",
        "reasoning": "It has the lowest density.",
        "recommendation": {"type": "gate", "name": "Gate A", "reason": "low density"},
        "language": "English",
        "data_snapshot_timestamp": "2026-07-14T12:00:00+00:00",
        "confidence": "high",
    }


def _valid_ops() -> dict:
    return {
        "alert_triggered": True,
        "severity": "warning",
        "triggers": [{"type": "queue_time", "location": "Grill", "value": 30.0, "threshold_breached": 25}],
        "reasoning": "One queue over threshold.",
        "recommended_action": "Open another till.",
        "generated_at": "2026-07-14T12:00:00+00:00",
    }


def _valid_nav() -> dict:
    return {
        "summary": "Use Gate A.",
        "reasoning": "Closest step-free gate with low crowding.",
        "recommended_gate": "Gate A",
        "avoid_gates": ["Gate C"],
        "estimated_walk_min": 4,
        "step_free": True,
        "language": "English",
        "confidence": "high",
    }


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


def test_valid_payloads_pass():
    validate_fan_assistant(_valid_fan())
    validate_ops_alert(_valid_ops())
    validate_navigation(_valid_nav())


# ---------------------------------------------------------------------------
# Fan assistant — every rejection branch
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad", [None, [], "x", 3])
def test_fan_root_not_dict(bad):
    with pytest.raises(SchemaValidationError):
        validate_fan_assistant(bad)


def test_fan_missing_key():
    d = _valid_fan()
    del d["answer"]
    with pytest.raises(SchemaValidationError, match="Missing top-level keys"):
        validate_fan_assistant(d)


@pytest.mark.parametrize("field", ["answer", "reasoning", "language", "data_snapshot_timestamp"])
def test_fan_non_string_fields(field):
    d = _valid_fan()
    d[field] = 123
    with pytest.raises(SchemaValidationError):
        validate_fan_assistant(d)


def test_fan_bad_confidence():
    d = _valid_fan()
    d["confidence"] = "certain"
    with pytest.raises(SchemaValidationError, match="confidence"):
        validate_fan_assistant(d)


def test_fan_recommendation_not_dict():
    d = _valid_fan()
    d["recommendation"] = "Gate A"
    with pytest.raises(SchemaValidationError, match="recommendation"):
        validate_fan_assistant(d)


def test_fan_recommendation_missing_keys():
    d = _valid_fan()
    d["recommendation"] = {"type": "gate"}
    with pytest.raises(SchemaValidationError, match="Missing keys in recommendation"):
        validate_fan_assistant(d)


def test_fan_recommendation_bad_type():
    d = _valid_fan()
    d["recommendation"] = {"type": "portal", "name": "x", "reason": "y"}
    with pytest.raises(SchemaValidationError, match="recommendation 'type'"):
        validate_fan_assistant(d)


@pytest.mark.parametrize("field", ["name", "reason"])
def test_fan_recommendation_non_string(field):
    d = _valid_fan()
    d["recommendation"][field] = 5
    with pytest.raises(SchemaValidationError):
        validate_fan_assistant(d)


# ---------------------------------------------------------------------------
# Ops alert — every rejection branch
# ---------------------------------------------------------------------------


def test_ops_root_not_dict():
    with pytest.raises(SchemaValidationError):
        validate_ops_alert(["not", "a", "dict"])


def test_ops_missing_key():
    d = _valid_ops()
    del d["severity"]
    with pytest.raises(SchemaValidationError, match="Missing top-level keys"):
        validate_ops_alert(d)


def test_ops_alert_triggered_not_bool():
    d = _valid_ops()
    d["alert_triggered"] = "yes"
    with pytest.raises(SchemaValidationError, match="alert_triggered"):
        validate_ops_alert(d)


def test_ops_bad_severity():
    d = _valid_ops()
    d["severity"] = "high"  # not in the allowed enum
    with pytest.raises(SchemaValidationError, match="severity"):
        validate_ops_alert(d)


@pytest.mark.parametrize("field", ["reasoning", "recommended_action", "generated_at"])
def test_ops_non_string_fields(field):
    d = _valid_ops()
    d[field] = 0
    with pytest.raises(SchemaValidationError):
        validate_ops_alert(d)


def test_ops_triggers_not_list():
    d = _valid_ops()
    d["triggers"] = {"type": "queue_time"}
    with pytest.raises(SchemaValidationError, match="'triggers' must be a list"):
        validate_ops_alert(d)


def test_ops_trigger_item_not_dict():
    d = _valid_ops()
    d["triggers"] = ["oops"]
    with pytest.raises(SchemaValidationError, match="must be a dictionary"):
        validate_ops_alert(d)


def test_ops_trigger_missing_keys():
    d = _valid_ops()
    d["triggers"] = [{"type": "queue_time"}]
    with pytest.raises(SchemaValidationError, match="missing keys"):
        validate_ops_alert(d)


def test_ops_trigger_bad_type():
    d = _valid_ops()
    d["triggers"][0]["type"] = "meteor_strike"
    with pytest.raises(SchemaValidationError, match="Invalid trigger 'type'"):
        validate_ops_alert(d)


def test_ops_trigger_non_string_location():
    d = _valid_ops()
    d["triggers"][0]["location"] = 42
    with pytest.raises(SchemaValidationError):
        validate_ops_alert(d)


@pytest.mark.parametrize("field", ["value", "threshold_breached"])
def test_ops_trigger_non_numeric(field):
    d = _valid_ops()
    d["triggers"][0][field] = "lots"
    with pytest.raises(SchemaValidationError):
        validate_ops_alert(d)


def test_ops_security_incident_type_allowed():
    d = _valid_ops()
    d["severity"] = "critical"
    d["triggers"][0] = {
        "type": "security_incident",
        "location": "Stadium",
        "value": 3,
        "threshold_breached": 1,
    }
    validate_ops_alert(d)  # must not raise


# ---------------------------------------------------------------------------
# Navigation — every rejection branch
# ---------------------------------------------------------------------------


def test_nav_root_not_dict():
    with pytest.raises(SchemaValidationError):
        validate_navigation("nope")


def test_nav_missing_key():
    d = _valid_nav()
    del d["recommended_gate"]
    with pytest.raises(SchemaValidationError, match="Missing top-level keys"):
        validate_navigation(d)


@pytest.mark.parametrize("field", ["summary", "reasoning", "recommended_gate", "language"])
def test_nav_non_string_fields(field):
    d = _valid_nav()
    d[field] = 1
    with pytest.raises(SchemaValidationError):
        validate_navigation(d)


def test_nav_empty_recommended_gate_allowed():
    """Empty recommended_gate is the valid 'no route' state and must pass."""
    d = _valid_nav()
    d["recommended_gate"] = ""
    validate_navigation(d)


def test_nav_avoid_gates_not_list():
    d = _valid_nav()
    d["avoid_gates"] = "Gate C"
    with pytest.raises(SchemaValidationError, match="avoid_gates"):
        validate_navigation(d)


def test_nav_avoid_gates_non_string_items():
    d = _valid_nav()
    d["avoid_gates"] = ["Gate C", 7]
    with pytest.raises(SchemaValidationError, match="avoid_gates"):
        validate_navigation(d)


@pytest.mark.parametrize("bad_walk", [True, False, "5", None])
def test_nav_walk_not_number(bad_walk):
    d = _valid_nav()
    d["estimated_walk_min"] = bad_walk
    with pytest.raises(SchemaValidationError, match="estimated_walk_min"):
        validate_navigation(d)


def test_nav_walk_accepts_int_and_float():
    d = _valid_nav()
    d["estimated_walk_min"] = 4.5
    validate_navigation(d)


def test_nav_step_free_not_bool():
    d = _valid_nav()
    d["step_free"] = "yes"
    with pytest.raises(SchemaValidationError, match="step_free"):
        validate_navigation(d)


def test_nav_bad_confidence():
    d = _valid_nav()
    d["confidence"] = "maybe"
    with pytest.raises(SchemaValidationError, match="confidence"):
        validate_navigation(d)
