"""Unit tests for the Stadium Brain module, mocking the Google Gen AI client."""

import json
from unittest.mock import MagicMock, patch

import pytest

from brain.schemas import SchemaValidationError
from brain.stadium_brain import (
    _call_gemini_structured,
    _get_client,
    ask_fan_assistant,
    check_operational_alerts,
    validate_fan_assistant,
)


@pytest.fixture(autouse=True)
def _clear_client_cache():
    """Reset the memoised Gen AI client around every test.

    ``_get_client`` is wrapped in ``functools.lru_cache``; without clearing it a
    client built in one test (with a key set) would leak into the next.
    """
    _get_client.cache_clear()
    yield
    _get_client.cache_clear()


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """Neutralise retry backoff so the retry-path tests run instantly."""
    monkeypatch.setattr("brain.stadium_brain.time.sleep", lambda *_: None)


def _fake_response(payload) -> MagicMock:
    """Build a mock mirroring the google-genai response contract.

    Args:
        payload: A dict (serialized to JSON) or a raw string to place on ``.text``.
    """
    resp = MagicMock()
    resp.text = payload if isinstance(payload, str) else json.dumps(payload)
    return resp


@pytest.fixture
def mock_snapshot() -> dict:
    """Fixture returning a standard mock stadium snapshot (no threshold breaches)."""
    return {
        "timestamp": "2026-07-08T19:30:59.357032+00:00",
        "stadium_id": "metlife",
        "stadium_name": "MetLife Stadium",
        "match_status": "pre-match",
        "gates": [
            {
                "gate_id": "Gate A",
                "crowd_density_pct": 20,
                "density_status": "Low",
                "entries_last_5min": 150,
            }
        ],
        "concessions": [
            {
                "stand_name": "Main Concourse Grill",
                "avg_wait_time_min": 5.0,
                "queue_length": 10,
                "status": "Open",
            }
        ],
        "security": {
            "alert_level": "Green",
            "last_updated": "2026-07-08T19:30:59.356415+00:00",
            "active_incidents": 0,
            "notes": "Routine monitoring",
        },
    }


@pytest.fixture
def breached_snapshot(mock_snapshot: dict) -> dict:
    """Fixture returning a snapshot with a concession queue-time threshold breach."""
    snapshot = json.loads(json.dumps(mock_snapshot))
    snapshot["concessions"][0]["avg_wait_time_min"] = 30.0
    return snapshot


def test_missing_api_key_raises_value_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing GEMINI_API_KEY surfaces as a ValueError from the low-level call."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(ValueError, match="Missing API token configuration."):
        _call_gemini_structured("sys", "content", validate_fan_assistant)


def test_missing_api_key_fan_assistant_falls_back(monkeypatch: pytest.MonkeyPatch, mock_snapshot: dict) -> None:
    """ask_fan_assistant is total: with no key it returns a schema-correct offline reply."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    result = ask_fan_assistant("Where can I eat?", mock_snapshot)
    # Data-driven fallback fired: still matches the full Fan Assistant schema.
    assert result["recommendation"]["type"] == "concession_stand"
    assert result["offline"] is True


@patch("brain.stadium_brain.genai.Client")
def test_ask_fan_assistant_success(
    mock_client_cls: MagicMock, monkeypatch: pytest.MonkeyPatch, mock_snapshot: dict
) -> None:
    """ask_fan_assistant parses and validates a well-formed Gemini response."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-api-key")
    client = MagicMock()
    mock_client_cls.return_value = client
    client.models.generate_content.return_value = _fake_response(
        {
            "answer": "You can enter through Gate A.",
            "reasoning": "Gate A has the lowest density of the available gates.",
            "recommendation": {"type": "gate", "name": "Gate A", "reason": "Low density."},
            "language": "English",
            "data_snapshot_timestamp": "2026-07-08T19:30:59.357032+00:00",
            "confidence": "high",
        }
    )

    result = ask_fan_assistant("Which gate is best?", mock_snapshot)

    assert result["answer"] == "You can enter through Gate A."
    assert result["reasoning"]  # explainability field populated
    assert result["language"] == "English"
    assert result["recommendation"]["name"] == "Gate A"
    assert result["confidence"] == "high"
    client.models.generate_content.assert_called_once()


