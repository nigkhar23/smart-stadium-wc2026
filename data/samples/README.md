# Sample evaluator datasets

These files demonstrate the shapes the app's **📤 Evaluate With Your Data**
uploader accepts. Upload either one from the sidebar to drive the entire app
(fan assistant + operations dashboard) from real data instead of the built-in
simulator.

| File | Shape | Notes |
|---|---|---|
| `stadium_snapshot.csv` | One mixed table; an `entity_type` column marks each row as `gate` or `concession`. | Includes a Critical gate (94%) and a >25 min concession queue, so the ops check trips immediately. |
| `stadium_snapshot.json` | A full snapshot object with `gates`, `concessions`, and `security`. | Uses a different venue/gate naming scheme to prove nothing is hard-coded to the three demo stadiums. |

## What the parser accepts

The ingestion layer (`simulator/ingestion.py`) is deliberately forgiving:

- **Column names are aliased and case-insensitive** — `Crowd Density (%)`,
  `crowd_density`, `occupancy`, and `density` all map to the same field; likewise
  `Avg Wait Time (min)`, `wait`, `queue_wait`, etc.
- **Units and separators are tolerated** — `"85%"`, `"28 min"`, and `"1,200"` all parse.
- **`density_status` is always recomputed** from the percentage, so an uploaded
  status label can never disagree with the number the dashboard charts.
- **Rows can be one kind or mixed** — a gate-only or concession-only table works;
  a mixed table needs a `type`/`entity_type` column to disambiguate.
- **JSON** may be a full snapshot object, a partial object, or a bare array of rows.

Minimum required to be accepted: at least one gate row (id + density) **or** one
concession row (name + wait time). A file with neither is rejected with a clear
message rather than silently rendering an empty dashboard.
