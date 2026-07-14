"""End-to-end render smoke tests for the Streamlit app.

Unit tests exercise the brain and ingestion logic, but they cannot catch
render-time failures inside the Streamlit views (e.g. passing an argument a
charting call rejects). These tests drive the real ``app.py`` through Streamlit's
``AppTest`` harness with no API key set (the offline path), asserting that every
primary screen renders without raising — the "no broken functionality" bar the
challenge is functionally evaluated against.

They are skipped automatically if the installed Streamlit lacks the testing API.
"""

import os

import pytest

st_testing = pytest.importorskip("streamlit.testing.v1")
from streamlit.testing.v1 import AppTest  # noqa: E402

from simulator.ingestion import build_snapshot_from_upload  # noqa: E402

APP = os.path.join(os.path.dirname(os.path.dirname(__file__)), "app.py")


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    """Force the offline path so no real Gemini call is attempted."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)


_VIEW_LABELS = {
    "Ops": "Stadium Operations Command Center",
    "Operations": "Stadium Operations Command Center",
    "Navigator": "Stadium Navigator",
    "Fan": "Fan Mobile Assistant",
}


def _set_view(at: "AppTest", label_contains: str) -> None:
    target = next(
        (v for k, v in _VIEW_LABELS.items() if k in label_contains),
        "Fan Mobile Assistant",
    )
    for r in at.radio:
        if "View Mode" in (r.label or ""):
            r.set_value(target).run()
            return


def test_app_initial_render_ok():
    at = AppTest.from_file(APP, default_timeout=60).run()
    assert not at.exception


def test_ops_view_refresh_and_evaluate_render_ok():
    """The ops dashboard (with charts) must render after refresh + evaluate.

    Regression guard for the chart-width crash: ``st.bar_chart`` rejected
    ``width="stretch"`` and took down the entire Operations Command Center.
    """
    at = AppTest.from_file(APP, default_timeout=60).run()
    _set_view(at, "Operations")
    for b in at.button:
        if "Refresh" in (b.label or ""):
            b.click().run()
    assert not at.exception, f"ops render after refresh raised: {at.exception}"
    for b in at.button:
        if "Evaluate" in (b.label or ""):
            b.click().run()
    assert not at.exception, f"ops evaluate raised: {at.exception}"


def test_fan_view_language_and_query_render_ok():
    at = AppTest.from_file(APP, default_timeout=60).run()
    for b in at.button:
        if "Refresh" in (b.label or ""):
            b.click().run()
    langs = [s for s in at.selectbox if "language" in (s.label or "").lower()]
    if langs:
        langs[0].set_value("Spanish").run()
    if at.chat_input:
        at.chat_input[0].set_value("where is the shortest food line?").run()
    assert not at.exception


def test_fan_view_suggested_question_chip_runs_query():
    """A suggested-question chip must feed the same path as typed input.

    Clicking a starter chip should produce a user+assistant exchange in
    chat_history and then hide the chips (empty-state only), without raising.
    """
    from ui.fan_view import SUGGESTED_QUESTIONS

    chip_labels = [label for label, _query in SUGGESTED_QUESTIONS]

    at = AppTest.from_file(APP, default_timeout=60).run()
    for b in at.button:
        if "Refresh" in (b.label or ""):
            b.click().run()

    # The starter chips should be present before any conversation exists.
    chips = [b for b in at.button if (b.label or "") in chip_labels]
    assert chips, "expected suggested-question chips in the empty chat state"

    # Click the first chip; its stored query is the tuple's query field, which
    # may differ from the button label (e.g. a 🌐-marked multilingual chip).
    first_label, first_query = SUGGESTED_QUESTIONS[0]
    chips[0].click().run()
    assert not at.exception, f"suggested chip click raised: {at.exception}"

    history = at.session_state["chat_history"]
    assert len(history) >= 2, "chip click should append a user + assistant turn"
    assert history[0]["role"] == "user"
    assert history[0]["content"] == first_query

    # Chips are empty-state only: once history exists they must be gone.
    assert not [b for b in at.button if (b.label or "") in chip_labels]


def test_navigator_view_reasoned_reroute_renders_ok():
    """The Stadium Navigator must render and produce a reasoned reroute offline.

    Regression guard for the navigation vertical: switch to the Navigator, pick a
    destination, request a step-free route, and click Find — the SVG map,
    recommendation, and reasoning must render without raising on the offline path.
    """
    at = AppTest.from_file(APP, default_timeout=90).run()
    for b in at.button:
        if "Refresh" in (b.label or ""):
            b.click().run()

    _set_view(at, "Navigator")
    assert not at.exception, f"navigator render raised: {at.exception}"

    # Flip the step-free accessibility toggle if present.
    for tg in getattr(at, "toggle", []):
        if "step-free" in (tg.label or "").lower():
            tg.set_value(True).run()
    assert not at.exception, f"step-free toggle raised: {at.exception}"

    # Click "Find my best entry" and confirm a guidance object was stored.
    clicked = False
    for b in at.button:
        if "Find my best entry" in (b.label or ""):
            b.click().run()
            clicked = True
    assert clicked, "expected the 'Find my best entry' button in the Navigator"
    assert not at.exception, f"navigator reroute raised: {at.exception}"

    guidance = at.session_state["nav_last_guidance"]
    assert guidance["recommended_gate"], "a gate should be recommended"
    assert guidance["reasoning"], "reasoning (XAI) should be populated"


def test_uploaded_dataset_renders_in_both_views():
    """A normalised uploaded snapshot drives both views with no special-casing."""
    at = AppTest.from_file(APP, default_timeout=60).run()

    csv = (
        b"entity_type,gate_id,crowd_density_pct,stand_name,avg_wait_time_min\n"
        b"gate,Gate A,94,,\n"
        b"gate,Gate B,30,,\n"
        b"concession,,,Taco Stand,28\n"
    )
    snap, _ = build_snapshot_from_upload(csv, "eval.csv")
    at.session_state["latest_snapshots"]["uploaded"] = snap
    at.session_state["selected_stadium"] = "uploaded"
    at.run()
    assert not at.exception, f"fan render of upload raised: {at.exception}"

    # Refresh must be hidden for the uploaded slot (no simulator to tick).
    assert not [b for b in at.button if "Refresh" in (b.label or "")]

    _set_view(at, "Operations")
    for b in at.button:
        if "Evaluate" in (b.label or ""):
            b.click().run()
    assert not at.exception, f"ops render/evaluate of upload raised: {at.exception}"