@patch("brain.stadium_brain.genai.Client")
def test_malformed_json_retry_flow(
    mock_client_cls: MagicMock, monkeypatch: pytest.MonkeyPatch, mock_snapshot: dict
) -> None:
    """Malformed JSON on the first attempt retries and succeeds on the second."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-api-key")
    client = MagicMock()
    mock_client_cls.return_value = client
    good = _fake_response(
        {
            "answer": "Go to Main Concourse Grill.",
            "reasoning": "Main Concourse Grill has the shortest wait among open stands.",
            "recommendation": {
                "type": "concession_stand",
                "name": "Main Concourse Grill",
                "reason": "Wait time is only 5 minutes.",
            },
            "language": "English",
            "data_snapshot_timestamp": "2026-07-08T19:30:59.357032+00:00",
            "confidence": "high",
        }
    )
    client.models.generate_content.side_effect = [
        _fake_response("Here is your answer: this is not JSON"),
        good,
    ]

    result = ask_fan_assistant("Where can I eat?", mock_snapshot)
    assert result["answer"] == "Go to Main Concourse Grill."
    assert client.models.generate_content.call_count == 2


@patch("brain.stadium_brain.genai.Client")
def test_max_retries_raises_schema_error(
    mock_client_cls: MagicMock, monkeypatch: pytest.MonkeyPatch, mock_snapshot: dict
) -> None:
    """After 3 malformed attempts the low-level call raises SchemaValidationError."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-api-key")
    client = MagicMock()
    mock_client_cls.return_value = client
    client.models.generate_content.return_value = _fake_response("invalid-json")

    user_content = json.dumps({"user_query": "hi", "snapshot": mock_snapshot})
    with pytest.raises(SchemaValidationError):
        _call_gemini_structured("system", user_content, validate_fan_assistant)
    assert client.models.generate_content.call_count == 3


@patch("brain.stadium_brain.genai.Client")
def test_max_retries_fan_assistant_falls_back(
    mock_client_cls: MagicMock, monkeypatch: pytest.MonkeyPatch, mock_snapshot: dict
) -> None:
    """When all retries fail, ask_fan_assistant degrades to the offline fallback."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-api-key")
    client = MagicMock()
    mock_client_cls.return_value = client
    client.models.generate_content.return_value = _fake_response("invalid-json")

    result = ask_fan_assistant("Which gate?", mock_snapshot)
    assert result["offline"] is True
    assert result["recommendation"]["type"] == "gate"
    assert client.models.generate_content.call_count == 3


@patch("brain.stadium_brain.genai.Client")
def test_check_operational_alerts_no_threshold_skips_llm(
    mock_client_cls: MagicMock, monkeypatch: pytest.MonkeyPatch, mock_snapshot: dict
) -> None:
    """No breach → no LLM call (cost/latency optimisation)."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-api-key")
    client = MagicMock()
    mock_client_cls.return_value = client

    result = check_operational_alerts(mock_snapshot)

    assert result["alert_triggered"] is False
    assert result["severity"] == "none"
    assert result["triggers"] == []
    client.models.generate_content.assert_not_called()


@patch("brain.stadium_brain.genai.Client")
def test_check_operational_alerts_with_threshold_calls_llm(
    mock_client_cls: MagicMock, monkeypatch: pytest.MonkeyPatch, breached_snapshot: dict
) -> None:
    """A breach engages Gemini and returns the contextualised alert."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-api-key")
    client = MagicMock()
    mock_client_cls.return_value = client
    client.models.generate_content.return_value = _fake_response(
        {
            "alert_triggered": True,
            "severity": "warning",
            "triggers": [
                {
                    "type": "queue_time",
                    "location": "Main Concourse Grill",
                    "value": 30.0,
                    "threshold_breached": 25,
                }
            ],
            "reasoning": "A single queue-time breach with no critical trigger warrants a warning.",
            "recommended_action": "Redirect fans from Main Concourse Grill to Family Fan Zone Kiosk.",
            "generated_at": "2026-07-08T19:35:00+00:00",
        }
    )

    result = check_operational_alerts(breached_snapshot)

    assert result["alert_triggered"] is True
    assert result["severity"] == "warning"
    assert len(result["triggers"]) == 1
    assert result["triggers"][0]["location"] == "Main Concourse Grill"
    client.models.generate_content.assert_called_once()


@patch("brain.stadium_brain.genai.Client")
def test_security_red_triggers_alert_offline(
    mock_client_cls: MagicMock, monkeypatch: pytest.MonkeyPatch, mock_snapshot: dict
) -> None:
    """A Red security level escalates to a critical alert even with no other breach."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)  # force offline fallback
    mock_snapshot["security"]["alert_level"] = "Red"
    mock_snapshot["security"]["active_incidents"] = 4

    result = check_operational_alerts(mock_snapshot)

    assert result["alert_triggered"] is True
    assert result["severity"] == "critical"
    assert any(t["type"] == "security_incident" for t in result["triggers"])
    assert "[Offline Simulation Mode]" in result["recommended_action"]
