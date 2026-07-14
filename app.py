"""Smart Stadium App.

Entry point for the FIFA World Cup 2026 Smart Stadium Streamlit application.
Sets up page configuration, global controls, and handles routing.
"""

import datetime
from typing import Any

import streamlit as st

st.set_page_config(
    page_title="Smart Stadium — WC2026",
    layout="wide",
    page_icon="⚽",
)

from simulator.ingestion import SnapshotIngestionError, build_snapshot_from_upload
from ui.fan_view import render_fan_view
from ui.map_view import render_map_view
from ui.ops_view import render_ops_view
from ui.state_manager import (
    UPLOADED_STADIUM_ID,
    apply_uploaded_snapshot,
    init_session_state,
    refresh_snapshot,
)
from ui.theme import inject_theme

# Live auto-refresh is OFF by default: it is a genuine enhancement but reverses
# the original "manual refresh" decision, so it is opt-in and fully guarded.
AUTO_REFRESH_DEFAULT: bool = False
AUTO_REFRESH_INTERVAL_S: int = 5

init_session_state()

# One "Floodlit" design-system payload, injected once per page load. It styles
# both Streamlit's native widgets and the custom components the views compose.
st.markdown(inject_theme(), unsafe_allow_html=True)


def _relative_sync_label(iso_timestamp: str | None) -> str:
    """Render an ISO snapshot timestamp as a human 'synced Ns ago' string.

    Makes the sidebar read as *live* rather than showing a raw machine timestamp.
    Fully defensive: uploaded datasets may carry an odd or absent timestamp, so
    any parse failure falls back to the raw value (or a clear placeholder).
    """
    if not iso_timestamp:
        return "No telemetry loaded yet."
    try:
        ts = datetime.datetime.fromisoformat(iso_timestamp)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=datetime.UTC)
        delta = datetime.datetime.now(datetime.UTC) - ts
        secs = int(delta.total_seconds())
        if secs < 0:
            return f"synced just now (UTC {iso_timestamp})"
        if secs < 60:
            return f"synced {secs}s ago"
        if secs < 3600:
            return f"synced {secs // 60}m ago"
        return f"synced {secs // 3600}h ago"
    except (ValueError, TypeError):
        return str(iso_timestamp)


def _run_auto_refresh(stadium_id: str) -> None:
    """Tick a fresh snapshot on an interval via an isolated fragment.

    Defensive by design: this reverses the original manual-refresh decision, so
    it must never break the app. ``st.fragment(run_every=...)`` reruns only this
    fragment (not the whole script), which sidesteps the session conflicts the
    manual-only approach was avoiding. If the running Streamlit is too old to
    support ``fragment``/``run_every``, we degrade silently to manual refresh.
    """
    frag = getattr(st, "fragment", None)
    if frag is None:  # Streamlit < 1.33 — no fragment API; stay manual.
        st.sidebar.caption("Auto-refresh needs Streamlit ≥ 1.33; using manual refresh.")
        return

    try:

        @frag(run_every=AUTO_REFRESH_INTERVAL_S)
        def _tick() -> None:
            refresh_snapshot(stadium_id)
            # A new snapshot invalidates the prior AI check for this venue.
            st.session_state.latest_alert_check[stadium_id] = None

        _tick()
    except Exception as err:  # never let live-mode take the app down
        st.sidebar.caption(f"Auto-refresh unavailable ({err}); using manual refresh.")


def _render_upload_control() -> None:
    """Render the evaluator data-upload widget in the sidebar.

    Lets a judge drive the *entire* app with their own real data instead of the
    built-in simulator: a valid CSV/JSON is parsed and normalised into the
    internal snapshot schema (:mod:`simulator.ingestion`), stored under the
    uploaded-data slot, and the venue selector is switched to it. Parse warnings
    are surfaced without discarding an otherwise usable snapshot; a hard failure
    shows a clear error and changes nothing.

    Uploads are keyed by name+size so the same file is ingested once, not on
    every Streamlit rerun.
    """
    st.sidebar.divider()
    st.sidebar.subheader("📤 Evaluate With Your Data")
    st.sidebar.caption(
        "Upload a CSV or JSON of real stadium telemetry to run the assistant and "
        "ops dashboard against it. See `data/samples/` for the expected shape."
    )

    uploaded = st.sidebar.file_uploader(
        "Upload stadium data (CSV / JSON)",
        type=["csv", "json"],
        help="Gate rows (id + crowd density) and/or concession rows (name + wait time). "
        "A 'type' column disambiguates a single mixed table.",
        key="data_upload_widget",
    )
    if uploaded is None:
        return

    # Ingest each distinct file only once, not on every rerun.
    file_key: str = f"{uploaded.name}:{uploaded.size}"
    if st.session_state.get("_last_ingested_key") == file_key:
        return

    try:
        raw_bytes: bytes = uploaded.getvalue()
        snapshot, warnings = build_snapshot_from_upload(raw_bytes, uploaded.name)
    except SnapshotIngestionError as err:
        st.sidebar.error(f"❌ Could not read '{uploaded.name}': {err}")
        return
    except Exception as err:  # defensive: never crash the app on a bad upload
        st.sidebar.error(f"❌ Unexpected error reading '{uploaded.name}': {err}")
        return

    apply_uploaded_snapshot(snapshot)
    st.session_state._last_ingested_key = file_key
    st.session_state.selected_stadium = UPLOADED_STADIUM_ID
    # Chat is not stadium-scoped; clear it so answers reflect the new dataset.
    st.session_state.chat_history = []

    n_gates = len(snapshot.get("gates", []))
    n_conc = len(snapshot.get("concessions", []))
    st.sidebar.success(f"✅ Loaded '{uploaded.name}': {n_gates} gate(s), {n_conc} concession(s).")
    for note in warnings:
        st.sidebar.warning(f"⚠️ {note}")
    st.rerun()


