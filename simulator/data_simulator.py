"""Smart Stadium Data Simulator.

This module simulates real-time data from World Cup 2026 stadiums,
including crowd density at gates, concession stand queues, and security levels,
generating realistic metrics that evolve over the course of a match.
"""

import argparse
import datetime
import json
import random
import sys
import threading
import time
from typing import Any

# Import configuration
from simulator.config import (
    CONCESSION_STANDS,
    GATES,
    SECURITY_LEVEL_WEIGHTS,
    SECURITY_LEVELS,
    STADIUMS,
    density_status_for_pct,
)

# Global lock for thread-safe output printing
print_lock = threading.Lock()

# --- Simulation Calibration Constants ---
# Maximum absolute change in crowd density percentage per tick
MAX_DENSITY_DELTA = 5.0

# Base factor to scale gate density to concession wait times (e.g. 80% density -> 20 min wait)
DENSITY_TO_WAIT_FACTOR = 0.25

# Minimum baseline wait time in minutes for concessions
MIN_WAIT_TIME_MIN = 0.5

# Halftime bonus wait time (in minutes) simulating the concession rush
HALFTIME_WAIT_BOOST = 8.0

# Average queue size per minute of wait time
QUEUE_PEOPLE_PER_MINUTE = 1.8

# Probability that a concession stand temporarily closes during a tick
CONCESSION_CLOSE_CHANCE = 0.01


