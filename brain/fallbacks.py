"""Deterministic, data-grounded fallbacks for the Stadium Brain.

These helpers produce schema-correct responses directly from the live snapshot
when the Gemini API is unavailable (missing key, network error, quota, etc.).

Design goal: an offline demo must still show *intelligence derived from live
data* — not a canned paragraph. Every function here returns the exact same
schema the LLM path returns, so the UI renders identically whether the answer
came from Gemini or from this module.
"""

from typing import Any

# Keywords that route a free-text fan query to the concession/gate branches.
# Note: the generic adjective "short(est)" is deliberately NOT a food keyword —
# it collides with gate queries like "which gate has the shortest line?" (the
# food branch is checked first). Food intent is signalled by concrete nouns.
_FOOD_KEYWORDS = (
    "food",
    "eat",
    "drink",
    "concession",
    "beer",
    "snack",
    "grill",
    "court",
    "kiosk",
    "taco",
    "hungry",
    "thirsty",
    "meal",
)
_GATE_KEYWORDS = ("gate", "enter", "entry", "crowd", "density", "exit", "turnstile")


def _language_note(language: str) -> str:
    """A short prefix noting that offline mode can't translate.

    The LLM path answers in the requested language; the deterministic fallback
    cannot, so it answers in English and says so (only when a non-English
    language was requested).
    """
    if language and language.strip().lower() != "english":
        return f"(Offline mode answers in English; '{language}' needs the live AI.) "
    return ""


def build_fan_fallback(
    user_query: str,
    snapshot: dict[str, Any],
    language: str = "English",
) -> dict[str, Any]:
    """Answer a fan query from snapshot data when the LLM is unavailable.

    The returned dict conforms to the full Fan Assistant schema
    (``answer`` / ``reasoning`` / ``recommendation`` / ``language`` /
    ``data_snapshot_timestamp`` / ``confidence``) and additionally carries
    ``offline=True`` so the UI can surface an "Offline Simulation Mode" banner
    without corrupting the ``confidence`` field.

    The ``reasoning`` field is populated with a genuine data-grounded comparison
    (best vs. worst option and the gap between them), so even the offline path
    demonstrates *why* — not just *what*.

    Args:
        user_query: Natural-language query from the fan.
        snapshot: Current live stadium snapshot from the simulator or an upload.
        language: Requested answer language. The fallback always answers in
            English but records the request and notes the limitation.

    Returns:
        A structured response dictionary matching the Fan Assistant schema,
        with an extra ``offline`` marker key.
    """
    query: str = user_query.lower()
    gates: list[dict[str, Any]] = snapshot.get("gates", [])
    concessions: list[dict[str, Any]] = snapshot.get("concessions", [])
    timestamp: str = snapshot.get("timestamp", "")
    lang_note: str = _language_note(language)

    # 1. Food and concessions query — recommend the shortest live wait.
    if any(w in query for w in _FOOD_KEYWORDS) and concessions:
        # Only recommend stands a fan can actually use: a "Temporarily Closed"
        # stand reports a 0.0 wait, which would otherwise sort to the front and
        # be recommended as the "fastest". Fall back to all stands only if every
        # one is closed (so the summary still has something to report).
        open_stands = [c for c in concessions if str(c.get("status", "")).lower() != "temporarily closed"]
        pool = open_stands if open_stands else concessions
        sorted_concessions = sorted(pool, key=lambda c: c.get("avg_wait_time_min", 999.0))
        best, worst = sorted_concessions[0], sorted_concessions[-1]
        best_wait = best.get("avg_wait_time_min", 0.0)
        worst_wait = worst.get("avg_wait_time_min", 0.0)
        saved = round(worst_wait - best_wait, 1)
        return {
            "answer": (
                f"{lang_note}I'm operating in Offline Simulation Mode. Concessions wait times: "
                f"'{best.get('stand_name')}' is the fastest with a {best_wait} mins wait. "
                f"The longest wait is at '{worst.get('stand_name')}' at {worst_wait} mins."
            ),
            "reasoning": (
                f"Compared all {len(concessions)} open concession wait times from the "
                f"live snapshot and selected the minimum: '{best.get('stand_name')}' at "
                f"{best_wait} min saves about {saved} min versus the busiest stand "
                f"('{worst.get('stand_name')}', {worst_wait} min)."
            ),
            "recommendation": {
                "type": "concession_stand",
                "name": best.get("stand_name", ""),
                "reason": f"Shortest wait time is currently {best_wait} mins.",
            },
            "language": "English",
            "data_snapshot_timestamp": timestamp,
            "confidence": "high",
            "offline": True,
        }

    # 2. Gates and entry query — recommend the lowest live density.
    if any(w in query for w in _GATE_KEYWORDS) and gates:
        sorted_gates = sorted(gates, key=lambda g: g.get("crowd_density_pct", 100))
        best_gate, worst_gate = sorted_gates[0], sorted_gates[-1]
        best_pct = best_gate.get("crowd_density_pct", 0)
        worst_pct = worst_gate.get("crowd_density_pct", 0)
        best_id, best_status = best_gate.get("gate_id"), best_gate.get("density_status")
        worst_id, worst_status = worst_gate.get("gate_id"), worst_gate.get("density_status")
        return {
            "answer": (
                f"{lang_note}I'm operating in Offline Simulation Mode. Gate densities: "
                f"'{best_id}' has the lowest density at {best_pct}% ({best_status}). "
                f"'{worst_id}' is currently the most crowded at {worst_pct}% ({worst_status})."
            ),
            "reasoning": (
                f"Ranked all {len(gates)} gates by live crowd density and chose the "
                f"least congested: '{best_gate.get('gate_id')}' at {best_pct}% "
                f"({best_gate.get('density_status')}) versus the busiest at {worst_pct}% "
                f"— a {abs(worst_pct - best_pct)} point difference in crowding."
            ),
            "recommendation": {
                "type": "gate",
                "name": best_gate.get("gate_id", ""),
                "reason": f"Lowest crowd density at {best_pct}%.",
            },
            "language": "English",
            "data_snapshot_timestamp": timestamp,
            "confidence": "high",
            "offline": True,
        }

    # 3. Default fallback summary — full status dump for out-of-domain queries.
    concession_summary = ", ".join(f"{c.get('stand_name')}: {c.get('avg_wait_time_min')}m" for c in concessions)
    gate_summary = ", ".join(f"{g.get('gate_id')}: {g.get('crowd_density_pct')}%" for g in gates)
    return {
        "answer": (
            f"{lang_note}I'm operating in Offline Simulation Mode. Current status: "
            f"Concessions: [{concession_summary}]. "
            f"Gates: [{gate_summary}]. "
            f"Please ask about concessions or gates for specific recommendations."
        ),
        "reasoning": (
            "Query did not map to a gate or concession decision, so no single "
            "recommendation was selected; returned the full live snapshot summary instead."
        ),
        "recommendation": {"type": "none", "name": "", "reason": ""},
        "language": "English",
        "data_snapshot_timestamp": timestamp,
        "confidence": "medium",
        "offline": True,
    }


