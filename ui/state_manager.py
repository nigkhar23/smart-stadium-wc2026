"""State manager for the Streamlit application.

Handles initializing Streamlit session state and refreshing data snapshots.
"""

from typing import Any

import streamlit as st
from typing_extensions import TypedDict

from simulator.data_simulator import StadiumDataSimulator

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VALID_STADIUM_IDS: tuple[str, ...] = ("metlife", "azteca", "bcplace")
"""Tuple of simulator-backed stadium identifiers used across the application."""

UPLOADED_STADIUM_ID: str = "uploaded"
"""Pseudo-stadium id under which an evaluator's uploaded snapshot is stored.

It is not simulator-backed: refreshing it is a no-op (there is no engine to
tick), so the app keeps showing the uploaded data until a new file is loaded.
Keying it like a real venue lets uploaded data flow through the identical fan
and ops views with zero special-casing downstream."""

# Every id that can appear as the "selected stadium" — the three simulated
# venues plus the uploaded-data slot.
ALL_STADIUM_IDS: tuple[str, ...] = VALID_STADIUM_IDS + (UPLOADED_STADIUM_ID,)

MAX_ALERT_HISTORY: int = 20
"""Maximum number of alert entries retained in session state."""

MAX_CHAT_HISTORY: int = 50
"""Maximum number of chat messages retained in session state."""


# ---------------------------------------------------------------------------
# TypedDict shapes
# ---------------------------------------------------------------------------


class ChatMessage(TypedDict):
    """Shape of a single chat-history entry stored in session state."""

    role: str
    content: str


class SnapshotShape(TypedDict, total=False):
    """Loose shape of a stadium data snapshot.

    ``total=False`` because individual keys are populated dynamically by the
    simulator and not every key is guaranteed on every snapshot.
    """

    stadium_id: str
    timestamp: str
    gates: list[dict[str, Any]]
    concessions: list[dict[str, Any]]
    weather: dict[str, Any]
    security: dict[str, Any]
    power: dict[str, Any]


# ---------------------------------------------------------------------------
# Session-state helpers
# ---------------------------------------------------------------------------


@st.cache_resource
def _get_cached_simulators() -> dict[str, StadiumDataSimulator]:
    return {sid: StadiumDataSimulator(sid) for sid in VALID_STADIUM_IDS}


def init_session_state() -> None:
    """Initialise standard state variables in ``st.session_state`` if not already present.

    Creates simulator instances for every valid stadium, empty snapshot slots,
    and empty chat / alert history lists capped at their respective maximums.
    """
    if "simulators" not in st.session_state:
        st.session_state.simulators = _get_cached_simulators()

    # NB: annotations cannot be attached to attribute assignments
    # (``st.session_state.x: T = ...`` is a syntax-level type error), so the
    # intended shapes are documented in comments instead.
    if "selected_stadium" not in st.session_state:
        st.session_state.selected_stadium = VALID_STADIUM_IDS[0]  # str

    # Per-stadium dicts span ALL ids (the three simulated venues + the uploaded
    # slot) so an evaluator's dataset gets its own isolated snapshot/alert state.
    if "latest_snapshots" not in st.session_state:
        # dict[str, SnapshotShape | None]
        st.session_state.latest_snapshots = dict.fromkeys(ALL_STADIUM_IDS)

    if "chat_history" not in st.session_state:
        st.session_state.chat_history = []  # list[ChatMessage]

    # Alert history and the latest check are keyed *per stadium* so switching
    # venues never surfaces another stadium's alerts (state desync guard).
    if "alert_history" not in st.session_state:
        # dict[str, list[dict[str, Any]]]
        st.session_state.alert_history = {sid: [] for sid in ALL_STADIUM_IDS}

    if "latest_alert_check" not in st.session_state:
        # dict[str, dict[str, Any] | None]
        st.session_state.latest_alert_check = dict.fromkeys(ALL_STADIUM_IDS)


def refresh_snapshot(stadium_id: str) -> dict[str, Any]:
    """Generate a new data snapshot for *stadium_id* and update session state.

    Args:
        stadium_id: One of the identifiers listed in
            :pydata:`VALID_STADIUM_IDS`.

    Returns:
        The freshly generated snapshot dictionary.

    Raises:
        ValueError: If *stadium_id* is not a recognised stadium identifier.
    """
    if stadium_id not in VALID_STADIUM_IDS:
        raise ValueError(f"Invalid stadium_id '{stadium_id}'. Must be one of {VALID_STADIUM_IDS}.")

    init_session_state()

    sim: StadiumDataSimulator = st.session_state.simulators[stadium_id]
    snapshot: dict[str, Any] = sim.generate_snapshot()
    st.session_state.latest_snapshots[stadium_id] = snapshot

    # Alert-history trimming is owned by the ops view (which inserts newest-first);
    # trimming it here too previously used the opposite end and dropped new entries.

    # Trim chat history to the configured maximum (oldest-first list, keep the tail).
    if len(st.session_state.chat_history) > MAX_CHAT_HISTORY:
        st.session_state.chat_history = st.session_state.chat_history[-MAX_CHAT_HISTORY:]

    return snapshot


def apply_uploaded_snapshot(snapshot: dict[str, Any]) -> None:
    """Store an evaluator's normalised upload under the uploaded-data slot.

    Mirrors what :func:`refresh_snapshot` does for a simulated venue: it writes
    the snapshot and invalidates the prior AI alert check for that slot (a new
    dataset makes the previous evaluation stale). The snapshot is expected to be
    already normalised (see :mod:`simulator.ingestion`).

    Args:
        snapshot: A normalised, upload-sourced snapshot dictionary.
    """
    init_session_state()
    st.session_state.latest_snapshots[UPLOADED_STADIUM_ID] = snapshot
    st.session_state.latest_alert_check[UPLOADED_STADIUM_ID] = None