class StadiumDataSimulator:
    """Simulates realistic, stateful data streams for a specific World Cup stadium."""

    def __init__(self, stadium_id: str, seed: int | None = None) -> None:
        """Initializes the simulator with stadium metadata and initial states.

        Args:
            stadium_id: Unique identifier for the stadium (e.g., 'metlife')
            seed: Optional RNG seed. When provided, this instance draws from a
                private ``random.Random(seed)`` so a run is fully reproducible
                (useful for scripted live demos and deterministic tests). When
                ``None``, it uses the shared module-level ``random`` — identical
                interface, so default behaviour is unchanged.

        Raises:
            ValueError: If the provided stadium_id is not configured.
        """
        # Find stadium configuration
        self.stadium_meta = next((s for s in STADIUMS if s["stadium_id"] == stadium_id), None)
        if not self.stadium_meta:
            valid_ids = [s["stadium_id"] for s in STADIUMS]
            raise ValueError(f"Invalid stadium_id '{stadium_id}'. Expected one of: {valid_ids}")

        # Always a private Random instance: seeded for reproducible runs, or
        # auto-seeded from the OS when seed is None. Using an instance (rather
        # than the module-level RNG) keeps this simulator's stream isolated.
        # PRNG drives fake telemetry only, never anything security-sensitive.
        self._rng: random.Random = random.Random(seed)

        self.stadium_id = stadium_id
        self.stadium_name = str(self.stadium_meta["name"])
        self.capacity = int(self.stadium_meta["capacity"])

        # Internal tick counter to drive match phase progression
        self._tick = 0

        # State initialization
        # Gates: start with low crowd density (around 10% - 20%)
        self.gate_densities: dict[str, float] = {gate: self._rng.uniform(10.0, 20.0) for gate in GATES}

        # Concessions: initial wait times and queues
        self.concession_waits: dict[str, float] = {stand: self._rng.uniform(2.0, 5.0) for stand in CONCESSION_STANDS}
        self.concession_queues: dict[str, int] = {
            stand: int(self.concession_waits[stand] * QUEUE_PEOPLE_PER_MINUTE) for stand in CONCESSION_STANDS
        }
        self.concession_status: dict[str, str] = dict.fromkeys(CONCESSION_STANDS, "Open")
        self.concession_closed_ticks: dict[str, int] = dict.fromkeys(CONCESSION_STANDS, 0)

        # Security: default state
        self.security_level = "Green"
        self.security_last_updated = datetime.datetime.now(datetime.UTC).isoformat()
        self.active_incidents = 0
        self.security_notes = "Routine monitoring"

        # Concession stand nearby gate mapping for localized density correlation
        self._gate_mapping = {
            "Main Concourse Grill": "Gate A",
            "Craft Beer & Snacks": "Gate B",
            "International Food Court": "Gate C",
            "Family Fan Zone Kiosk": "Gate D",
        }

    def _determine_match_status(self) -> str:
        """Determines the match status based on the current simulation tick.

        Returns:
            A string representing the match phase.
        """
        # Cycle representation: 100 ticks total
        # 0-29: pre-match, 30-59: live (1st half), 60-74: halftime,
        # 75-89: live (2nd half), 90-99: post-match
        cycle_tick = self._tick % 100
        if cycle_tick < 30:
            return "pre-match"
        if 30 <= cycle_tick < 60:
            return "live"
        if 60 <= cycle_tick < 75:
            return "halftime"
        if 75 <= cycle_tick < 90:
            return "live"
        return "post-match"

    def _get_density_status(self, pct: int) -> str:
        """Maps crowd density percentage to a text status.

        Delegates to :func:`simulator.config.density_status_for_pct` so the
        live simulator and the evaluator upload path share one banding rule.

        Args:
            pct: Crowd density percentage (0-100)

        Returns:
            Status string (Low, Moderate, High, Critical)
        """
        return density_status_for_pct(pct)

    def _update_gate_states(self, match_status: str) -> None:
        """Updates crowd density and gate entry volumes based on match phase.

        Args:
            match_status: Current phase of the match.
        """
        # Define bias direction based on match phase to simulate trends
        if match_status == "pre-match":
            # Flow is entering, trending upwards
            bias = 2.5
        elif match_status == "live":
            # Flow stabilizes at high capacity inside, but gate traffic fluctuates
            bias = 0.0
        elif match_status == "halftime":
            # Temporary decrease in entries
            bias = -1.5
        else:  # post-match
            # People exit (simulating a drop in gate entries, but gates might show outward density)
            # As per rules: "Crowd density should trend upward before kickoff,
            # peak during live, and drop during post-match"
            bias = -3.5

        for gate in GATES:
            # Generate a delta within simulation constraints
            delta = self._rng.uniform(-3.0, 3.0) + bias
            # Hard limit of absolute changes to max ±5% as required
            clamped_delta = max(-MAX_DENSITY_DELTA, min(MAX_DENSITY_DELTA, delta))
            new_density = self.gate_densities[gate] + clamped_delta
            # Clamp percentage between 0 and 100
            self.gate_densities[gate] = max(0.0, min(100.0, new_density))

    def _update_concession_states(self, match_status: str) -> None:
        """Updates concession wait times, queues, and operational status.

        Args:
            match_status: Current phase of the match.
        """
        for stand in CONCESSION_STANDS:
            # Handle temporary closure cooldowns
            if self.concession_closed_ticks[stand] > 0:
                self.concession_closed_ticks[stand] -= 1
                if self.concession_closed_ticks[stand] == 0:
                    self.concession_status[stand] = "Open"
                else:
                    self.concession_status[stand] = "Temporarily Closed"
                    self.concession_waits[stand] = 0.0
                    self.concession_queues[stand] = 0
                    continue

            # Random chance of closing
            if self._rng.random() < CONCESSION_CLOSE_CHANCE:
                self.concession_status[stand] = "Temporarily Closed"
                # Keep closed for 3 to 6 ticks
                self.concession_closed_ticks[stand] = self._rng.randint(3, 6)
                self.concession_waits[stand] = 0.0
                self.concession_queues[stand] = 0
                continue

            # Calculate target wait time based on nearby gate density
            nearby_gate = self._gate_mapping[stand]
            gate_density = self.gate_densities[nearby_gate]

            target_wait = gate_density * DENSITY_TO_WAIT_FACTOR + MIN_WAIT_TIME_MIN
            if match_status == "halftime":
                target_wait += HALFTIME_WAIT_BOOST

            # Evolve current wait toward target with a small delta (max change ±2.0 mins)
            diff = target_wait - self.concession_waits[stand]
            noise = self._rng.uniform(-0.5, 0.5)
            change = max(-2.0, min(2.0, diff * 0.4 + noise))

            new_wait = max(MIN_WAIT_TIME_MIN, self.concession_waits[stand] + change)
            self.concession_waits[stand] = round(new_wait, 1)

            # Update queue lengths proportionally to wait times with noise
            target_queue = int(self.concession_waits[stand] * QUEUE_PEOPLE_PER_MINUTE)
            queue_diff = target_queue - self.concession_queues[stand]
            queue_change = max(-3, min(3, queue_diff + self._rng.randint(-1, 1)))
            self.concession_queues[stand] = max(0, self.concession_queues[stand] + queue_change)

            # Determine demand status
            if self.concession_waits[stand] >= 15.0:
                self.concession_status[stand] = "High Demand"
            else:
                self.concession_status[stand] = "Open"

    def _update_security_state(self) -> None:
        """Updates security alert levels, active incidents, and notes."""
        # Weighted choice for alert levels: Green 85%, Yellow 10%, Orange 4%, Red 1%
        new_level = self._rng.choices(SECURITY_LEVELS, weights=SECURITY_LEVEL_WEIGHTS, k=1)[0]

        if new_level != self.security_level:
            self.security_level = new_level
            self.security_last_updated = datetime.datetime.now(datetime.UTC).isoformat()

        # Update active incidents and notes probabilistically based on level
        if self.security_level == "Green":
            self.active_incidents = 0
            self.security_notes = "Routine monitoring"
        elif self.security_level == "Yellow":
            # 1 to 2 incidents
            self.active_incidents = self._rng.randint(1, 2)
            yellow_notes = [
                f"Elevated crowd at {self._rng.choice(GATES)}",
                "Minor congestion at concessions area",
                "Reports of lost ticket/item",
            ]
            self.security_notes = self._rng.choice(yellow_notes)
        elif self.security_level == "Orange":
            # 2 to 4 incidents
            self.active_incidents = self._rng.randint(2, 4)
            orange_notes = [
                f"Severe congestion at {self._rng.choice(GATES)}",
                "Minor alteration near concessions",
                "Medical assistance dispatched to concourse",
            ]
            self.security_notes = self._rng.choice(orange_notes)
        else:  # Red
            # 3 to 6 incidents
            self.active_incidents = self._rng.randint(3, 6)
            red_notes = [
                f"Security incident near {self._rng.choice(GATES)} - access restricted",
                "Crowd control protocols activated",
                "Power outage reported in local subsector",
            ]
            self.security_notes = self._rng.choice(red_notes)

    def generate_snapshot(self) -> dict[str, Any]:
        """Generates a single stateful, JSON-serializable snapshot of stadium data.

        Returns:
            A dictionary containing the current simulated metrics of the stadium.
        """
        # Determine match phase
        match_status = self._determine_match_status()

        # Update simulated attributes
        self._update_gate_states(match_status)
        self._update_concession_states(match_status)
        self._update_security_state()

        # Increment simulation clock tick
        self._tick += 1

        # Format gate data
        gate_list = []
        for gate in GATES:
            density_pct = int(round(self.gate_densities[gate]))
            # Calculate entries scaled dynamically by stadium capacity and density
            # Scaled so peak density represents ~4-5% of gate share every 5 min
            # (realistic flow rate)
            gate_capacity_share = self.capacity / len(GATES)
            base_flow = gate_capacity_share * (density_pct / 100.0) * 0.04
            noise = self._rng.uniform(0.9, 1.1)
            entries = max(0, int(base_flow * noise))

            gate_list.append(
                {
                    "gate_id": gate,
                    "crowd_density_pct": density_pct,
                    "density_status": self._get_density_status(density_pct),
                    "entries_last_5min": entries,
                }
            )

        # Format concessions data
        concession_list = []
        for stand in CONCESSION_STANDS:
            concession_list.append(
                {
                    "stand_name": stand,
                    "avg_wait_time_min": self.concession_waits[stand],
                    "queue_length": self.concession_queues[stand],
                    "status": self.concession_status[stand],
                }
            )

        # Construct final snapshot
        now_utc = datetime.datetime.now(datetime.UTC).isoformat()
        return {
            "timestamp": now_utc,
            "stadium_id": self.stadium_id,
            "stadium_name": self.stadium_name,
            "match_status": match_status,
            "gates": gate_list,
            "concessions": concession_list,
            "security": {
                "alert_level": self.security_level,
                "last_updated": self.security_last_updated,
                "active_incidents": self.active_incidents,
                "notes": self.security_notes,
            },
        }

    def run_stream(
        self,
        interval_seconds: float = 5.0,
        duration_seconds: float | None = None,
        stop_event: threading.Event | None = None,
        prefix_name: bool = False,
    ) -> None:
        """Runs the simulation stream, printing formatted JSON snapshots to stdout.

        Args:
            interval_seconds: The frequency of data generation in seconds.
            duration_seconds: If provided, runs only for this amount of time.
            stop_event: Optional threading.Event used to stop the loop.
            prefix_name: If True, prefixes output lines with the stadium name.
        """
        start_time = time.time()

        try:
            while True:
                # Check signaling from parent thread
                if stop_event and stop_event.is_set():
                    break

                # Check duration limit
                if duration_seconds is not None and time.time() - start_time >= duration_seconds:
                    break

                # Generate and serialize snapshot
                snapshot = self.generate_snapshot()
                json_str = json.dumps(snapshot, indent=2)

                # Format output
                if prefix_name:
                    prefixed_lines = [f"[{self.stadium_name}] {line}" for line in json_str.splitlines()]
                    output = "\n".join(prefixed_lines) + "\n"
                else:
                    output = json_str + "\n"

                # Output stdout thread-safely
                with print_lock:
                    sys.stdout.write(output)
                    sys.stdout.flush()

                # Sleep responsively in small increments to check for stops
                sleep_start = time.time()
                while time.time() - sleep_start < interval_seconds:
                    if stop_event and stop_event.is_set():
                        break
                    time.sleep(0.1)

        except KeyboardInterrupt:
            # Handle keyboard interrupt signals cleanly (in single-threaded mode)
            if stop_event:
                stop_event.set()


