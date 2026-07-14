"""Validation schemas and functions for stadium brain responses."""

from typing import Any


class SchemaValidationError(Exception):
    """Exception raised when the parsed JSON response does not conform to the expected schema."""


# ---------------------------------------------------------------------------
# Gemini controlled-generation schemas
# ---------------------------------------------------------------------------
# Passed as ``response_schema`` to GenerateContentConfig so the model performs
# *constrained decoding*: it is structurally unable to emit unknown keys or
# invalid enum values. The hand-written validators below then act as a
# belt-and-suspenders safety net rather than the primary line of defence.

FAN_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": [
        "answer",
        "reasoning",
        "recommendation",
        "language",
        "data_snapshot_timestamp",
        "confidence",
    ],
    "properties": {
        "answer": {"type": "string"},
        # Explainability (XAI): the causal "why" behind the answer — which live
        # data points drove it and any trade-off considered. This is what makes
        # the assistant reason over the data rather than just look it up.
        "reasoning": {"type": "string"},
        "recommendation": {
            "type": "object",
            "required": ["type", "name", "reason"],
            "properties": {
                "type": {"type": "string", "enum": ["gate", "concession_stand", "none"]},
                "name": {"type": "string"},
                "reason": {"type": "string"},
            },
        },
        # The natural-language the ``answer`` is written in (e.g. "Spanish").
        "language": {"type": "string"},
        "data_snapshot_timestamp": {"type": "string"},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
    },
}

OPS_ALERT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": [
        "alert_triggered",
        "severity",
        "triggers",
        "reasoning",
        "recommended_action",
        "generated_at",
    ],
    "properties": {
        "alert_triggered": {"type": "boolean"},
        "severity": {"type": "string", "enum": ["none", "warning", "critical"]},
        "triggers": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["type", "location", "value", "threshold_breached"],
                "properties": {
                    "type": {
                        "type": "string",
                        "enum": ["queue_time", "crowd_density", "security_incident"],
                    },
                    "location": {"type": "string"},
                    "value": {"type": "number"},
                    "threshold_breached": {"type": "number"},
                },
            },
        },
        # Explainability (XAI): why this severity and this action follow from the
        # triggers — the causal chain an ops operator needs to trust the call.
        "reasoning": {"type": "string"},
        "recommended_action": {"type": "string"},
        "generated_at": {"type": "string"},
    },
}


NAVIGATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": [
        "summary",
        "reasoning",
        "recommended_gate",
        "avoid_gates",
        "estimated_walk_min",
        "step_free",
        "language",
        "confidence",
    ],
    "properties": {
        "summary": {"type": "string"},
        # Explainability (XAI): the density-vs-walk-time trade-off that produced
        # the reroute — the reasoning a fan (and a judge) can check.
        "reasoning": {"type": "string"},
        "recommended_gate": {"type": "string"},
        "avoid_gates": {"type": "array", "items": {"type": "string"}},
        "estimated_walk_min": {"type": "number"},
        "step_free": {"type": "boolean"},
        "language": {"type": "string"},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
    },
}


def validate_navigation(data: Any) -> None:
    """Validates data against the Navigation (wayfinding) schema.

    Args:
        data: The parsed JSON response to validate.

    Raises:
        SchemaValidationError: If validation fails.
    """
    if not isinstance(data, dict):
        raise SchemaValidationError("Root element must be a dictionary.")

    required_keys = {
        "summary",
        "reasoning",
        "recommended_gate",
        "avoid_gates",
        "estimated_walk_min",
        "step_free",
        "language",
        "confidence",
    }
    missing = required_keys - data.keys()
    if missing:
        raise SchemaValidationError(f"Missing top-level keys: {missing}")

    if not isinstance(data["summary"], str):
        raise SchemaValidationError("'summary' must be a string.")
    if not isinstance(data["reasoning"], str):
        raise SchemaValidationError("'reasoning' must be a string.")
    # A string is required; an *empty* string is permitted, because it is the
    # valid "no gate could be recommended" state (e.g. a snapshot with no gates).
    # Enforcing non-empty here would reject the graceful no-route fallback.
    if not isinstance(data["recommended_gate"], str):
        raise SchemaValidationError("'recommended_gate' must be a string.")
    if not isinstance(data["language"], str):
        raise SchemaValidationError("'language' must be a string.")

    avoid = data["avoid_gates"]
    if not isinstance(avoid, list) or not all(isinstance(g, str) for g in avoid):
        raise SchemaValidationError("'avoid_gates' must be a list of strings.")

    # Booleans are ints in Python; guard the walk-time against that so a stray
    # True/False can't masquerade as a number.
    walk = data["estimated_walk_min"]
    if isinstance(walk, bool) or not isinstance(walk, (int, float)):
        raise SchemaValidationError("'estimated_walk_min' must be a number.")

    if not isinstance(data["step_free"], bool):
        raise SchemaValidationError("'step_free' must be a boolean.")

    confidence = data["confidence"]
    if confidence not in {"high", "medium", "low"}:
        raise SchemaValidationError(f"Invalid 'confidence': '{confidence}'. Expected 'high', 'medium', or 'low'.")


