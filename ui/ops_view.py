"""Stadium Operations Command Center View.

A broadcast-grade control room for stadium operators: an at-a-glance executive
summary, live gate/concession meters that carry their own severity colour and
decision thresholds, a security panel, and one-click AI-assisted alert analysis
that explains its reasoning.

Rendering is built from the shared :mod:`ui.theme` design system so the ops and
fan views read as one product. The data pipeline and the AI alert-check flow are
untouched — this module only decides how the numbers are shown.
"""

from typing import Any

import streamlit as st

from brain.stadium_brain import check_operational_alerts
from ui import theme as T
from ui.state_manager import MAX_ALERT_HISTORY

# Decision thresholds surfaced directly on the meters so an operator reads the
# breach line, not just the value. These mirror the deterministic pre-check.
GATE_TARGET_PCT: float = 70.0
QUEUE_BREACH_MIN: float = 25.0
# Longest bar on the wait-time panel maps to this many minutes (full track).
WAIT_SCALE_MIN: float = 40.0

_MATCH_PHASE_LABELS: dict[str, str] = {
    "pre-match": "Pre-Match Surge",
    "live": "Match Live",
    "halftime": "Half-Time Rush",
    "post-match": "Full-Time Dispersal",
}


def _summary_tiles(
    gates: list[dict[str, Any]],
    concessions: list[dict[str, Any]],
    security: dict[str, Any],
) -> str:
    """Build the four executive-summary KPI tiles ('is anything wrong?')."""
    # Peak gate density + count of gates above the High threshold.
    if gates:
        peak_gate = max(gates, key=lambda g: g.get("crowd_density_pct", 0))
        peak_pct = peak_gate.get("crowd_density_pct", 0)
        peak_status = peak_gate.get("density_status", "Low")
        peak_tile = T.kpi_tile(
            caption="Peak Gate Density",
            value=str(peak_pct),
            unit="%",
            foot=f"at <b>{T._esc(peak_gate.get('gate_id', '—'))}</b> · {T._esc(peak_status)}",
            color=T.density_color(peak_status),
        )
        alerting = sum(1 for g in gates if g.get("density_status") in ("High", "Critical"))
        alert_color = T.SEV_CRIT if alerting else T.SEV_OK
        gates_tile = T.kpi_tile(
            caption="Gates In Alert",
            value=f"{alerting}",
            unit=f"/ {len(gates)}",
            foot="above High threshold" if alerting else "all within target",
            color=alert_color,
        )
    else:
        peak_tile = T.kpi_tile(caption="Peak Gate Density", value="—", color=T.MUTED)
        gates_tile = T.kpi_tile(caption="Gates In Alert", value="—", color=T.MUTED)

    # Longest concession wait among stands that are actually open.
    open_stands = [c for c in concessions if str(c.get("status", "")).lower() != "temporarily closed"]
    if open_stands:
        worst = max(open_stands, key=lambda c: c.get("avg_wait_time_min", 0.0))
        worst_wait = worst.get("avg_wait_time_min", 0.0)
        wait_tile = T.kpi_tile(
            caption="Longest Food Wait",
            value=f"{worst_wait:g}",
            unit="min",
            foot=f"at <b>{T._esc(worst.get('stand_name', '—'))}</b>",
            color=T.wait_color(float(worst_wait)),
        )
    else:
        wait_tile = T.kpi_tile(caption="Longest Food Wait", value="—", foot="no stands open", color=T.MUTED)

    # Security posture.
    level = security.get("alert_level", "Green")
    incidents = security.get("active_incidents", 0)
    sec_tile = T.kpi_tile(
        caption="Security Posture",
        value=str(level).upper(),
        foot=f"<b>{T._esc(incidents)}</b> active incident(s)",
        color=T.security_color(level),
    )

    return T.grid([peak_tile, wait_tile, sec_tile, gates_tile], cols=4)


def _gate_meters(gates: list[dict[str, Any]]) -> str:
    """Build the gate crowd-density meter panel (with the 70% target tick)."""
    if not gates:
        return T.panel("<div style='color:#8A9BB4'>No gate telemetry available.</div>")
    rows: list[str] = []
    for gate in gates:
        pct = float(gate.get("crowd_density_pct", 0))
        status = gate.get("density_status", "Low")
        entries = gate.get("entries_last_5min")
        sub = f"{entries:,} entries · last 5 min" if isinstance(entries, int) else ""
        color = T.density_color(status)
        rows.append(
            T.meter_row(
                name=str(gate.get("gate_id", "Gate")),
                sub=sub,
                value_text=f"{pct:g}%  {status}",
                pct=pct,
                color=color,
                threshold_pct=GATE_TARGET_PCT,
                threshold_label=f"target {GATE_TARGET_PCT:g}%",
            )
        )
    return T.panel("".join(rows))


