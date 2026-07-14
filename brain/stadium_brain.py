"""Stadium Brain.

This module implements the backend intelligence layer for the FIFA World Cup
2026 Smart Stadium App. It uses the Google Gen AI SDK to power operational
alerts and a fan assistant based on live stadium data.
"""

import datetime
import functools
import json
import logging
import os
import time
from collections.abc import Callable
from typing import Any

from google import genai
from google.genai import types

from brain.fallbacks import (
    build_fan_fallback,
    build_gate_candidates,
    build_navigation_fallback,
)
from brain.prompts import (
    FAN_ASSISTANT_SYSTEM_PROMPT,
    NAVIGATION_SYSTEM_PROMPT,
    OPS_ALERT_SYSTEM_PROMPT,
)
from brain.schemas import (
    FAN_RESPONSE_SCHEMA,
    NAVIGATION_SCHEMA,
    OPS_ALERT_SCHEMA,
    SchemaValidationError,
    validate_fan_assistant,
    validate_navigation,
    validate_ops_alert,
)

# ---------------------------------------------------------------------------
# Configure logger
# ---------------------------------------------------------------------------
logger = logging.getLogger("stadium_brain")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_MODEL: str = "gemini-2.5-flash"
"""Default Gemini model string."""

QUEUE_TIME_THRESHOLD_MIN: int = 25
"""Minutes above which a concession wait time is considered a breach."""

CROWD_DENSITY_CRITICAL_STATUS: str = "Critical"
"""Density status string that triggers a crowd-density alert."""

MAX_FAN_QUERY_LENGTH: int = 500
"""Maximum character length accepted for fan assistant queries."""

SECURITY_ALERT_LEVELS: frozenset = frozenset({"Orange", "Red"})
"""Security alert levels that escalate to an operational alert."""

MAX_OUTPUT_TOKENS: int = 1024
"""Upper bound on model output tokens — caps latency and cost per call."""


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------


def sanitize_user_input(raw_input: str) -> str:
    """Sanitise a raw user query before it is forwarded to the LLM."""
    sanitized = raw_input.strip()
    sanitized = sanitized.replace("\x00", "")
    return sanitized[:MAX_FAN_QUERY_LENGTH]


def _get_api_key() -> str | None:
    """Resolve the Gemini API key from the environment, then Streamlit secrets.

    Resolution order:
      1. ``GEMINI_API_KEY`` environment variable (local dev, CI, containers).
      2. ``st.secrets["GEMINI_API_KEY"]`` (Streamlit Community Cloud deploys,
         where secrets are injected via the dashboard, not env vars).

    The ``st.secrets`` lookup is lazy-imported and fully guarded: accessing it
    with no secrets file configured raises inside Streamlit, and this function
    is also called from tests/CLI where Streamlit has no script context. Any
    such failure is swallowed so key resolution degrades cleanly to "no key".

    Returns:
        The resolved API key, or ``None`` if neither source provides one.
    """
    api_key: str = os.environ.get("GEMINI_API_KEY", "").strip()
    if api_key:
        return api_key

    # Fallback: Streamlit Cloud secrets. Guarded so a missing secrets file or a
    # missing script context can never crash the caller.
    try:
        import streamlit as st

        secret_key = str(st.secrets.get("GEMINI_API_KEY", "")).strip()
        if secret_key:
            return secret_key
    except Exception:
        # Optional secrets probe: any failure just means "no key here" — fall through.
        pass

    logger.warning("GEMINI_API_KEY not found in environment or Streamlit secrets.")
    return None


def _clean_json_string(text: str) -> str:
    """Remove potential markdown code fences and leading/trailing whitespace."""
    text = text.strip()
    if text.startswith("```json"):
        text = text[7:]
    elif text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    return text.strip()


# ---------------------------------------------------------------------------
# Core API interaction
# ---------------------------------------------------------------------------


@functools.lru_cache(maxsize=1)
def _get_client() -> "genai.Client":
    """Return a process-wide singleton Gen AI client.

    Constructing ``genai.Client`` performs auth/transport setup, so we build it
    once and memoise it rather than paying that cost on every call. The cache is
    keyed on nothing (maxsize=1); call ``_get_client.cache_clear()`` in tests to
    force a rebuild after mutating the environment.

    Raises:
        ValueError: If no API key is configured in the environment.
    """
    api_key = _get_api_key()
    if not api_key:
        raise ValueError("Missing API token configuration.")
    return genai.Client(api_key=api_key)