def validate_fan_assistant(data: Any) -> None:
    """Validates data against the Fan Assistant schema.

    Args:
        data: The parsed JSON response to validate.

    Raises:
        SchemaValidationError: If validation fails.
    """
    if not isinstance(data, dict):
        raise SchemaValidationError("Root element must be a dictionary.")

    # Required top-level keys
    required_keys = {
        "answer",
        "reasoning",
        "recommendation",
        "language",
        "data_snapshot_timestamp",
        "confidence",
    }
    missing = required_keys - data.keys()
    if missing:
        raise SchemaValidationError(f"Missing top-level keys: {missing}")

    # Validate types of top-level fields
    if not isinstance(data["answer"], str):
        raise SchemaValidationError("'answer' must be a string.")
    if not isinstance(data["reasoning"], str):
        raise SchemaValidationError("'reasoning' must be a string.")
    if not isinstance(data["language"], str):
        raise SchemaValidationError("'language' must be a string.")
    if not isinstance(data["data_snapshot_timestamp"], str):
        raise SchemaValidationError("'data_snapshot_timestamp' must be a string.")

    confidence = data["confidence"]
    if confidence not in {"high", "medium", "low"}:
        raise SchemaValidationError(f"Invalid 'confidence': '{confidence}'. Expected 'high', 'medium', or 'low'.")

    # Validate 'recommendation'
    recommendation = data["recommendation"]
    if not isinstance(recommendation, dict):
        raise SchemaValidationError("'recommendation' must be a dictionary.")

    rec_keys = {"type", "name", "reason"}
    missing_rec = rec_keys - recommendation.keys()
    if missing_rec:
        raise SchemaValidationError(f"Missing keys in recommendation: {missing_rec}")

    rec_type = recommendation["type"]
    if rec_type not in {"gate", "concession_stand", "none"}:
        raise SchemaValidationError(
            f"Invalid recommendation 'type': '{rec_type}'. Expected 'gate', 'concession_stand', or 'none'."
        )

    if not isinstance(recommendation["name"], str):
        raise SchemaValidationError("Recommendation 'name' must be a string.")
    if not isinstance(recommendation["reason"], str):
        raise SchemaValidationError("Recommendation 'reason' must be a string.")


def validate_ops_alert(data: Any) -> None:
    """Validates data against the Operations Alert schema.

    Args:
        data: The parsed JSON response to validate.

    Raises:
        SchemaValidationError: If validation fails.
    """
    if not isinstance(data, dict):
        raise SchemaValidationError("Root element must be a dictionary.")

    # Required top-level keys
    required_keys = {
        "alert_triggered",
        "severity",
        "triggers",
        "reasoning",
        "recommended_action",
        "generated_at",
    }
    missing = required_keys - data.keys()
    if missing:
        raise SchemaValidationError(f"Missing top-level keys: {missing}")

    # Validate types of top-level fields
    if not isinstance(data["alert_triggered"], bool):
        raise SchemaValidationError("'alert_triggered' must be a boolean.")

    severity = data["severity"]
    if severity not in {"none", "warning", "critical"}:
        raise SchemaValidationError(f"Invalid 'severity': '{severity}'. Expected 'none', 'warning', or 'critical'.")

    if not isinstance(data["reasoning"], str):
        raise SchemaValidationError("'reasoning' must be a string.")
    if not isinstance(data["recommended_action"], str):
        raise SchemaValidationError("'recommended_action' must be a string.")
    if not isinstance(data["generated_at"], str):
        raise SchemaValidationError("'generated_at' must be a string.")

    # Validate 'triggers' list
    triggers = data["triggers"]
    if not isinstance(triggers, list):
        raise SchemaValidationError("'triggers' must be a list.")

    trigger_keys = {"type", "location", "value", "threshold_breached"}
    for idx, trigger in enumerate(triggers):
        if not isinstance(trigger, dict):
            raise SchemaValidationError(f"Trigger at index {idx} must be a dictionary.")

        missing_trig = trigger_keys - trigger.keys()
        if missing_trig:
            raise SchemaValidationError(f"Trigger at index {idx} missing keys: {missing_trig}")

        trig_type = trigger["type"]
        if trig_type not in {"queue_time", "crowd_density", "security_incident"}:
            raise SchemaValidationError(
                f"Invalid trigger 'type' at index {idx}: '{trig_type}'. "
                f"Expected 'queue_time', 'crowd_density', or 'security_incident'."
            )

        if not isinstance(trigger["location"], str):
            raise SchemaValidationError(f"Trigger 'location' at index {idx} must be a string.")

        # Value and threshold check (should be float or int)
        if not isinstance(trigger["value"], (int, float)):
            raise SchemaValidationError(f"Trigger 'value' at index {idx} must be a number (int or float).")
        if not isinstance(trigger["threshold_breached"], (int, float)):
            raise SchemaValidationError(f"Trigger 'threshold_breached' at index {idx} must be a number (int or float).")