def _concession_meters(concessions: list[dict[str, Any]]) -> str:
    """Build the concession wait-time meter panel (with the 25-min breach tick)."""
    if not concessions:
        return T.panel("<div style='color:#8A9BB4'>No concession telemetry available.</div>")
    rows: list[str] = []
    for c in concessions:
        wait = float(c.get("avg_wait_time_min", 0.0))
        status = str(c.get("status", "Open"))
        queue = c.get("queue_length")
        closed = status.lower() == "temporarily closed"
        if closed:
            sub = "temporarily closed"
            value_text = "CLOSED"
            color = T.MUTED
            pct = 0.0
        else:
            sub = f"{queue} in queue" if isinstance(queue, int) else ""
            value_text = f"{wait:g} min"
            color = T.wait_color(wait)
            pct = (wait / WAIT_SCALE_MIN) * 100.0
        rows.append(
            T.meter_row(
                name=str(c.get("stand_name", "Stand")),
                sub=sub,
                value_text=value_text,
                pct=pct,
                color=color,
                threshold_pct=(QUEUE_BREACH_MIN / WAIT_SCALE_MIN) * 100.0,
                threshold_label=f"breach {QUEUE_BREACH_MIN:g}m",
            )
        )
    return T.panel("".join(rows))


def _security_panel(security: dict[str, Any]) -> str:
    """Build the security intelligence panel."""
    level = security.get("alert_level", "Green")
    incidents = security.get("active_incidents", 0)
    notes = security.get("notes", "Routine monitoring")
    last_updated = security.get("last_updated", "—")
    color = T.security_color(level)
    level_word = {
        "Green": "Normal",
        "Yellow": "Elevated",
        "Orange": "High Alert",
        "Red": "Critical",
    }.get(level, "")

    inner = (
        '<div class="fl-meter-top" style="margin-bottom:0.7rem;">'
        f"<div>{T.badge(f'{level} · {level_word}', color)}</div>"
        f'<div class="fl-meter-val" style="color:{color}">{T._esc(incidents)} incidents</div>'
        "</div>"
        f'<div style="font-size:0.95rem;color:#E8EEF6;margin-bottom:0.55rem;">'
        f"{T._esc(notes)}</div>"
        f'<div class="fl-chips">{T.chip(f"Last sync · {T._esc(last_updated)}")}</div>'
    )
    return T.panel(inner)


def _render_alert_result(latest_check: dict[str, Any]) -> None:
    """Render a completed AI alert check as a styled banner."""
    if latest_check.get("alert_triggered"):
        sev = str(latest_check.get("severity", "warning")).lower()
        color = T.SEV_CRIT if sev == "critical" else T.SEV_HIGH

        action_plan: str = latest_check.get("recommended_action", "")
        offline = "[Offline Simulation Mode]" in action_plan
        if offline:
            action_plan = action_plan.replace("[Offline Simulation Mode]", "").strip()

        # Trigger bullets with precise units.
        trig_html = ""
        for trig in latest_check.get("triggers", []):
            trig_type = str(trig.get("type")).replace("_", " ").title()
            val = trig.get("value")
            thresh = trig.get("threshold_breached")
            unit = " min" if trig.get("type") == "queue_time" else "%"
            trig_html += (
                f'<div style="margin:0.2rem 0;font-size:0.9rem;">'
                f'<b style="color:{color}">{T._esc(trig.get("location"))}</b> · '
                f"{T._esc(trig_type)} measured <b>{T._esc(val)}{unit}</b> "
                f'<span style="color:#8A9BB4">(threshold {T._esc(thresh)}{unit})</span></div>'
            )

        reasoning = str(latest_check.get("reasoning", "")).strip()
        reasoning_html = (
            f'<div style="margin-top:0.6rem;font-size:0.88rem;color:#C7D3E4;">'
            f"🧠 <b>Reasoning:</b> {T._esc(reasoning)}</div>"
            if reasoning
            else ""
        )
        action_html = (
            f'<div style="margin-top:0.6rem;padding-top:0.6rem;'
            f'border-top:1px solid rgba(255,255,255,0.08);font-size:0.92rem;">'
            f'<b style="color:{color}">Recommended Ops Action</b><br>{T._esc(action_plan)}</div>'
        )
        body = trig_html + reasoning_html + action_html
        st.markdown(
            T.alert_banner(title=f"⚠ Threshold Breach — {sev.upper()}", color=color, body_html=body),
            unsafe_allow_html=True,
        )
        if offline:
            st.caption("⚠️ Offline Simulation Mode (no valid GEMINI_API_KEY found).")

        # Latency chip — present only when a live Gemini call produced this
        # analysis, mirroring the Fan Assistant. Its presence proves the analysis
        # was AI-generated, not a deterministic fallback.
        meta: dict[str, Any] = latest_check.get("_meta", {})
        latency = meta.get("latency_s")
        if latency is not None:
            st.caption(f"⚡ AI analysis in {latency}s")
    else:
        st.markdown(
            T.alert_banner(
                title="✓ All Systems Nominal",
                color=T.SEV_OK,
                body_html='<div style="font-size:0.9rem;color:#C7D3E4;">'
                "No operational thresholds breached on the current snapshot.</div>",
            ),
            unsafe_allow_html=True,
        )