def main() -> None:
    """CLI entry point for the Smart Stadium Data Simulator."""
    parser = argparse.ArgumentParser(description="FIFA World Cup 2026 Smart Stadium Data Simulator")
    parser.add_argument(
        "--stadium",
        choices=["metlife", "azteca", "bcplace", "all"],
        default="all",
        help="The stadium ID to simulate, or 'all' to run all 3 concurrently.",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=5.0,
        help="Time interval between data snapshots in seconds (default: 5.0).",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=None,
        help="Total duration to run the simulation in seconds (default: infinite).",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Optional RNG seed for reproducible, scriptable runs (default: random).",
    )

    args = parser.parse_args()

    # Determine simulation list
    stadium_ids = ["metlife", "azteca", "bcplace"] if args.stadium == "all" else [args.stadium]

    stop_event = threading.Event()
    threads = []
    prefix_name = len(stadium_ids) > 1

    # Initialize and spawn stream threads. When seeding multiple stadiums, offset
    # each seed by its index so the venues stay distinct yet fully reproducible.
    for idx, s_id in enumerate(stadium_ids):
        try:
            seed = args.seed + idx if args.seed is not None else None
            sim = StadiumDataSimulator(s_id, seed=seed)
        except ValueError as err:
            print(f"Initialization Error: {err}", file=sys.stderr)
            sys.exit(1)

        t = threading.Thread(
            target=sim.run_stream,
            args=(args.interval, args.duration, stop_event, prefix_name),
            daemon=True,
        )
        threads.append(t)

    # Start all threads
    for t in threads:
        t.start()

    start_time = time.time()
    try:
        while True:
            # Exit if all threads finished
            if not any(t.is_alive() for t in threads):
                break

            # Handle duration limit monitoring from parent thread
            if args.duration is not None and time.time() - start_time >= args.duration:
                stop_event.set()
                break

            time.sleep(0.2)

    except KeyboardInterrupt:
        with print_lock:
            sys.stdout.write("\nInterrupted. Stopping simulator streams...\n")
            sys.stdout.flush()
        stop_event.set()
    finally:
        # Join all threads with a timeout
        for t in threads:
            t.join(timeout=1.0)


if __name__ == "__main__":
    main()
