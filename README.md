# ⚽ Smart Stadium — FIFA World Cup 2026

> Real-time stadium operations intelligence and a multilingual, **reasoning** fan-facing assistant, powered by the Google Gen AI SDK (`gemini-2.5-flash`) with **constrained decoding**, an **evaluator data-upload path**, and a **zero-crash offline fallback**.

![Tests](https://img.shields.io/badge/tests-107%20passing-brightgreen)
![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12-blue)
![Model](https://img.shields.io/badge/model-gemini--2.5--flash-4285F4)

A dual-audience platform for FIFA World Cup 2026 host venues (MetLife, Estadio Azteca, BC Place):

- **Fans** get a mobile chat assistant that answers "which gate is least crowded?" / "where's the shortest food line?" from live telemetry — **in their own language**, with an on-demand **"why this answer?"** explanation.
- **Operators** get a command-center dashboard with color-coded gate KPIs, wait-time charts, security intelligence, and one-click AI-assisted alert analysis that **explains its reasoning**, not just its verdict.
- **Evaluators** can **upload their own CSV/JSON** of real stadium telemetry and run the whole app against it — no code path is special-cased for the built-in demo data.

A stateful simulator drives realistic match-day dynamics (pre-match gate congestion → halftime concession rush → post-match dispersal), so the whole system is demonstrable with **no external data feed** — or point it at real data via the upload path.

### Why this isn't a "wrapper app"

| Concern | Design choice |
|---|---|
| **The AI is load-bearing** | The LLM does what plain rules cannot: it **reasons** — comparing options, weighing trade-offs, emitting an explicit *why* (`reasoning` field) — and answers **in the fan's own language with locally-appropriate register**. The deterministic layer decides *when* to spend a call; the model decides *what to say*. |
| **Cost & latency** | The Python pre-check gates *every* ops LLM call, so a normal tick makes **zero** API calls — cost control by design, not by dropping intelligence. |
| **Output trust** | Gemini runs with `response_schema` **constrained decoding** — the model is structurally unable to emit unknown keys or invalid enums. Hand-written validators are a second safety net, not the first. |
| **Evaluator-ready** | A judge can **upload their own CSV/JSON** and drive the entire app from it; the ingestion layer normalizes messy real data (aliased columns, dirty units) into the internal schema. See [`data/samples/`](data/samples/). |
| **Resilience** | Every AI routine degrades to a **data-driven** fallback grounded in the live snapshot (not a canned string), so a network/quota blip never crashes a demo. The offline fallback is a reliability net — the *live intelligence* (multilingual, register-aware, novel reasoning) is the model's job. |
| **State integrity** | Alert history and the latest check are keyed **per stadium** (including the uploaded-data slot), so switching venues never leaks another stadium's alerts into the view. |
| **Proof** | 107 tests — unit + ingestion edge cases + **headless render smoke tests** (Streamlit `AppTest`) — fully mocked/offline, all green with `pytest`. |

### Architecture

A strictly decoupled, four-layer flow with no circular dependencies:

```
┌──────────────┐   snapshot   ┌──────────────┐   pre-check   ┌──────────────┐
│  simulator/  │─────────────▶│    brain/    │──────────────▶│    Gemini    │
│ stateful     │   (dict)     │ deterministic│  only on      │ 2.5-flash    │
│ telemetry    │              │ gate + LLM   │  breach       │ (schema-      │
│ engine       │              │ orchestration│◀──────────────│  constrained)│
└──────────────┘              └──────┬───────┘   structured   └──────────────┘
                                     │ JSON        JSON
                            ┌────────▼────────┐
                            │      ui/        │   fan_view · ops_view
                            │ Streamlit views │   state_manager (per-stadium)
                            └────────┬────────┘
                                     │
                                ┌────▼────┐
                                │ app.py  │  thin router + sidebar controls
                                └─────────┘

Offline path: brain/fallbacks.py → schema-correct answers from the live snapshot
              (fires automatically on any missing-key / network / quota failure)
```

## Folder Structure

The project has the following directory layout:

```
smart-stadium-wc2026/
├── brain/
│   ├── __init__.py              # Python package marker
│   ├── stadium_brain.py         # Backend LLM routines (Google Gen AI)
│   ├── prompts.py               # System prompts for Fan Assistant & Ops Alerts
│   ├── schemas.py               # Output schemas, Gemini response schemas, validators
│   └── fallbacks.py             # Data-driven offline fallbacks (schema-correct)
├── data/
│   └── .gitkeep                 # Empty directory for future output snapshots/logs
├── simulator/
│   ├── __init__.py              # Python package marker
│   ├── config.py                # Static configurations
│   └── data_simulator.py        # Core simulation logic & CLI runner
├── ui/
│   ├── __init__.py              # Python package marker
│   ├── fan_view.py              # Fan mobile assistant interface
│   ├── ops_view.py              # Stadium operations command center dashboard
│   └── state_manager.py         # Streamlit session state and refresh managers
├── tests/
│   ├── test_data_simulator.py   # Unit tests for simulator
│   └── test_stadium_brain.py    # Unit tests for stadium brain
├── requirements.txt             # Project dependencies (pytest, google-genai, streamlit, pandas)
├── .gitignore                   # Files to ignore in Git
├── app.py                       # Thin frontend entry point
└── README.md                    # Project documentation (this file)
```

## Setup and Installation

1. Ensure you have Python 3.11+ installed (required by pandas 3.0 and the modern type-hint syntax).
2. It is recommended to use a virtual environment:
   ```powershell
   python -m venv .venv
   .venv\Scripts\activate
   ```
3. Install the dependencies:
   ```powershell
   pip install -r requirements.txt
   ```

## Running the Simulator

The simulator provides a CLI that can generate snapshots for a single stadium, or for all stadiums concurrently using threads.

To run the simulator for all stadiums:
```powershell
python -m simulator.data_simulator --stadium all
```

To run the simulator for a specific stadium (e.g., MetLife Stadium):
```powershell
python -m simulator.data_simulator --stadium metlife
```

### CLI Arguments
- `--stadium`: Choices are `metlife`, `azteca`, `bcplace`, or `all` (default: `all`).
- `--interval`: Tick frequency in seconds (default: `5`).
- `--duration`: Optional run duration limit in seconds (runs indefinitely if omitted).

## Phase 2 — Stadium Brain (LLM Layer)

The backend intelligence layer is implemented in `stadium_brain.py` and provides two main AI routines powered by the Google Gen AI SDK (`google-genai`) running the `gemini-2.5-flash` model with **constrained decoding** (`response_schema`) for guaranteed output structure:

1. **Fan Assistant (`ask_fan_assistant`)**: Interprets and responds to fan questions strictly using a live JSON data snapshot of the stadium's current state. It handles out-of-domain queries or empty snapshots gracefully, and outputs structured JSON responses matching a strict schema. On any API failure it degrades to a **data-driven** offline answer (`brain/fallbacks.py`) that still matches the full schema — the assistant stays intelligent even with no API key.
2. **Operations Alerts (`check_operational_alerts`)**: Performs a deterministic, fast pre-check in plain Python to verify whether critical thresholds are breached (concession queues > 25 min, crowd density at "Critical", OR security at Orange/Red). If a breach is confirmed, it calls Gemini to summarize, contextualize, and recommend immediate operational actions for stadium staff. If no breach exists, it skips the LLM call entirely to optimize cost and performance.

### Configuration
Set the `GEMINI_API_KEY` environment variable prior to calling LLM routines:
```powershell
# PowerShell
$env:GEMINI_API_KEY="your-api-key-here"

# Bash/macOS
export GEMINI_API_KEY="your-api-key-here"
```
*(With no key set, both routines transparently fall back to deterministic, snapshot-grounded responses so the app never crashes.)*

## Phase 3 — Streamlit Frontend

A visual user interface is built in `app.py` using Streamlit, featuring a dual-mode portal that shares a synchronized in-memory telemetry state:

1. **Fan Mobile Assistant View**: A clean, distraction-free chat container styled like a mobile assistant that lets fans ask questions and receive friendly, formatted recommendations.
2. **Stadium Operations Command Center View**: A high-density control-room dashboard presenting live gate KPIs (with color-coded severity), wait time bar charts, security status alert cards, and a module to run and review operations threshold reports.

### Running the Streamlit App
To launch the interactive dashboard:
```powershell
streamlit run app.py
```
*(Make sure to set the `GEMINI_API_KEY` environment variable in your terminal if you wish to use the LLM-powered features in the Fan Assistant or Ops Alert checks).*

### Data Refresh
- **Manual (default)**: Click **🔄 Refresh Live Data** in the sidebar to pull a fresh snapshot on demand — predictable and demo-friendly.
- **Live auto-refresh (opt-in)**: Toggle **🟢 Live auto-refresh** to stream a new snapshot every few seconds via an isolated `st.fragment(run_every=...)`, which reruns only the data fragment rather than the whole page (avoiding the session conflicts a naive loop would cause). It is OFF by default and degrades silently to manual refresh on older Streamlit builds.

### 📤 Evaluate With Your Own Data
The simulator is for the demo — but the app is built to run on **real** data. In the
sidebar, under **"📤 Evaluate With Your Data,"** upload a CSV or JSON file and the
whole app (fan assistant + ops dashboard) runs against it instead of the simulator.

The ingestion layer (`simulator/ingestion.py`) is deliberately forgiving so a jury
doesn't need to know our exact schema:
- **Aliased, case-insensitive columns** — `Crowd Density (%)`, `crowd_density`,
  `occupancy`, `density` all map to one field; likewise `Avg Wait Time (min)`, `wait`, etc.
- **Dirty units tolerated** — `"85%"`, `"28 min"`, `"1,200"` all parse.
- **`density_status` is always recomputed** from the percentage, so an uploaded label
  can never disagree with the number the dashboard charts.
- **Mixed or single-kind tables**, plus full-snapshot or bare-array JSON.

Ready-to-try samples live in [`data/samples/`](data/samples/) (`stadium_snapshot.csv`,
`stadium_snapshot.json`). A file with no recognizable gate/concession rows is rejected
with a clear message rather than silently rendering an empty dashboard.

### 🌐 Multilingual & 🧠 Explainable
- The Fan Assistant has an **answer-language selector** — it replies in the fan's own
  language with locally-appropriate register (a prompt-design task, not a raw
  translation-API call). Requires the live AI; offline mode answers in English and says so.
- Every fan answer and ops alert carries a **`reasoning`** field — the causal *why*
  behind the recommendation (which data points were compared, the trade-off weighed) —
  surfaced as a **"🧠 Why this answer?"** expander (fan) and a **Reasoning** line (ops).

### 🎬 Live Demo
See **[DEMO.md](DEMO.md)** for a rehearsed, beat-by-beat 60-second presentation script (including how to seed the simulator for a reproducible run).

## Running Tests

Verify the simulation layer and the LLM layer by running:
```powershell
# Run all tests
python -m pytest

# Run only simulator tests
python -m pytest tests/test_data_simulator.py

# Run only stadium brain tests (mocked, runs offline)
python -m pytest tests/test_stadium_brain.py
```

## Next Steps

This project serves as the foundational data, intelligence, and presentation layers. Future phases will build upon this by adding:
1. **API Layer**: A REST/WebSocket API server to expose these metrics to external consumers.
2. **Database Integration**: Time-series databases to store historical trends and security logs.