def render_ops_view(snapshot: dict[str, Any] | None, stadium_id: str) -> None:
    """Renders the Operations Command Center dashboard.

    Args:
        snapshot: Current live stadium snapshot from simulator, or None.
        stadium_id: Active stadium ID (e.g. 'metlife').
    """
    stadium_name = (snapshot or {}).get("stadium_name") or stadium_id.upper()
    match_status = (snapshot or {}).get("match_status")
    phase_label = _MATCH_PHASE_LABELS.get(str(match_status), "Standing By")

    st.markdown(
        T.topbar(
            eyebrow="Operations Command Center",
            title=stadium_name,
            subtitle=f"Real-time crowd flow, concession & security intelligence · {phase_label}",
            crest="🎛️",
            live=snapshot is not None,
            live_label="LIVE" if snapshot is not None else "STANDBY",
        ),
        unsafe_allow_html=True,
    )

    if snapshot is None:
        st.markdown(
            T.empty_state(
                emoji="📡",
                title="No Live Data Stream",
                text="Tap “Refresh Live Data” in the sidebar to begin monitoring this venue.",
            ),
            unsafe_allow_html=True,
        )
        return

    st.markdown(T.match_phase_strip(match_status), unsafe_allow_html=True)

    gates: list[dict[str, Any]] = snapshot.get("gates", [])
    concessions: list[dict[str, Any]] = snapshot.get("concessions", [])
    security: dict[str, Any] = snapshot.get("security", {})

    # --- Executive summary ---
    st.markdown(T.section("Situation Summary", "one-glance status"), unsafe_allow_html=True)
    st.markdown(_summary_tiles(gates, concessions, security), unsafe_allow_html=True)

    st.write("")

    # --- Gate + concession meters, side by side ---
    col_gate, col_conc = st.columns(2)
    with col_gate:
        st.markdown(T.section("Gate Crowd Density", "target below 70%"), unsafe_allow_html=True)
        st.markdown(_gate_meters(gates), unsafe_allow_html=True)
    with col_conc:
        st.markdown(T.section("Concession Wait Times", "breach at 25 min"), unsafe_allow_html=True)
        st.markdown(_concession_meters(concessions), unsafe_allow_html=True)

    st.write("")

    # --- Security + AI operational check ---
    col_sec, col_check = st.columns(2)
    with col_sec:
        st.markdown(T.section("Security Intelligence"), unsafe_allow_html=True)
        st.markdown(_security_panel(security), unsafe_allow_html=True)

    with col_check:
        st.markdown(T.section("AI Operational Check"), unsafe_allow_html=True)
        st.caption(
            "Deterministic thresholds run first (queue > 25 min, density Critical, "
            "or security Orange/Red). Gemini is engaged **only** on a real breach."
        )
        if st.button(
            "🤖 Evaluate Stadium Conditions",
            help="Runs deterministic threshold checks + AI analysis on the current telemetry snapshot",
        ):
            with st.spinner("Analyzing operational thresholds..."):
                try:
                    alert_res: dict[str, Any] = check_operational_alerts(snapshot)

                    # Store in this stadium's history slice (newest-first) and trim the tail.
                    history: list[dict[str, Any]] = st.session_state.alert_history.setdefault(stadium_id, [])
                    history.insert(0, alert_res)
                    del history[MAX_ALERT_HISTORY:]

                    st.session_state.latest_alert_check[stadium_id] = alert_res
                except Exception as err:
                    st.error(
                        f"🚨 Critical Backend Error: Unable to perform operational checks. "
                        f"Please check logs. Details: {err}"
                    )

        # Render this stadium's latest check result if it exists in state.
        latest_check: dict[str, Any] | None = st.session_state.latest_alert_check.get(stadium_id)
        if latest_check is not None:
            _render_alert_result(latest_check)

    st.write("")

    # --- Historical log ---
    st.markdown(T.section("Alert Log History", "this session"), unsafe_allow_html=True)
    stadium_history: list[dict[str, Any]] = st.session_state.alert_history.get(stadium_id, [])
    if not stadium_history:
        st.caption("No operational evaluations performed for this stadium in this session.")
    else:
        for _idx, alert in enumerate(stadium_history):
            timestamp: str = alert.get("generated_at", alert.get("timestamp", "Unknown"))
            triggered: bool = alert.get("alert_triggered", False)
            severity: str = str(alert.get("severity", "none")).upper()

            icon: str = "🚨" if triggered else "✅"
            status_text: str = "Triggered" if triggered else "Normal"
            label: str = f"{icon} [{timestamp}] — {status_text} · Severity: {severity}"

            with st.expander(label, expanded=False):
                st.json(alert)