def _call_gemini_structured(
    system_prompt: str,
    user_content: str,
    validator: Callable[[Any], None],
    response_schema: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Send a structured request to the Gemini API and validate the response.

    Args:
        system_prompt: System instruction steering the model.
        user_content: Serialized user payload (compact JSON).
        validator: Callable that raises ``SchemaValidationError`` on a bad shape.
        response_schema: Optional JSON Schema for constrained decoding. When
            provided, the model is structurally prevented from emitting unknown
            keys or invalid enum values, so ``validator`` becomes a safety net.

    Returns:
        The parsed, schema-valid response dictionary.

    Raises:
        ValueError: If the API key is missing (surfaced from ``_get_client``).
        SchemaValidationError: If all attempts fail to yield valid output.
    """
    client = _get_client()

    max_attempts: int = 3
    backoff: float = 0.5
    for attempt in range(max_attempts):
        try:
            start_time: float = time.time()

            response = client.models.generate_content(
                model=DEFAULT_MODEL,
                contents=user_content,
                config=types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    response_mime_type="application/json",
                    response_schema=response_schema,  # constrained decoding
                    temperature=0.2,
                    max_output_tokens=MAX_OUTPUT_TOKENS,
                ),
            )

            latency: float = time.time() - start_time
            logger.info(f"Gemini API call completed in {latency:.2f}s (Attempt {attempt + 1}/{max_attempts})")

            # A blocked/truncated candidate yields ``None`` here; treat as retryable
            # rather than letting ``_clean_json_string(None)`` raise AttributeError.
            raw_text: str | None = response.text
            if not raw_text:
                raise SchemaValidationError("Model returned an empty response body.")

            cleaned_text: str = _clean_json_string(raw_text)

            try:
                parsed_json: dict[str, Any] = json.loads(cleaned_text)
            except json.JSONDecodeError as err:
                logger.warning(f"JSON decode failed on attempt {attempt + 1}: {err}. Cleaned text: '{cleaned_text}'")
                if attempt == max_attempts - 1:
                    raise SchemaValidationError(f"Failed to parse Gemini response as valid JSON: {err}") from err
                time.sleep(backoff)
                backoff *= 2
                continue

            try:
                validator(parsed_json)
                logger.info("Response successfully validated against schema.")
                # Namespaced metadata for the UI (latency badge). Validators check
                # only for *missing* required keys, so an extra key is safe.
                parsed_json["_meta"] = {"latency_s": round(latency, 2), "source": "gemini"}
                return parsed_json
            except SchemaValidationError as err:
                logger.warning(f"Schema validation failed on attempt {attempt + 1}: {err}")
                if attempt == max_attempts - 1:
                    raise
                time.sleep(backoff)
                backoff *= 2
                continue

        except Exception as err:
            if isinstance(err, (SchemaValidationError, ValueError)):
                raise
            logger.error(f"Unexpected error during Gemini call on attempt {attempt + 1}: {err}")
            if attempt == max_attempts - 1:
                raise SchemaValidationError(f"Unexpected API or library error: {err}") from err
            time.sleep(backoff)
            backoff *= 2

    raise SchemaValidationError("Exhausted all retry attempts without a result.")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def ask_fan_assistant(
    user_query: str,
    snapshot: dict[str, Any],
    language: str = "English",
) -> dict[str, Any]:
    """Answer a fan's natural-language question using live stadium data.

    This function is *total*: on any LLM failure it degrades to a data-driven,
    schema-correct fallback (:func:`brain.fallbacks.build_fan_fallback`) instead
    of raising. Callers can therefore always render the returned dict directly.

    Args:
        user_query: The fan's natural-language question.
        snapshot: Live stadium snapshot (simulated or uploaded).
        language: Target natural-language for the answer (e.g. "Spanish"). The
            model answers in this language with locally-appropriate register;
            the offline fallback notes the request but answers in English.
    """
    sanitized_query: str = sanitize_user_input(user_query)
    # Compact separators: the LLM does not benefit from pretty-printing, and
    # every indent character is a wasted input token.
    user_content: str = json.dumps(
        {
            "user_query": sanitized_query,
            "target_language": language,
            "snapshot": snapshot,
        },
        separators=(",", ":"),
    )

    try:
        return _call_gemini_structured(
            system_prompt=FAN_ASSISTANT_SYSTEM_PROMPT,
            user_content=user_content,
            validator=validate_fan_assistant,
            response_schema=FAN_RESPONSE_SCHEMA,
        )
    except Exception as err:
        logger.warning(f"Gemini Fan Assistant invocation failed. Using data-driven fallback. Error: {err}")
        # Grounded in the live snapshot AND matches the full Fan Assistant schema,
        # so the UI renders an identical, intelligent card even while offline.
        return build_fan_fallback(sanitized_query, snapshot, language)


def check_operational_alerts(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Run deterministic checks and, if breaches exist, ask Gemini to contextualise them."""
    triggers: list[dict[str, Any]] = []

    for gate in snapshot.get("gates", []):
        if gate.get("density_status") == CROWD_DENSITY_CRITICAL_STATUS:
            triggers.append(
                {
                    "type": "crowd_density",
                    "location": gate.get("gate_id", "Unknown Gate"),
                    "value": gate.get("crowd_density_pct", 0),
                    "threshold_breached": 90,
                }
            )

    for concession in snapshot.get("concessions", []):
        wait_time: float = concession.get("avg_wait_time_min", 0.0)
        if wait_time > QUEUE_TIME_THRESHOLD_MIN:
            triggers.append(
                {
                    "type": "queue_time",
                    "location": concession.get("stand_name", "Unknown Concession"),
                    "value": wait_time,
                    "threshold_breached": QUEUE_TIME_THRESHOLD_MIN,
                }
            )

    # Security escalation: an Orange/Red alert level is the single most severe
    # real-world signal, yet was previously invisible to the alert engine.
    security: dict[str, Any] = snapshot.get("security", {})
    security_level: str = security.get("alert_level", "Green")
    if security_level in SECURITY_ALERT_LEVELS:
        triggers.append(
            {
                "type": "security_incident",
                "location": snapshot.get("stadium_name", "Stadium"),
                "value": security.get("active_incidents", 0),
                "threshold_breached": 1,
            }
        )

    if not triggers:
        return {
            "alert_triggered": False,
            "severity": "none",
            "triggers": [],
            "reasoning": (
                "Deterministic pre-check found no breach: all gate densities are "
                "below Critical, every concession wait is within the 25-minute "
                "threshold, and security is Green/Yellow."
            ),
            "recommended_action": "All stadium systems, concessions, and gates are operating normally.",
            "generated_at": datetime.datetime.now(datetime.UTC).isoformat(),
        }

    # Compact separators: pretty-printing only inflates the input token count.
    user_content: str = json.dumps(
        {
            "snapshot": snapshot,
            "deterministic_pre_check": {
                "alert_triggered": True,
                "triggers": triggers,
            },
        },
        separators=(",", ":"),
    )

    try:
        return _call_gemini_structured(
            system_prompt=OPS_ALERT_SYSTEM_PROMPT,
            user_content=user_content,
            validator=validate_ops_alert,
            response_schema=OPS_ALERT_SCHEMA,
        )
    except Exception as err:
        logger.warning(f"Gemini operations alert analysis failed. Activating secure fallback tracking: {err}")
        fallback_actions: list[str] = []
        for trig in triggers:
            loc: str = trig.get("location", "Unknown Location")
            val: Any = trig.get("value", 0)
            trig_type: str = trig.get("type", "")
            if trig_type == "queue_time":
                fallback_actions.append(
                    f"Deploy staff reinforcement to Concession stand '{loc}' due to high wait time ({val} mins)."
                )
            elif trig_type == "security_incident":
                fallback_actions.append(
                    f"Escalate to security command for '{loc}' — {security_level} alert, {val} active incident(s)."
                )
            else:
                fallback_actions.append(
                    f"Open auxiliary bypass lanes at Gate '{loc}' due to Critical crowd density ({val}%)."
                )

        # A security incident (or a Critical density breach) is critical severity;
        # a lone queue-time breach is a warning.
        has_critical: bool = any(t.get("type") in ("security_incident", "crowd_density") for t in triggers)
        severity: str = "critical" if has_critical else "warning"

        # Data-grounded explanation of why this severity follows from the triggers.
        trig_types = sorted({str(t.get("type")) for t in triggers})
        critical_types = [t for t in ("security_incident", "crowd_density") if t in trig_types]
        if has_critical:
            reasoning = (
                f"Severity is critical because {len(triggers)} threshold breach(es) "
                f"include {', '.join(critical_types)}, which pose an immediate "
                f"crowd-safety risk; the actions below target each breached location."
            )
        else:
            reasoning = (
                f"Severity is a warning: the {len(triggers)} breach(es) are "
                f"queue-time only (no Critical density or security incident), so "
                f"staffing relief resolves it without escalation."
            )

        fallback_action_str: str = " ".join(fallback_actions)
        return {
            "alert_triggered": True,
            "severity": severity,
            "triggers": triggers,
            "reasoning": reasoning,
            "recommended_action": f"[Offline Simulation Mode] {fallback_action_str}",
            "generated_at": datetime.datetime.now(datetime.UTC).isoformat(),
        }


def _resolve_destination_point(
    destination: str,
    snapshot: dict[str, Any],
) -> "tuple[float, float]":
    """Map a destination label to an (x, y) point in the schematic map space.

    A destination may be a gate id, a concession stand name, or a free-text zone
    ("my seat", "the pitch"). Gates resolve to their ring position; a stand
    resolves to the position of its nearest-mapped gate (stands sit just inside
    the concourse); anything else resolves to the stadium centre (the pitch),
    which is a sensible neutral target for "my seat". Matching is case-insensitive
    and tolerant of partial names.
    """
    from simulator.config import STADIUM_CENTER, gate_layout

    gates = snapshot.get("gates", []) or []
    gate_ids = [str(g.get("gate_id", f"Gate {i + 1}")) for i, g in enumerate(gates)]
    layout = gate_layout(gate_ids)
    dest_l = destination.strip().lower()

    # Direct (or partial) gate match.
    for gid in gate_ids:
        if dest_l == gid.lower() or (dest_l and dest_l in gid.lower()):
            pos = layout[gid]
            return (pos["x"], pos["y"])

    # Concession stand → nudge from its nearest gate toward the centre (stands
    # are inside the ring). We approximate by mapping the stand to the gate that
    # shares its index, then pulling 35% toward centre.
    concessions = snapshot.get("concessions", []) or []
    for idx, c in enumerate(concessions):
        name = str(c.get("stand_name", "")).lower()
        if name and (dest_l == name or dest_l in name):
            anchor_gid = gate_ids[idx] if idx < len(gate_ids) else (gate_ids[0] if gate_ids else None)
            if anchor_gid is not None:
                pos = layout[anchor_gid]
                cx, cy = STADIUM_CENTER
                return (
                    round(pos["x"] + 0.35 * (cx - pos["x"]), 2),
                    round(pos["y"] + 0.35 * (cy - pos["y"]), 2),
                )

    # Fallback: the pitch/centre — a neutral target for "my seat" / unknown zones.
    return STADIUM_CENTER


def get_navigation_guidance(
    destination: str,
    snapshot: dict[str, Any],
    language: str = "English",
    step_free_required: bool = False,
) -> dict[str, Any]:
    """Recommend the best entry gate for a destination, reasoning over live crowds.

    This is the navigation vertical: unlike a shortest-path map, it weighs a
    gate's live crowd density against its walk time so it can steer a fan away
    from a jammed-but-close entry toward a clear-but-slightly-farther one — a
    judgement a plain rule cannot make well. Like the other brain entry points it
    is *total*: on any LLM failure it degrades to a data-driven, schema-correct
    fallback (:func:`brain.fallbacks.build_navigation_fallback`).

    Args:
        destination: Where the fan wants to go (a gate, a stand, or "my seat").
        snapshot: Live stadium snapshot (simulated or uploaded).
        language: Target language for the fan-facing text.
        step_free_required: When True, prefer a wheelchair-accessible gate and
            say so in the reasoning (the accessibility toggle).

    Returns:
        A dict matching the Navigation schema (with a ``_meta`` latency badge on
        the live path, or ``offline=True`` on the fallback path).
    """
    dest_point = _resolve_destination_point(destination, snapshot)
    candidates = build_gate_candidates(snapshot, dest_point)

    # No gates → nothing to route between; return the schema-valid fallback
    # directly without spending an API call.
    if not candidates:
        return build_navigation_fallback(destination, snapshot, candidates, language, step_free_required)

    # Compact payload: the model reasons over the pre-computed candidates (with
    # walk times + step-free flags), never raw coordinates.
    user_content: str = json.dumps(
        {
            "destination": sanitize_user_input(destination),
            "target_language": language,
            "step_free_required": step_free_required,
            "candidate_gates": candidates,
        },
        separators=(",", ":"),
    )

    try:
        return _call_gemini_structured(
            system_prompt=NAVIGATION_SYSTEM_PROMPT,
            user_content=user_content,
            validator=validate_navigation,
            response_schema=NAVIGATION_SCHEMA,
        )
    except Exception as err:
        logger.warning(f"Gemini navigation invocation failed. Using data-driven fallback. Error: {err}")
        return build_navigation_fallback(destination, snapshot, candidates, language, step_free_required)