# ---------------------------------------------------------------------------
# Navigation (wayfinding) helpers
# ---------------------------------------------------------------------------
# Shared by both the live-AI path (to build the candidate payload the model
# reasons over) and the offline fallback (which reasons over the same candidates
# with a transparent scoring rule). Keeping one candidate builder means the map,
# the AI, and the fallback all agree on walk times and step-free flags.

# Density-band penalty (minutes-equivalent) added when scoring a gate, so the
# trade-off is "effective time" = walk time + congestion cost. A Critical gate
# is heavily penalised; a Low gate is free. This is what lets the offline path
# pick a slightly-farther-but-clear gate over a close-but-jammed one.
_DENSITY_PENALTY_MIN: dict[str, float] = {
    "Low": 0.0,
    "Moderate": 2.0,
    "High": 6.0,
    "Critical": 15.0,
}


def build_gate_candidates(
    snapshot: dict[str, Any],
    destination_point: "tuple[float, float]",
) -> list[dict[str, Any]]:
    """Build the per-gate candidate list the navigator reasons over.

    Each candidate carries the gate's live density, its walk time to the chosen
    destination (from the shared schematic layout), and whether it is step-free.
    Imported lazily so :mod:`brain.fallbacks` keeps no hard dependency on the
    simulator package's layout details beyond call time.

    Args:
        snapshot: The live stadium snapshot.
        destination_point: ``(x, y)`` of the destination in the normalised space.

    Returns:
        A list of candidate dicts, one per gate in the snapshot.
    """
    from simulator.config import GatePosition, gate_layout, walk_time_between

    gates: list[dict[str, Any]] = snapshot.get("gates", []) or []
    gate_ids = [str(g.get("gate_id", f"Gate {i + 1}")) for i, g in enumerate(gates)]
    layout = gate_layout(gate_ids)

    candidates: list[dict[str, Any]] = []
    for gid, gate in zip(gate_ids, gates, strict=False):
        pos: GatePosition = layout.get(gid, {"x": 50.0, "y": 50.0, "angle": 0.0, "step_free": False})
        walk = walk_time_between((pos["x"], pos["y"]), destination_point)
        candidates.append(
            {
                "gate_id": gid,
                "crowd_density_pct": gate.get("crowd_density_pct", 0),
                "density_status": gate.get("density_status", "Low"),
                "walk_time_min": walk,
                "step_free": bool(pos["step_free"]),
            }
        )
    return candidates


