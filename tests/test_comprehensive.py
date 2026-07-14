from unittest.mock import patch

import pytest

from brain.schemas import SchemaValidationError, validate_fan_assistant, validate_ops_alert
from brain.stadium_brain import (
    _clean_json_string,
    ask_fan_assistant,
    check_operational_alerts,
    sanitize_user_input,
)
from simulator.config import (
    CONCESSION_STANDS,
    GATES,
    SECURITY_LEVEL_WEIGHTS,
    SECURITY_LEVELS,
    STADIUMS,
)
from simulator.data_simulator import StadiumDataSimulator
from ui.fan_view import generate_fallback_fan_response

# --- Fixtures ---


@pytest.fixture
def base_snapshot():
    return {
        "timestamp": "2026-07-08T19:30:59Z",
        "stadium_id": "metlife",
        "stadium_name": "MetLife Stadium",
        "match_status": "live",
        "gates": [
            {
                "gate_id": "Gate A",
                "crowd_density_pct": 50,
                "density_status": "Moderate",
                "entries_last_5min": 200,
            }
        ],
        "concessions": [
            {
                "stand_name": "Main Concourse Grill",
                "avg_wait_time_min": 10.0,
                "queue_length": 15,
                "status": "Open",
            },
            {
                "stand_name": "Craft Beer & Snacks",
                "avg_wait_time_min": 5.0,
                "queue_length": 5,
                "status": "Open",
            },
        ],
        "security": {
            "alert_level": "Green",
            "last_updated": "2026-07-08T19:30:59Z",
            "active_incidents": 0,
            "notes": "Routine monitoring",
        },
    }


# --- Category 1: Threshold Boundary Tests ---


def test_check_operational_alerts_wait_time_exact(base_snapshot):
    base_snapshot["concessions"][0]["avg_wait_time_min"] = 25.0
    res = check_operational_alerts(base_snapshot)
    assert res["alert_triggered"] is False


def test_check_operational_alerts_wait_time_exceeds(base_snapshot, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "dummy_key")
    # Using monkeypatch and mock to avoid real API calls since it exceeds threshold
    with patch("brain.stadium_brain._call_gemini_structured") as mock_call:
        mock_call.return_value = {
            "alert_triggered": True,
            "severity": "warning",
            "triggers": [
                {
                    "type": "queue_time",
                    "location": "Main Concourse Grill",
                    "value": 25.1,
                    "threshold_breached": 25,
                }
            ],
            "recommended_action": "Open more registers",
            "generated_at": "2026-07-08T19:30:59Z",
        }
        base_snapshot["concessions"][0]["avg_wait_time_min"] = 25.1
        res = check_operational_alerts(base_snapshot)
        assert res["alert_triggered"] is True


def test_check_operational_alerts_gate_density_high(base_snapshot):
    base_snapshot["gates"][0]["density_status"] = "High"
    base_snapshot["gates"][0]["crowd_density_pct"] = 85
    res = check_operational_alerts(base_snapshot)
    assert res["alert_triggered"] is False


def test_check_operational_alerts_gate_density_critical(base_snapshot, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "dummy_key")
    with patch("brain.stadium_brain._call_gemini_structured") as mock_call:
        mock_call.return_value = {
            "alert_triggered": True,
            "severity": "critical",  # schema enum: none | warning | critical
            "triggers": [
                {
                    "type": "crowd_density",
                    "location": "Gate A",
                    "value": 92,
                    "threshold_breached": 90,
                }
            ],
            "recommended_action": "Redirect",
            "generated_at": "2026-07-08T19:30:59Z",
        }
        base_snapshot["gates"][0]["density_status"] = "Critical"
        base_snapshot["gates"][0]["crowd_density_pct"] = 92
        res = check_operational_alerts(base_snapshot)
        assert res["alert_triggered"] is True


def test_check_operational_alerts_multiple_triggers(base_snapshot, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "dummy_key")
    with patch("brain.stadium_brain._call_gemini_structured") as mock_call:
        mock_call.return_value = {
            "alert_triggered": True,
            "severity": "critical",
            "triggers": [
                {
                    "type": "crowd_density",
                    "location": "Gate A",
                    "value": 95,
                    "threshold_breached": 90,
                },
                {
                    "type": "queue_time",
                    "location": "Main Concourse Grill",
                    "value": 30.0,
                    "threshold_breached": 25,
                },
            ],
            "recommended_action": "Do something",
            "generated_at": "2026-07-08T19:30:59Z",
        }
        base_snapshot["gates"][0]["density_status"] = "Critical"
        base_snapshot["gates"][0]["crowd_density_pct"] = 95
        base_snapshot["concessions"][0]["avg_wait_time_min"] = 30.0
        res = check_operational_alerts(base_snapshot)
        assert res["alert_triggered"] is True


