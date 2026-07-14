"""Unit tests for pure helpers in app.py.

app.py is mostly Streamlit glue exercised by the AppTest smoke suite, but a few
helpers hold real, testable logic: the relative-time formatter shown in the
sidebar, and the guarded auto-refresh runner. These are covered directly here.
"""

import datetime

import app


def test_relative_sync_label_none():
    assert app._relative_sync_label(None) == "No telemetry loaded yet."
    assert app._relative_sync_label("") == "No telemetry loaded yet."


def test_relative_sync_label_seconds():
    now = datetime.datetime.now(datetime.UTC)
    ts = (now - datetime.timedelta(seconds=20)).isoformat()
    assert app._relative_sync_label(ts) == "synced 20s ago"


def test_relative_sync_label_minutes():
    now = datetime.datetime.now(datetime.UTC)
    ts = (now - datetime.timedelta(minutes=7)).isoformat()
    assert app._relative_sync_label(ts) == "synced 7m ago"


def test_relative_sync_label_hours():
    now = datetime.datetime.now(datetime.UTC)
    ts = (now - datetime.timedelta(hours=3)).isoformat()
    assert app._relative_sync_label(ts) == "synced 3h ago"


def test_relative_sync_label_naive_timestamp_treated_as_utc():
    """A timestamp without tzinfo must not raise; it's assumed UTC."""
    label = app._relative_sync_label("2020-01-01T00:00:00")
    assert "synced" in label  # far in the past → "Nh ago"


def test_relative_sync_label_unparseable_falls_back_to_raw():
    assert app._relative_sync_label("not-a-timestamp") == "not-a-timestamp"


def test_relative_sync_label_future_timestamp():
    """A slightly-future timestamp (clock skew) reads as 'just now', never negative."""
    now = datetime.datetime.now(datetime.UTC)
    ts = (now + datetime.timedelta(seconds=30)).isoformat()
    assert "just now" in app._relative_sync_label(ts)


def test_run_auto_refresh_without_fragment_api(monkeypatch):
    """On a Streamlit build lacking st.fragment, auto-refresh degrades quietly."""
    captured = {}
    monkeypatch.setattr(app.st, "fragment", None, raising=False)
    monkeypatch.setattr(app.st.sidebar, "caption", lambda msg: captured.setdefault("msg", msg))
    app._run_auto_refresh("metlife")
    assert "manual refresh" in captured["msg"].lower()