def _score_candidate(cand: dict[str, Any]) -> float:
    """Effective-time score for a candidate: walk time + congestion penalty.

    Lower is better. This encodes the navigator's core judgement — a jammed
    entry costs more than a short extra walk — in one transparent number.
    """
    walk = float(cand.get("walk_time_min", 0.0))
    penalty = _DENSITY_PENALTY_MIN.get(str(cand.get("density_status", "Low")), 3.0)
    return walk + penalty


def build_navigation_fallback(
    destination: str,
    _snapshot: dict[str, Any],
    candidates: list[dict[str, Any]],
    language: str = "English",
    step_free_required: bool = False,
) -> dict[str, Any]:
    """Reasoned wayfinding answer from candidate data when the LLM is unavailable.

    Chooses the entry gate with the best *effective time* (walk + congestion
    penalty), honouring a step-free requirement by restricting the pool to
    accessible gates when possible. The returned dict matches the full Navigation
    schema (plus an ``offline`` marker), and its ``reasoning`` is a genuine
    comparison — the least-congested viable gate versus the busiest — so even the
    offline path shows *why* it rerouted, not just where.

    Args:
        destination: The fan's chosen destination label (for the summary text).
        snapshot: The live snapshot (used only for a timestamp/name if needed).
        candidates: Output of :func:`build_gate_candidates`.
        language: Requested answer language (fallback answers in English + notes it).
        step_free_required: If True, prefer a step-free gate.

    Returns:
        A schema-valid Navigation dict with an extra ``offline=True`` marker.
    """
    lang_note = _language_note(language)

    # No gates at all — degrade gracefully with a schema-valid "can't route" reply.
    if not candidates:
        return {
            "summary": (
                f"{lang_note}I'm in Offline Simulation Mode and no gate data is "
                f"available, so I can't recommend an entry for '{destination}'."
            ),
            "reasoning": "No gate candidates were present in the snapshot to compare.",
            "recommended_gate": "",
            "avoid_gates": [],
            "estimated_walk_min": 0.0,
            "step_free": False,
            "language": "English",
            "confidence": "low",
            "offline": True,
        }

    # Honour the accessibility constraint: restrict to step-free gates if any
    # exist; otherwise fall back to all and flag that none were fully accessible.
    step_free_pool = [c for c in candidates if c.get("step_free")]
    no_accessible = step_free_required and not step_free_pool
    pool = step_free_pool if (step_free_required and step_free_pool) else candidates

    ranked = sorted(pool, key=_score_candidate)
    best = ranked[0]
    # "Avoid" list: the most congested gates across ALL candidates (what we're
    # steering away from), excluding the recommended gate itself.
    by_density = sorted(
        candidates,
        key=lambda c: float(c.get("crowd_density_pct", 0)),
        reverse=True,
    )
    avoid = [c["gate_id"] for c in by_density if c["gate_id"] != best["gate_id"]][:2]

    best_pct = best.get("crowd_density_pct", 0)
    best_status = best.get("density_status", "Low")
    best_walk = float(best.get("walk_time_min", 0.0))
    worst = by_density[0]

    access_note = ""
    if step_free_required:
        if no_accessible:
            access_note = (
                " No fully step-free gate was found in the data, so this is the "
                "best available — verify accessibility with a steward."
            )
        else:
            access_note = " This gate has step-free (wheelchair-accessible) entry."

    summary = (
        f"{lang_note}I'm in Offline Simulation Mode. For '{destination}', enter via "
        f"{best['gate_id']} — {best_pct}% ({best_status}), about {best_walk:g} min away."
        f"{access_note}"
    )

    reasoning = (
        f"Compared {len(candidates)} gate(s) by effective time (walk time plus a "
        f"congestion penalty). {best['gate_id']} wins at {best_pct}% ({best_status}) "
        f"and ~{best_walk:g} min; the busiest, {worst['gate_id']} at "
        f"{worst.get('crowd_density_pct', 0)}% ({worst.get('density_status')}), was "
        f"rejected because clearing a congested entry costs more than a short extra walk."
    )
    if step_free_required:
        reasoning += (
            " Step-free access was required, so the choice was limited to accessible gates."
            if not no_accessible
            else " Step-free access was required but no accessible gate was in the data."
        )

    # Confidence: high when the winner is genuinely clear (Low/Moderate), else medium.
    confidence = "high" if best_status in ("Low", "Moderate") else "medium"

    return {
        "summary": summary,
        "reasoning": reasoning,
        "recommended_gate": best["gate_id"],
        "avoid_gates": avoid,
        "estimated_walk_min": best_walk,
        "step_free": bool(best.get("step_free", False)),
        "language": "English",
        "confidence": confidence,
        "offline": True,
    }