# --- Category 2: Input Sanitization Tests ---


def test_sanitize_user_input_normal():
    assert sanitize_user_input("hello world") == "hello world"


def test_sanitize_user_input_strip():
    assert sanitize_user_input("   test   ") == "test"


def test_sanitize_user_input_truncate():
    long_str = "a" * 600
    res = sanitize_user_input(long_str)
    assert len(res) == 500
    assert res == "a" * 500


def test_sanitize_user_input_null_bytes():
    assert sanitize_user_input("hello\x00world") == "helloworld"


def test_sanitize_user_input_empty():
    assert sanitize_user_input("") == ""


# --- Category 3: Fan Fallback Response Tests ---


def test_fan_fallback_food(base_snapshot):
    res = generate_fallback_fan_response("where is the food?", base_snapshot)
    assert res["recommendation"]["type"] == "concession_stand"
    assert "Craft Beer & Snacks" in res["recommendation"]["name"]  # Since wait time 5 is lower than 10


def test_fan_fallback_gate(base_snapshot):
    res = generate_fallback_fan_response("which gate to enter?", base_snapshot)
    assert res["recommendation"]["type"] == "gate"
    assert "Gate A" in res["recommendation"]["name"]


def test_fan_fallback_none(base_snapshot):
    res = generate_fallback_fan_response("what is the meaning of life?", base_snapshot)
    assert res["recommendation"]["type"] == "none"


def test_fan_fallback_empty_concessions(base_snapshot):
    base_snapshot["concessions"] = []
    res = generate_fallback_fan_response("food", base_snapshot)
    # The logic might return default fallback if no concessions match
    # Since food matched, it checks if concessions exist. If not, what does it do?
    # Based on the code, if no concessions, it skips to default.
    assert res["recommendation"]["type"] == "none"


def test_fan_fallback_empty_gates(base_snapshot):
    base_snapshot["gates"] = []
    res = generate_fallback_fan_response("gate", base_snapshot)
    assert res["recommendation"]["type"] == "none"


def test_fan_fallback_populates_reasoning_and_language(base_snapshot):
    # Every fallback branch must carry the new schema fields.
    res = generate_fallback_fan_response("where is the food?", base_snapshot)
    validate_fan_assistant(res)  # full schema incl. reasoning + language
    assert res["reasoning"]
    assert res["language"] == "English"


def test_fan_fallback_notes_non_english_request(base_snapshot):
    # Offline mode can't translate; it says so when a non-English language is asked.
    res = generate_fallback_fan_response("food", base_snapshot, "Spanish")
    assert "Spanish" in res["answer"]
    assert res["language"] == "English"  # actual answer language is still English


def test_fan_fallback_skips_temporarily_closed_stand(base_snapshot):
    # A closed stand reports a 0.0 wait; it must not be recommended as "fastest".
    base_snapshot["concessions"] = [
        {
            "stand_name": "ClosedOne",
            "avg_wait_time_min": 0.0,
            "queue_length": 0,
            "status": "Temporarily Closed",
        },
        {"stand_name": "OpenGrill", "avg_wait_time_min": 8.0, "queue_length": 12, "status": "Open"},
    ]
    res = generate_fallback_fan_response("where is the food?", base_snapshot)
    assert res["recommendation"]["name"] == "OpenGrill"


def test_fan_fallback_shortest_line_to_enter_routes_to_gate(base_snapshot):
    # "shortest ... to enter" is a gate query; the generic word must not hit food.
    res = generate_fallback_fan_response("which gate has the shortest line to enter?", base_snapshot)
    assert res["recommendation"]["type"] == "gate"


def test_ask_fan_assistant_forwards_language_to_fallback(base_snapshot, monkeypatch):
    # Offline: the language arg must reach build_fan_fallback (note in the answer).
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    res = ask_fan_assistant("where is the food?", base_snapshot, "French")
    assert "French" in res["answer"]


# --- Category 4: Schema Validation Edge Cases ---


