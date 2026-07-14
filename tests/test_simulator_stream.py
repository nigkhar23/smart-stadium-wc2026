"""Tests for the simulator's streaming loop and CLI entry point.

The core state-evolution logic is covered elsewhere; this module exercises the
``run_stream`` output loop (duration-bounded, stop-event, name-prefixing) and the
``main`` CLI (single-stadium and multi-stadium fan-out), which are otherwise only
reachable by running the module as a script.
"""

import json
import threading

import pytest

from simulator.data_simulator import StadiumDataSimulator, main


def test_run_stream_duration_bounded_emits_json(capsys):
    """A short duration-bounded run prints at least one valid JSON snapshot.

    Snapshots are pretty-printed and newline-separated; split on the closing
    brace at column 0 to isolate the first complete document and parse it.
    """
    sim = StadiumDataSimulator("metlife", seed=1)
    sim.run_stream(interval_seconds=0.01, duration_seconds=0.05)
    out = capsys.readouterr().out
    assert out.strip(), "expected at least one snapshot on stdout"
    # raw_decode reads exactly one JSON document from the front of the stream,
    # ignoring the trailing snapshots — robust to pretty-printed nested braces.
    first, _end = json.JSONDecoder().raw_decode(out.lstrip())
    assert first["stadium_id"] == "metlife"
    assert "gates" in first and "concessions" in first


def test_run_stream_stops_on_event():
    """A pre-set stop_event makes run_stream return immediately."""
    sim = StadiumDataSimulator("azteca", seed=2)
    stop = threading.Event()
    stop.set()
    # Should return at once without blocking on the interval.
    sim.run_stream(interval_seconds=10.0, duration_seconds=None, stop_event=stop)


def test_run_stream_prefixes_name(capsys):
    """prefix_name mode tags every output line with the stadium name."""
    sim = StadiumDataSimulator("bcplace", seed=3)
    sim.run_stream(interval_seconds=0.01, duration_seconds=0.03, prefix_name=True)
    out = capsys.readouterr().out.strip()
    assert out
    assert all(line.startswith("[BC Place]") for line in out.splitlines() if line)


def test_main_single_stadium(monkeypatch, capsys):
    """CLI runs a single stadium for a bounded duration and exits cleanly."""
    monkeypatch.setattr(
        "sys.argv",
        ["prog", "--stadium", "metlife", "--interval", "0.01", "--duration", "0.05"],
    )
    main()
    assert "metlife" in capsys.readouterr().out


def test_main_all_stadiums_prefixed(monkeypatch, capsys):
    """CLI 'all' fans out to threads and prefixes each stadium's output."""
    monkeypatch.setattr(
        "sys.argv",
        ["prog", "--stadium", "all", "--interval", "0.01", "--duration", "0.05"],
    )
    main()
    out = capsys.readouterr().out
    # At least one venue name should appear as a line prefix.
    assert "[MetLife Stadium]" in out or "[Estadio Azteca]" in out or "[BC Place]" in out


def test_main_invalid_stadium_exits(monkeypatch):
    """An unknown --stadium is rejected by argparse with SystemExit."""
    monkeypatch.setattr("sys.argv", ["prog", "--stadium", "nonexistent"])
    with pytest.raises(SystemExit):
        main()
