"""Unit tests for the Smart Stadium Data Simulator.

Tests coverage includes schema correctness, data boundary values,
logical derivations, security rules, and state continuity.
"""

import pytest

from simulator.config import SECURITY_LEVELS
from simulator.data_simulator import StadiumDataSimulator


@pytest.fixture
def simulator() -> StadiumDataSimulator:
    """Fixture to initialize a StadiumDataSimulator instance."""
    return StadiumDataSimulator("metlife")


def test_snapshot_schema_keys(simulator: StadiumDataSimulator) -> None:
    """Verifies that the generated snapshot contains all required keys and correct sub-keys."""
    snapshot = simulator.generate_snapshot()

    # Top level keys
    expected_top_keys = {
        "timestamp",
        "stadium_id",
        "stadium_name",
        "match_status",
        "gates",
        "concessions",
        "security",
    }
    assert expected_top_keys.issubset(snapshot.keys())

    # Gates structure
    assert isinstance(snapshot["gates"], list)
    assert len(snapshot["gates"]) > 0
    expected_gate_keys = {
        "gate_id",
        "crowd_density_pct",
        "density_status",
        "entries_last_5min",
    }
    for gate in snapshot["gates"]:
        assert expected_gate_keys.issubset(gate.keys())

    # Concessions structure
    assert isinstance(snapshot["concessions"], list)
    assert len(snapshot["concessions"]) > 0
    expected_concession_keys = {
        "stand_name",
        "avg_wait_time_min",
        "queue_length",
        "status",
    }
    for concession in snapshot["concessions"]:
        assert expected_concession_keys.issubset(concession.keys())

    # Security structure
    assert isinstance(snapshot["security"], dict)
    expected_security_keys = {
        "alert_level",
        "last_updated",
        "active_incidents",
        "notes",
    }
    assert expected_security_keys.issubset(snapshot["security"].keys())


def test_crowd_density_limits(simulator: StadiumDataSimulator) -> None:
    """Verifies that crowd density is always between 0 and 100%."""
    # Test over multiple ticks to verify accumulated state changes stay within bounds
    for _ in range(50):
        snapshot = simulator.generate_snapshot()
        for gate in snapshot["gates"]:
            assert 0 <= gate["crowd_density_pct"] <= 100


def test_density_status_thresholds(simulator: StadiumDataSimulator) -> None:
    """Verifies that density_status matches the crowd_density_pct based on thresholds.

    Thresholds:
    - 0-40: Low
    - 41-70: Moderate
    - 71-90: High
    - 91-100: Critical
    """
    # Directly test helper method
    assert simulator._get_density_status(0) == "Low"
    assert simulator._get_density_status(40) == "Low"
    assert simulator._get_density_status(41) == "Moderate"
    assert simulator._get_density_status(70) == "Moderate"
    assert simulator._get_density_status(71) == "High"
    assert simulator._get_density_status(90) == "High"
    assert simulator._get_density_status(91) == "Critical"
    assert simulator._get_density_status(100) == "Critical"

    # Test values in generated snapshot
    for _ in range(20):
        snapshot = simulator.generate_snapshot()
        for gate in snapshot["gates"]:
            pct = gate["crowd_density_pct"]
            status = gate["density_status"]
            if pct <= 40:
                assert status == "Low"
            elif pct <= 70:
                assert status == "Moderate"
            elif pct <= 90:
                assert status == "High"
            else:
                assert status == "Critical"


def test_security_alert_level_validity(simulator: StadiumDataSimulator) -> None:
    """Verifies that the alert level is always one of the 4 valid values."""
    for _ in range(50):
        snapshot = simulator.generate_snapshot()
        alert_level = snapshot["security"]["alert_level"]
        assert alert_level in SECURITY_LEVELS

        # Verify relationship of active incidents based on alert level
        active_incidents = snapshot["security"]["active_incidents"]
        if alert_level == "Green":
            assert active_incidents == 0
        elif alert_level == "Yellow":
            assert active_incidents in (1, 2)
        elif alert_level == "Orange":
            assert 2 <= active_incidents <= 4
        elif alert_level == "Red":
            assert 3 <= active_incidents <= 6


def test_state_evolution_continuity(simulator: StadiumDataSimulator) -> None:
    """Verifies that two consecutive snapshots produce different but plausible values.

    Checks that:
    - Values are not static (they evolve).
    - The difference in gate density is within the configured maximum delta (±5%).
    """
    snapshot_1 = simulator.generate_snapshot()
    snapshot_2 = simulator.generate_snapshot()

    # Map snapshot_1 gates by ID for easy comparison
    gates_1 = {g["gate_id"]: g for g in snapshot_1["gates"]}
    gates_2 = {g["gate_id"]: g for g in snapshot_2["gates"]}

    has_changes = False
    for gate_id, gate_1 in gates_1.items():
        gate_2 = gates_2[gate_id]
        diff = abs(gate_2["crowd_density_pct"] - gate_1["crowd_density_pct"])

        # Maximum allowed change is 5 percentage points
        assert diff <= 5

        if diff != 0:
            has_changes = True

    # Check concessions continuity
    concessions_1 = {c["stand_name"]: c for c in snapshot_1["concessions"]}
    concessions_2 = {c["stand_name"]: c for c in snapshot_2["concessions"]}

    for stand_name, c1 in concessions_1.items():
        c2 = concessions_2[stand_name]

        # If the stand was closed/opened, wait time might go to 0 or start from a baseline,
        # but if it remained open, wait time should evolve by at most 2.0 mins (plus noise/clamp limits)
        if c1["status"] != "Temporarily Closed" and c2["status"] != "Temporarily Closed":
            wait_diff = abs(c2["avg_wait_time_min"] - c1["avg_wait_time_min"])
            assert wait_diff <= 2.5  # Clamped difference is around 2.0 max change

    # Ensure that state actually changes over ticks (is not static)
    assert has_changes, "Expect some variation in gate densities between ticks"
