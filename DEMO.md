# 🎬 60-Second Demo Script

A beat-by-beat runbook for the judging room. Rehearse it once end-to-end. The
goal: prove the **AI is load-bearing** (multilingual + explainable reasoning),
that it **runs on the evaluator's own data**, and that it's **real engineering**
(deterministic cost-gate, schema-safe LLM, graceful degradation) — not "another
chatbot on top of an API."

> ⚠️ **The live key must be set for judging.** The multilingual answers and the
> model's reasoning are the differentiators, and they only appear on a real
> Gemini call. The offline fallback is a *reliability net* so a network blip
> never crashes you — it is not the thing you're demoing. Confirm the `⚡` chip.

## Pre-flight (do this before you present)

1. **Set the key** (unlocks the live AI path):
   ```powershell
   $env:GEMINI_API_KEY="your-key"      # PowerShell
   export GEMINI_API_KEY="your-key"    # bash/macOS
   ```
   On Streamlit Cloud, put it in **App → Settings → Secrets** as `GEMINI_API_KEY` instead.
2. **Launch:** `streamlit run app.py`
3. **Smoke test the live call once** (this is the one thing that can't be unit-tested):
   ask the fan assistant a question and confirm you see the `⚡ <n>s` latency chip —
   that chip *only* appears on a real Gemini call, so it's your proof the key works.
4. Land on the **Operations Command Center** view, MetLife selected, data refreshed.

## The script

| Time | Action | What you say |
|---|---|---|
| **0:00–0:10** | Land on the Ops dashboard (pre-refreshed). Gesture at the gate KPIs, wait-time chart, security card. | "This is a live stadium operations center. Every number is streaming from a stateful simulator that models a real match — pre-match surge, halftime food rush, post-match exit." |
| **0:10–0:25** | Click **🤖 Evaluate Stadium Conditions**. A breach + AI action plan appears. | "The key move: a deterministic Python layer checks thresholds *first*. On a normal tick it makes **zero** API calls. Only a real breach — like this Critical gate — engages Gemini to write the ops action. That's cost control by design." |
| **0:25–0:35** | Point at the structured alert (triggers, severity, recommended action). | "The model runs with a constrained-output schema, so it *cannot* return a malformed alert. What you see is guaranteed-shape JSON, rendered directly." |
| **0:35–0:50** | Switch to **Fan Mobile Assistant**. Set **🌐 language to Spanish**, ask *"where's the shortest food line?"* Answer comes back **in Spanish** + recommendation card + `⚡` chip. Expand **🧠 Why this answer?**. | "Same brain, fan-facing — and this is where the AI is doing what rules can't. It answers in the fan's own language with the right register, and it *shows its reasoning*: which queues it compared and the trade-off it weighed. That's the explainability we were asked for." |
| **0:50–0:60** | **The kicker — evaluator data:** in the sidebar, upload `data/samples/stadium_snapshot.csv` (your own real data). The whole dashboard + assistant re-render on it; run the ops check — it trips Critical on the uploaded gate. | "And it's not locked to our simulator. Upload *your* telemetry — messy columns, `31 mins`, a security row — and the entire app runs on it. This is the app working on real data, live, right now." |

## Reproducible runs (optional but recommended)

The simulator accepts a seed so your demo is identical every rehearsal and every take:

```powershell
python -m simulator.data_simulator --stadium metlife --seed 42 --duration 30
```

> Tip: if you want a *guaranteed* Critical breach on stage, pre-seed and step the
> simulator until a gate hits Critical, note the seed, and reuse it live.

## If something goes wrong

- **No `⚡` chip / "Offline" badge unexpectedly** → key not picked up. Check env var / `st.secrets`.
- **Quota or network error mid-demo** → *lean into it*: "and there's the fallback doing its job." It's a feature, not a failure.
- **Nothing breaches for the ops demo** → click Refresh a few times, or use a known seed (above).