def main() -> None:
    # Branded sidebar header — the wordmark uses the display face from the theme.
    st.sidebar.markdown(
        '<div style="display:flex;align-items:center;gap:0.6rem;margin:0.2rem 0 0.4rem;">'
        '<div style="width:40px;height:40px;border-radius:11px;display:grid;place-items:center;'
        "font-size:1.35rem;background:linear-gradient(135deg,#38BDF8,#22D3EE);"
        'box-shadow:0 6px 16px rgba(34,211,238,0.3);">⚽</div>'
        "<div>"
        "<div style=\"font-family:'Space Mono',monospace;font-size:0.58rem;letter-spacing:0.24em;"
        'text-transform:uppercase;color:#22D3EE;">FIFA World Cup 2026</div>'
        "<div style=\"font-family:'Anton',sans-serif;font-size:1.35rem;line-height:1;"
        'text-transform:uppercase;color:#E8EEF6;">Smart Stadium</div>'
        "</div></div>",
        unsafe_allow_html=True,
    )
    st.sidebar.divider()

    stadium_mapping: dict[str, str] = {
        "MetLife Stadium (East Rutherford, NJ)": "metlife",
        "Estadio Azteca (Mexico City)": "azteca",
        "BC Place (Vancouver)": "bcplace",
        "📤 Uploaded Dataset (evaluator data)": UPLOADED_STADIUM_ID,
    }
    reverse_mapping: dict[str, str] = {v: k for k, v in stadium_mapping.items()}

    current_stadium_id: str = st.session_state.selected_stadium
    current_stadium_label: str = reverse_mapping.get(current_stadium_id, list(stadium_mapping.keys())[0])
    stadium_options: list[str] = list(stadium_mapping.keys())
    current_index: int = stadium_options.index(current_stadium_label)

    selected_label: str = st.sidebar.selectbox(
        "🏟️ Select Host Venue",
        options=stadium_options,
        index=current_index,
        help="Choose a FIFA World Cup 2026 host stadium to monitor",
    )
    new_stadium_id: str = stadium_mapping[selected_label]

    if new_stadium_id != st.session_state.selected_stadium:
        st.session_state.selected_stadium = new_stadium_id
        # Chat is not stadium-scoped, so clear it; alert history/latest are keyed
        # per stadium and simply display the newly selected venue's slice.
        st.session_state.chat_history = []
        st.rerun()

    st.sidebar.divider()

    view_selection: str = st.sidebar.radio(
        "🗺️ View Mode",
        options=[
            "Fan Mobile Assistant",
            "Stadium Navigator",
            "Stadium Operations Command Center",
        ],
        help="Switch between the fan assistant, reasoned entry navigation, and operations control",
    )

    st.sidebar.divider()

    st.sidebar.subheader("🔄 Control Panel")

    is_uploaded_slot: bool = st.session_state.selected_stadium == UPLOADED_STADIUM_ID

    if is_uploaded_slot:
        # The uploaded slot has no simulator engine to tick — refresh/auto-refresh
        # would fail, so we surface the upload path instead.
        st.sidebar.caption("📤 Showing an uploaded dataset. Load a new file below to update it.")
    else:
        if st.sidebar.button(
            "Refresh Live Data",
            width="stretch",
            help="Pull fresh telemetry data from the stadium simulation engine",
        ):
            refresh_snapshot(st.session_state.selected_stadium)
            # The prior AI check is stale once telemetry changes; clear it for this venue.
            st.session_state.latest_alert_check[st.session_state.selected_stadium] = None
            st.toast("Updated live stadium telemetry snapshot!")
            st.rerun()

        # Opt-in live mode. Defaults OFF; when enabled, a guarded fragment ticker
        # pulls fresh telemetry on an interval without a full-page rerun. Only
        # meaningful for simulator-backed venues.
        auto_refresh: bool = st.sidebar.toggle(
            "🟢 Live auto-refresh",
            value=AUTO_REFRESH_DEFAULT,
            help=f"Automatically pull a fresh snapshot every {AUTO_REFRESH_INTERVAL_S}s (experimental).",
        )
        if auto_refresh:
            _run_auto_refresh(st.session_state.selected_stadium)

    _render_upload_control()

    snapshot: dict[str, Any] | None = st.session_state.latest_snapshots[st.session_state.selected_stadium]
    if snapshot:
        st.sidebar.caption(f"📡 Telemetry: {_relative_sync_label(snapshot.get('timestamp'))}")
    else:
        st.sidebar.caption("📡 Telemetry: No data loaded yet.")

    if view_selection == "Fan Mobile Assistant":
        render_fan_view(snapshot, st.session_state.selected_stadium)
    elif view_selection == "Stadium Navigator":
        render_map_view(snapshot, st.session_state.selected_stadium)
    else:
        render_ops_view(snapshot, st.session_state.selected_stadium)


if __name__ == "__main__":
    main()
