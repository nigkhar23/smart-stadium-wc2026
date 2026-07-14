"""Tests for the uploaded-slot session-state handling in ui.state_manager.

These exercise the state logic added for the evaluator data-upload path without
a running Streamlit server, by driving the module's functions against a stub
``st.session_state``. Guards two behaviours a regression could silently break:
the uploaded slot is isolated per-stadium, and applying a new upload invalidates
the prior AI verdict so a stale alert never displays against fresh data.
"""

import sys
import types

import pytest


class _SessionState(dict):
    """dict that also supports attribute access, like st.session_state."""

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError as err:
            raise AttributeError(name) from err

    def __setattr__(self, name, value):
        self[name] = value


@pytest.fixture
def state_manager(monkeypatch):
    """Import ui.state_manager with a stubbed streamlit module."""
    fake_st = types.ModuleType("streamlit")
    fake_st.session_state = _SessionState()

    def _cache_resource(fn):
        return fn

    fake_st.cache_resource = _cache_resource
    monkeypatch.setitem(sys.modules, "streamlit", fake_st)

    # Import fresh so it binds to the stubbed streamlit.
    for mod in ("ui.state_manager",):
        sys.modules.pop(mod, None)
    import ui.state_manager as sm

    return sm


def test_all_stadium_ids_includes_uploaded(state_manager):
    sm = state_manager
    assert sm.UPLOADED_STADIUM_ID == "uploaded"
    assert sm.UPLOADED_STADIUM_ID in sm.ALL_STADIUM_IDS
    assert sm.UPLOADED_STADIUM_ID not in sm.VALID_STADIUM_IDS


def test_apply_uploaded_snapshot_stores_and_invalidates(state_manager):
    sm = state_manager
    sm.init_session_state()
    uid = sm.UPLOADED_STADIUM_ID

    # Pretend a previous upload had already produced an AI verdict.
    sm.st.session_state.latest_alert_check[uid] = {"alert_triggered": True, "stale": True}

    snap = {"stadium_id": uid, "gates": [], "concessions": [], "source": "upload"}
    sm.apply_uploaded_snapshot(snap)

    assert sm.st.session_state.latest_snapshots[uid] is snap
    # Prior verdict must be cleared so it can't render against the new dataset.
    assert sm.st.session_state.latest_alert_check[uid] is None


def test_refresh_rejects_uploaded_slot(state_manager):
    """The uploaded slot has no simulator, so refreshing it must raise."""
    sm = state_manager
    sm.init_session_state()
    with pytest.raises(ValueError):
        sm.refresh_snapshot(sm.UPLOADED_STADIUM_ID)


def test_per_stadium_alert_isolation(state_manager):
    sm = state_manager
    sm.init_session_state()
    # Every id (incl. uploaded) gets its own isolated alert slot.
    for sid in sm.ALL_STADIUM_IDS:
        assert sid in sm.st.session_state.alert_history
        assert sid in sm.st.session_state.latest_alert_check