def test_validate_fan_assistant_missing_answer():
    with pytest.raises(SchemaValidationError):
        validate_fan_assistant(
            {
                "recommendation": {"type": "none", "name": "", "reason": ""},
                "data_snapshot_timestamp": "ts",
                "confidence": "high",
            }
        )


def test_validate_fan_assistant_invalid_confidence():
    with pytest.raises(SchemaValidationError):
        validate_fan_assistant(
            {
                "answer": "Test",
                "recommendation": {"type": "none", "name": "", "reason": ""},
                "data_snapshot_timestamp": "ts",
                "confidence": "invalid",
            }
        )


def test_validate_ops_alert_missing_triggers():
    with pytest.raises(SchemaValidationError):
        validate_ops_alert(
            {
                "alert_triggered": False,
                "severity": "none",
                "recommended_action": "Nothing",
                "generated_at": "ts",
            }
        )


def test_validate_ops_alert_invalid_trigger_type():
    with pytest.raises(SchemaValidationError):
        validate_ops_alert(
            {
                "alert_triggered": True,
                "severity": "warning",
                "triggers": [
                    {
                        "type": "invalid_type",
                        "location": "Gate A",
                        "value": 90,
                        "threshold_breached": 90,
                    }
                ],
                "recommended_action": "Test",
                "generated_at": "ts",
            }
        )


def test_validate_ops_alert_valid():
    validate_ops_alert(
        {
            "alert_triggered": False,
            "severity": "none",
            "triggers": [],
            "reasoning": "No thresholds breached.",
            "recommended_action": "Nothing",
            "generated_at": "ts",
        }
    )


# --- Category 5: JSON Cleaning Tests ---


def test_clean_json_string_json_fence():
    txt = '```json\n{"a": 1}\n```'
    assert _clean_json_string(txt) == '{"a": 1}'


def test_clean_json_string_plain_fence():
    txt = '```\n{"a": 1}\n```'
    assert _clean_json_string(txt) == '{"a": 1}'


def test_clean_json_string_clean():
    txt = '{"a": 1}'
    assert _clean_json_string(txt) == '{"a": 1}'


def test_clean_json_string_whitespace():
    txt = '   \n  {"a": 1}  \n   '
    assert _clean_json_string(txt) == '{"a": 1}'


# --- Category 6: Stadium Config Tests ---


def test_config_stadiums():
    assert len(STADIUMS) == 3
    ids = [s["stadium_id"] for s in STADIUMS]
    assert set(ids) == {"metlife", "azteca", "bcplace"}
    for s in STADIUMS:
        assert "stadium_id" in s
        assert "name" in s
        assert "city" in s
        assert "capacity" in s


def test_config_gates_concessions_security():
    assert len(GATES) == 4
    assert len(CONCESSION_STANDS) == 4
    assert len(SECURITY_LEVELS) == 4
    assert sum(SECURITY_LEVEL_WEIGHTS) == 1.0


def test_simulator_invalid_stadium():
    with pytest.raises(ValueError):
        StadiumDataSimulator("invalid_stadium")


# --- Category 7: Simulator Multi-Stadium Tests ---


def test_multi_stadium_initialization():
    sim_metlife = StadiumDataSimulator("metlife")
    sim_azteca = StadiumDataSimulator("azteca")
    sim_bcplace = StadiumDataSimulator("bcplace")

    snap1 = sim_metlife.generate_snapshot()
    snap2 = sim_azteca.generate_snapshot()
    snap3 = sim_bcplace.generate_snapshot()

    assert snap1["stadium_id"] == "metlife"
    assert snap2["stadium_id"] == "azteca"
    assert snap3["stadium_id"] == "bcplace"


def test_match_status_cycle():
    sim = StadiumDataSimulator("metlife")
    sim._tick = 0
    assert sim._determine_match_status() == "pre-match"
    sim._tick = 35
    assert sim._determine_match_status() == "live"
    sim._tick = 65
    assert sim._determine_match_status() == "halftime"
    sim._tick = 80
    assert sim._determine_match_status() == "live"
    sim._tick = 95
    assert sim._determine_match_status() == "post-match"


# --- Category 8: Operational Alerts Offline Fallback ---


def test_offline_fallback(base_snapshot, monkeypatch):
    # Ensure no API key
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    # Trigger an alert
    base_snapshot["concessions"][0]["avg_wait_time_min"] = 30.0

    res = check_operational_alerts(base_snapshot)

    assert res["alert_triggered"] is True
    assert res["severity"] == "warning"
    assert "[Offline Simulation Mode]" in res["recommended_action"]
