"""Floodlit — the shared design system for the Smart Stadium app.

A single source of truth for the app's look and feel so the fan and ops views
read as one product. The aesthetic direction is *broadcast-grade matchday*: a
deep "stadium at dusk" ink base, layered floodlight glows, an electric-sky brand
accent reserved for actions, and a strict green→amber→orange→rose severity ramp
that carries *meaning* in the data (never decoration).

The module exposes two kinds of thing:

* :func:`inject_theme` — one CSS payload injected once per page load. It styles
  both Streamlit's own widgets (so the native selectbox / radio / chat input
  belong) and the custom component classes below.
* HTML component builders (``topbar``, ``kpi_tile``, ``meter_row`` …) that return
  markup strings. Views compose these and hand them to ``st.markdown(...,
  unsafe_allow_html=True)``. Builders emit **left-aligned** HTML on purpose:
  Streamlit's Markdown parser turns 4-space-indented lines into code blocks, so
  indentation is deliberately avoided inside returned markup.

Nothing here touches session state or the data pipeline — it is pure
presentation, safe to restyle without risking app behaviour.
"""

from __future__ import annotations

import html as _html
from collections.abc import Iterable, Sequence

from simulator.config import STADIUM_CENTER

# ---------------------------------------------------------------------------
# Design tokens
# ---------------------------------------------------------------------------
# Kept in Python as well as CSS so component builders can pick a colour for an
# inline style (e.g. a meter fill) without duplicating hex literals.

INK = "#0A0F16"
BRAND = "#22D3EE"  # electric cyan — actions & brand only
BRAND_2 = "#38BDF8"  # sky — gradient partner

# Severity ramp — the ONLY colours that encode data state.
SEV_OK = "#34D399"  # emerald  · Low / Green / normal
SEV_WARN = "#FBBF24"  # amber    · Moderate / Yellow / watch
SEV_HIGH = "#FB923C"  # orange   · High / Orange / elevated
SEV_CRIT = "#FB3B53"  # rose     · Critical / Red / breach

TEXT = "#E8EEF6"
MUTED = "#8A9BB4"


def density_color(status: str) -> str:
    """Return the ramp colour for a gate crowd-density status label."""
    return {
        "Low": SEV_OK,
        "Moderate": SEV_WARN,
        "High": SEV_HIGH,
        "Critical": SEV_CRIT,
    }.get(status, MUTED)


def security_color(level: str) -> str:
    """Return the ramp colour for a security alert level."""
    return {
        "Green": SEV_OK,
        "Yellow": SEV_WARN,
        "Orange": SEV_HIGH,
        "Red": SEV_CRIT,
    }.get(level, MUTED)


def wait_color(minutes: float) -> str:
    """Return the ramp colour for a concession wait time (25 min = breach)."""
    if minutes >= 25:
        return SEV_CRIT
    if minutes >= 15:
        return SEV_HIGH
    if minutes >= 8:
        return SEV_WARN
    return SEV_OK


def _confidence_ramp(confidence: str) -> str:
    """Map a high/medium/low confidence label to a ramp colour for its chip."""
    return {
        "high": SEV_OK,
        "medium": SEV_WARN,
        "low": SEV_HIGH,
    }.get(str(confidence).lower(), MUTED)


def _esc(value: object) -> str:
    """HTML-escape any value for safe interpolation into markup."""
    return _html.escape(str(value), quote=True)


# ---------------------------------------------------------------------------
# Global CSS
# ---------------------------------------------------------------------------


def inject_theme() -> str:
    """Return the full CSS payload for the app.

    Callers pass the result to ``st.markdown(inject_theme(),
    unsafe_allow_html=True)`` exactly once, right after ``set_page_config``.
    """
    return """
<style>
@import url('https://fonts.googleapis.com/css2?family=Anton&family=Hanken+Grotesk:wght@400;500;600;700;800&family=Space+Mono:wght@400;700&display=swap');

:root {
  --ink: #0A0F16;
  --panel: #111A28;
  --panel-2: #0E1622;
  --line: #1E2A3C;
  --brand: #22D3EE;
  --brand-2: #38BDF8;
  --ok: #34D399;
  --warn: #FBBF24;
  --high: #FB923C;
  --crit: #FB3B53;
  --text: #E8EEF6;
  --muted: #8A9BB4;
}

/* --- Base canvas: stadium at dusk with layered floodlight glows --- */
.stApp {
  background:
    radial-gradient(1100px 620px at 78% -8%, rgba(56,189,248,0.14), transparent 60%),
    radial-gradient(900px 560px at 8% 0%, rgba(34,211,238,0.10), transparent 55%),
    radial-gradient(1200px 800px at 50% 118%, rgba(251,59,83,0.06), transparent 60%),
    linear-gradient(180deg, #0A0F16 0%, #0B1119 55%, #080C12 100%);
  background-attachment: fixed;
  color: var(--text);
  font-family: 'Hanken Grotesk', -apple-system, sans-serif;
}

/* Tighten the default top padding so the broadcast bar sits near the top. */
.block-container { padding-top: 2.2rem; max-width: 1220px; }

h1, h2, h3, h4 { font-family: 'Hanken Grotesk', sans-serif; letter-spacing: -0.01em; }

/* --- Sidebar as a control rail --- */
section[data-testid="stSidebar"] {
  background: linear-gradient(180deg, #0D1420 0%, #0A111B 100%);
  border-right: 1px solid var(--line);
}
section[data-testid="stSidebar"] .stButton>button { width: 100%; }

/* --- Buttons: brand gradient reserved for actions --- */
.stButton>button {
  background: linear-gradient(135deg, var(--brand-2), var(--brand));
  color: #04222B;
  border: none;
  border-radius: 10px;
  font-weight: 700;
  letter-spacing: 0.01em;
  padding: 0.5rem 1rem;
  transition: transform .15s ease, box-shadow .2s ease, filter .2s ease;
  box-shadow: 0 6px 18px rgba(34,211,238,0.18);
}
.stButton>button:hover {
  transform: translateY(-1px);
  filter: brightness(1.05);
  box-shadow: 0 10px 26px rgba(34,211,238,0.34);
  color: #04222B;
}
.stButton>button:active { transform: translateY(0); }

/* Native inputs harmonised with the panels. */
div[data-baseweb="select"] > div,
.stTextInput input, .stNumberInput input {
  background: var(--panel-2) !important;
  border-color: var(--line) !important;
  border-radius: 10px !important;
}
.stChatInput textarea, div[data-testid="stChatInput"] {
  border-radius: 12px !important;
}

/* Expander → a quiet card. */
.streamlit-expanderHeader, details summary {
  border-radius: 10px !important;
}
div[data-testid="stExpander"] {
  border: 1px solid var(--line);
  border-radius: 12px;
  background: rgba(17,26,40,0.55);
  overflow: hidden;
}

/* Chat bubbles inherit the panel look. */
div[data-testid="stChatMessage"] {
  background: rgba(17,26,40,0.60);
  border: 1px solid var(--line);
  border-radius: 14px;
}

/* =====================  CUSTOM COMPONENTS  ===================== */

/* Broadcast top bar */
.fl-topbar {
  display: flex; align-items: center; justify-content: space-between;
  gap: 1rem; flex-wrap: wrap;
  padding: 1.05rem 1.35rem;
  border: 1px solid var(--line);
  border-radius: 18px;
  background:
    radial-gradient(600px 200px at 100% -40%, rgba(56,189,248,0.16), transparent 70%),
    linear-gradient(180deg, rgba(20,30,46,0.92), rgba(12,19,29,0.92));
  box-shadow: 0 18px 40px rgba(0,0,0,0.35);
  margin-bottom: 1.15rem;
}
.fl-topbar-left { display: flex; align-items: center; gap: 0.95rem; }
.fl-crest {
  width: 46px; height: 46px; border-radius: 13px; flex: 0 0 auto;
  display: grid; place-items: center; font-size: 1.5rem;
  background: linear-gradient(135deg, var(--brand-2), var(--brand));
  box-shadow: 0 6px 18px rgba(34,211,238,0.3);
}
.fl-eyebrow {
  font-family: 'Space Mono', monospace; font-size: 0.66rem;
  letter-spacing: 0.28em; text-transform: uppercase; color: var(--brand);
  margin: 0 0 0.15rem 0;
}
.fl-title {
  font-family: 'Anton', sans-serif; font-weight: 400;
  font-size: 1.72rem; line-height: 1; letter-spacing: 0.01em;
  text-transform: uppercase; color: var(--text); margin: 0;
}
.fl-sub { color: var(--muted); font-size: 0.86rem; margin: 0.28rem 0 0 0; }

/* LIVE pill with pulsing dot */
.fl-live {
  display: inline-flex; align-items: center; gap: 0.5rem;
  font-family: 'Space Mono', monospace; font-size: 0.74rem; font-weight: 700;
  letter-spacing: 0.18em; text-transform: uppercase;
  padding: 0.42rem 0.8rem; border-radius: 999px;
  border: 1px solid rgba(52,211,153,0.4);
  background: rgba(52,211,153,0.10); color: var(--ok);
}
.fl-live .dot {
  width: 9px; height: 9px; border-radius: 50%; background: var(--ok);
  box-shadow: 0 0 0 0 rgba(52,211,153,0.7);
  animation: fl-pulse 1.8s infinite;
}
.fl-live.paused { border-color: rgba(138,155,180,0.35); background: rgba(138,155,180,0.08); color: var(--muted); }
.fl-live.paused .dot { background: var(--muted); animation: none; box-shadow: none; }
@keyframes fl-pulse {
  0%   { box-shadow: 0 0 0 0 rgba(52,211,153,0.55); }
  70%  { box-shadow: 0 0 0 9px rgba(52,211,153,0); }
  100% { box-shadow: 0 0 0 0 rgba(52,211,153,0); }
}

/* Match phase strip */
.fl-phases { display: flex; gap: 0.5rem; margin: 0 0 1.25rem 0; flex-wrap: wrap; }
.fl-phase {
  flex: 1 1 0; min-width: 110px;
  padding: 0.6rem 0.8rem; border-radius: 12px;
  border: 1px solid var(--line); background: rgba(14,22,34,0.6);
  display: flex; flex-direction: column; gap: 0.15rem;
  position: relative; overflow: hidden;
}
.fl-phase .k {
  font-family: 'Space Mono', monospace; font-size: 0.6rem;
  letter-spacing: 0.2em; text-transform: uppercase; color: var(--muted);
}
.fl-phase .v { font-weight: 700; font-size: 0.95rem; color: var(--text); }
.fl-phase.done { opacity: 0.55; }
.fl-phase.active {
  border-color: rgba(34,211,238,0.55);
  background: linear-gradient(180deg, rgba(34,211,238,0.16), rgba(34,211,238,0.05));
  box-shadow: inset 0 0 0 1px rgba(34,211,238,0.25), 0 8px 22px rgba(34,211,238,0.12);
}
.fl-phase.active .k { color: var(--brand); }

/* Section label */
.fl-section {
  display: flex; align-items: baseline; gap: 0.6rem; margin: 0.4rem 0 0.85rem 0;
}
.fl-section h3 {
  font-family: 'Anton', sans-serif; font-weight: 400; text-transform: uppercase;
  letter-spacing: 0.02em; font-size: 1.18rem; margin: 0; color: var(--text);
}
.fl-section .hint { color: var(--muted); font-size: 0.82rem; }

/* KPI tiles */
.fl-grid { display: grid; gap: 0.8rem; margin-bottom: 0.4rem; }
.fl-grid.c4 { grid-template-columns: repeat(4, 1fr); }
.fl-grid.c3 { grid-template-columns: repeat(3, 1fr); }
.fl-grid.c2 { grid-template-columns: repeat(2, 1fr); }
@media (max-width: 900px) { .fl-grid.c4, .fl-grid.c3 { grid-template-columns: repeat(2, 1fr); } }

.fl-tile {
  padding: 0.95rem 1.05rem; border-radius: 16px;
  border: 1px solid var(--line);
  background: linear-gradient(180deg, rgba(20,30,46,0.72), rgba(12,19,29,0.72));
  position: relative; overflow: hidden;
}
.fl-tile::before {
  content: ""; position: absolute; left: 0; top: 0; bottom: 0; width: 4px;
  background: var(--accent, var(--brand));
}
.fl-tile .cap {
  font-family: 'Space Mono', monospace; font-size: 0.62rem;
  letter-spacing: 0.16em; text-transform: uppercase; color: var(--muted);
  display: flex; align-items: center; gap: 0.4rem;
}
.fl-tile .val {
  font-family: 'Anton', sans-serif; font-weight: 400; font-size: 2.05rem;
  line-height: 1.05; margin: 0.4rem 0 0.15rem 0; color: var(--text);
}
.fl-tile .val .unit { font-size: 1.05rem; color: var(--muted); margin-left: 0.15rem; }
.fl-tile .foot { font-size: 0.8rem; color: var(--muted); }
.fl-tile .foot b { color: var(--accent, var(--text)); font-weight: 700; }

/* Meter rows (horizontal, with threshold tick) */
.fl-panel {
  padding: 1.05rem 1.15rem 0.6rem; border-radius: 16px;
  border: 1px solid var(--line);
  background: linear-gradient(180deg, rgba(18,27,41,0.66), rgba(11,18,28,0.66));
}
.fl-meter { margin-bottom: 0.95rem; }
.fl-meter:last-child { margin-bottom: 0.3rem; }
.fl-meter-top {
  display: flex; align-items: baseline; justify-content: space-between; gap: 0.6rem;
  margin-bottom: 0.4rem;
}
.fl-meter-name { font-weight: 700; font-size: 0.94rem; color: var(--text); }
.fl-meter-name .sub { color: var(--muted); font-weight: 500; font-size: 0.78rem; margin-left: 0.5rem; }
.fl-meter-val {
  font-family: 'Space Mono', monospace; font-weight: 700; font-size: 0.96rem;
}
.fl-track {
  position: relative; height: 12px; border-radius: 999px;
  background: rgba(255,255,255,0.06); overflow: hidden;
}
.fl-fill {
  position: absolute; left: 0; top: 0; bottom: 0; border-radius: 999px;
  background: var(--c); box-shadow: 0 0 14px -2px var(--c);
  transition: width .5s ease;
}
.fl-tick {
  position: absolute; top: -3px; bottom: -3px; width: 2px;
  background: rgba(232,238,246,0.55);
}
.fl-tick::after {
  content: attr(data-label); position: absolute; top: -1.15rem; left: 50%;
  transform: translateX(-50%); white-space: nowrap;
  font-family: 'Space Mono', monospace; font-size: 0.56rem; color: var(--muted);
}

/* Badge / chip */
.fl-badge {
  display: inline-flex; align-items: center; gap: 0.35rem;
  font-family: 'Space Mono', monospace; font-size: 0.62rem; font-weight: 700;
  letter-spacing: 0.12em; text-transform: uppercase;
  padding: 0.22rem 0.55rem; border-radius: 999px;
  border: 1px solid var(--c); color: var(--c);
  background: color-mix(in srgb, var(--c) 14%, transparent);
}
.fl-chips { display: flex; flex-wrap: wrap; gap: 0.4rem; }
.fl-chip {
  font-family: 'Space Mono', monospace; font-size: 0.68rem; color: var(--muted);
  border: 1px solid var(--line); border-radius: 999px; padding: 0.24rem 0.6rem;
  background: rgba(255,255,255,0.02);
}
.fl-chip b { color: var(--text); font-weight: 700; }

/* Recommendation card (fan) */
.fl-rec {
  margin-top: 0.7rem; padding: 0.85rem 1rem; border-radius: 14px;
  border: 1px solid rgba(34,211,238,0.35);
  background: linear-gradient(135deg, rgba(34,211,238,0.12), rgba(56,189,248,0.05));
  display: flex; gap: 0.75rem; align-items: flex-start;
}
.fl-rec .ic { font-size: 1.35rem; line-height: 1; }
.fl-rec .lab {
  font-family: 'Space Mono', monospace; font-size: 0.6rem; letter-spacing: 0.16em;
  text-transform: uppercase; color: var(--brand); margin-bottom: 0.15rem;
}
.fl-rec .nm { font-weight: 800; font-size: 1rem; color: var(--text); }
.fl-rec .rs { color: var(--muted); font-size: 0.85rem; margin-top: 0.1rem; }

/* Alert banner (ops) */
.fl-alert {
  padding: 0.95rem 1.1rem; border-radius: 14px; margin-top: 0.4rem;
  border: 1px solid var(--c);
  background: color-mix(in srgb, var(--c) 12%, transparent);
}
.fl-alert .hd {
  display: flex; align-items: center; gap: 0.55rem;
  font-family: 'Anton', sans-serif; font-weight: 400; text-transform: uppercase;
  letter-spacing: 0.03em; font-size: 1.05rem; color: var(--c);
}

/* Empty / hero state */
.fl-empty {
  text-align: center; padding: 2.6rem 1.5rem; border-radius: 18px;
  border: 1px dashed var(--line); background: rgba(14,22,34,0.5);
}
.fl-empty .em { font-size: 2.6rem; }
.fl-empty h3 {
  font-family: 'Anton', sans-serif; font-weight: 400; text-transform: uppercase;
  margin: 0.5rem 0 0.3rem; color: var(--text);
}
.fl-empty p { color: var(--muted); margin: 0; }

/* Meta caption row for chat answers */
.fl-meta { display: flex; flex-wrap: wrap; gap: 0.4rem; margin-top: 0.55rem; }

/* Schematic stadium map (navigation view) */
.fl-map {
  border: 1px solid var(--line); border-radius: 16px;
  background:
    radial-gradient(420px 220px at 50% -10%, rgba(56,189,248,0.10), transparent 65%),
    linear-gradient(180deg, rgba(16,25,38,0.72), rgba(10,16,25,0.72));
  padding: 0.6rem; overflow: hidden;
}
.fl-map svg { width: 100%; height: auto; display: block; }
/* Legend row under / beside the map */
.fl-legend { display: flex; flex-wrap: wrap; gap: 0.5rem 0.9rem; margin: 0.6rem 0.2rem 0; }
.fl-legend .it {
  display: inline-flex; align-items: center; gap: 0.4rem;
  font-size: 0.72rem; color: var(--muted);
  font-family: 'Space Mono', monospace;
}
.fl-legend .sw { width: 10px; height: 10px; border-radius: 50%; flex: 0 0 auto; }
.fl-legend .ring { box-shadow: 0 0 0 2px var(--brand); background: transparent; }

/* Hide Streamlit chrome we don't need for a cleaner canvas. */
#MainMenu { visibility: hidden; }
footer { visibility: hidden; }

/* --- Accessibility: visible keyboard focus (WCAG 2.4.7 Focus Visible) --- */
/* A high-contrast focus ring on every interactive element, including the
   custom-styled button and native widgets, so keyboard users can always see
   where they are. :focus-visible avoids showing it on mouse clicks. */
.stButton>button:focus-visible,
a:focus-visible,
summary:focus-visible,
[role="button"]:focus-visible,
div[data-baseweb="select"] > div:focus-within,
.stTextInput input:focus-visible,
.stChatInput textarea:focus-visible,
input:focus-visible,
[tabindex]:focus-visible {
  outline: 3px solid var(--brand) !important;
  outline-offset: 2px !important;
  box-shadow: 0 0 0 4px rgba(34,211,238,0.35) !important;
}

/* --- Accessibility: honour reduced-motion (WCAG 2.3.3 / 2.2.2) --- */
/* Users who ask their OS to reduce motion get no pulsing dot, no animated map
   ring, and no hover translate — all decorative motion is neutralised. */
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: 0.001ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: 0.001ms !important;
    scroll-behavior: auto !important;
  }
  .fl-live .dot { animation: none !important; box-shadow: none !important; }
  .stButton>button:hover { transform: none !important; }
}
</style>
"""


# ---------------------------------------------------------------------------
# Component builders
# ---------------------------------------------------------------------------

_PHASE_ORDER: list[tuple] = [
    ("pre-match", "01", "Pre-Match"),
    ("live", "02", "Live"),
    ("halftime", "03", "Half-Time"),
    ("post-match", "04", "Full-Time"),
]

_PHASE_ALIASES = {
    "pre-match": "pre-match",
    "prematch": "pre-match",
    "pre": "pre-match",
    "live": "live",
    "in-play": "live",
    "1st half": "live",
    "2nd half": "live",
    "halftime": "halftime",
    "half-time": "halftime",
    "ht": "halftime",
    "post-match": "post-match",
    "postmatch": "post-match",
    "full-time": "post-match",
    "ft": "post-match",
}


def topbar(
    *,
    eyebrow: str,
    title: str,
    subtitle: str,
    crest: str = "⚽",
    live: bool = True,
    live_label: str = "LIVE",
    heading_level: int = 1,
) -> str:
    """Return the broadcast top-bar for a view header.

    The title is emitted as a real ``<h1>``/``<h2>`` (per ``heading_level``) so
    assistive tech gets a proper document heading, not a styled ``<div>``
    (WCAG 1.3.1 Info & Relationships / 2.4.6 Headings & Labels). The decorative
    crest is ``aria-hidden`` and the LIVE pill exposes its state via ``role`` +
    ``aria-label`` rather than colour/animation alone.
    """
    live_cls = "fl-live" if live else "fl-live paused"
    htag = f"h{heading_level}" if heading_level in (1, 2, 3) else "h1"
    return (
        '<div class="fl-topbar">'
        '<div class="fl-topbar-left">'
        f'<div class="fl-crest" aria-hidden="true">{_esc(crest)}</div>'
        "<div>"
        f'<p class="fl-eyebrow">{_esc(eyebrow)}</p>'
        f'<{htag} class="fl-title">{_esc(title)}</{htag}>'
        f'<p class="fl-sub">{_esc(subtitle)}</p>'
        "</div></div>"
        f'<div class="{live_cls}" role="status" aria-label="Data status: {_esc(live_label)}">'
        '<span class="dot" aria-hidden="true"></span>'
        f"{_esc(live_label)}</div>"
        "</div>"
    )


def match_phase_strip(match_status: str | None) -> str:
    """Return the four-stage match-phase progress strip.

    Highlights the active phase, dims the ones already passed. ``match_status``
    is normalised through a small alias table so both simulator values
    (``pre-match``, ``live`` …) and looser upload values read correctly.
    """
    current = _PHASE_ALIASES.get(str(match_status or "").strip().lower())
    active_idx = next(
        (i for i, (key, _, _) in enumerate(_PHASE_ORDER) if key == current),
        -1,
    )
    cells: list[str] = []
    for i, (_, num, label) in enumerate(_PHASE_ORDER):
        if active_idx == -1:
            cls = "fl-phase"
        elif i < active_idx:
            cls = "fl-phase done"
        elif i == active_idx:
            cls = "fl-phase active"
        else:
            cls = "fl-phase"
        cells.append(f'<div class="{cls}"><span class="k">Phase {num}</span><span class="v">{_esc(label)}</span></div>')
    return f'<div class="fl-phases">{"".join(cells)}</div>'


def section(title: str, hint: str = "") -> str:
    """Return a section header (condensed label + optional hint)."""
    hint_html = f'<span class="hint">{_esc(hint)}</span>' if hint else ""
    return f'<div class="fl-section"><h3>{_esc(title)}</h3>{hint_html}</div>'


def kpi_tile(*, caption: str, value: str, unit: str = "", foot: str = "", color: str = BRAND) -> str:
    """Return a single KPI tile. Compose several inside :func:`grid`."""
    unit_html = f'<span class="unit">{_esc(unit)}</span>' if unit else ""
    foot_html = f'<div class="foot">{foot}</div>' if foot else ""
    return (
        f'<div class="fl-tile" style="--accent:{color}">'
        f'<div class="cap">{_esc(caption)}</div>'
        f'<div class="val">{_esc(value)}{unit_html}</div>'
        f"{foot_html}</div>"
    )


def grid(tiles: Sequence[str], cols: int = 4) -> str:
    """Wrap tile/markup fragments in a responsive grid (2, 3 or 4 columns)."""
    cls = {2: "c2", 3: "c3", 4: "c4"}.get(cols, "c4")
    return f'<div class="fl-grid {cls}">{"".join(tiles)}</div>'


def badge(text: str, color: str) -> str:
    """Return a small status badge in the given ramp colour."""
    return f'<span class="fl-badge" style="--c:{color}">{_esc(text)}</span>'


def meter_row(
    *,
    name: str,
    sub: str,
    value_text: str,
    pct: float,
    color: str,
    threshold_pct: float | None = None,
    threshold_label: str = "",
) -> str:
    """Return one horizontal meter with optional threshold tick.

    Args:
        name: Primary label (e.g. gate or stand name).
        sub: Secondary muted label (e.g. entries / queue length).
        value_text: The right-aligned readout (already unit-formatted).
        pct: Fill width as a 0–100 percentage.
        color: Ramp colour for fill + readout.
        threshold_pct: Optional 0–100 position for the threshold tick.
        threshold_label: Small caption printed above the tick.
    """
    fill = max(0.0, min(100.0, float(pct)))
    tick = ""
    if threshold_pct is not None:
        tpos = max(0.0, min(100.0, float(threshold_pct)))
        tick = f'<div class="fl-tick" style="left:{tpos:.1f}%" data-label="{_esc(threshold_label)}"></div>'
    return (
        '<div class="fl-meter">'
        '<div class="fl-meter-top">'
        f'<div class="fl-meter-name">{_esc(name)}<span class="sub">{_esc(sub)}</span></div>'
        f'<div class="fl-meter-val" style="color:{color}">{_esc(value_text)}</div>'
        "</div>"
        f'<div class="fl-track"><div class="fl-fill" style="width:{fill:.1f}%;--c:{color}"></div>{tick}</div>'
        "</div>"
    )


def panel(inner: str) -> str:
    """Wrap meter rows (or any markup) in a bordered panel card."""
    return f'<div class="fl-panel">{inner}</div>'


def recommendation_card(*, icon: str, label: str, name: str, reason: str) -> str:
    """Return the fan-facing recommendation callout card."""
    return (
        '<div class="fl-rec">'
        f'<div class="ic">{_esc(icon)}</div>'
        "<div>"
        f'<div class="lab">{_esc(label)}</div>'
        f'<div class="nm">{_esc(name)}</div>'
        f'<div class="rs">{_esc(reason)}</div>'
        "</div></div>"
    )


def chips(items: Iterable[str]) -> str:
    """Return a row of small meta chips. Each item may contain safe ``<b>``."""
    body = "".join(f'<span class="fl-chip">{c}</span>' for c in items)
    return f'<div class="fl-meta">{body}</div>'


def chip(text_html: str) -> str:
    """Return a single chip whose inner text is already-escaped HTML."""
    return f'<span class="fl-chip">{text_html}</span>'


def alert_banner(*, title: str, color: str, body_html: str) -> str:
    """Return an ops alert banner (header + arbitrary body markup)."""
    return f'<div class="fl-alert" style="--c:{color}"><div class="hd">{title}</div>{body_html}</div>'


def empty_state(*, emoji: str, title: str, text: str) -> str:
    """Return a centered empty / call-to-action state card."""
    return (
        '<div class="fl-empty">'
        f'<div class="em" aria-hidden="true">{_esc(emoji)}</div>'
        f"<h3>{_esc(title)}</h3><p>{_esc(text)}</p></div>"
    )


def localized_text(text: str, *, lang: str, direction: str) -> str:
    """Wrap assistant text in a ``<div>`` tagged with ``lang``/``dir``.

    Gives assistive tech the correct pronunciation for a non-English answer and
    lays RTL scripts (e.g. Arabic) out correctly (WCAG 3.1.2 Language of Parts).
    Markdown/HTML is escaped; newlines become ``<br>`` so multi-line answers keep
    their breaks when rendered with ``unsafe_allow_html``.
    """
    safe = _esc(text).replace("\n", "<br>")
    return f'<div lang="{_esc(lang)}" dir="{_esc(direction)}">{safe}</div>'


# ---------------------------------------------------------------------------
# Schematic stadium map (SVG) — for the navigation view
# ---------------------------------------------------------------------------
# A dependency-free inline SVG: no map tiles, no API key, no billing. Gate nodes
# are placed from the shared normalised (0–100) layout and coloured by the same
# severity ramp as the rest of the app, so the map reads as one system. The
# recommended entry is ringed, avoided gates get a muted ✕, and a dashed line
# traces the suggested walk to the destination. The whole figure carries an
# ARIA label + <title>/<desc> so a screen reader gets the same information.


def stadium_map(
    *,
    gate_nodes: Sequence[dict],
    destination_xy: tuple | None = None,
    destination_label: str = "Destination",
    recommended_gate: str = "",
    avoid_gates: Sequence[str] = (),
    aria_summary: str = "",
) -> str:
    """Return an inline SVG schematic of the stadium with gates + a route.

    Args:
        gate_nodes: dicts with ``id``, ``x``, ``y`` (0–100), ``color`` (hex),
            ``label`` (e.g. "62% High"), and optional ``step_free`` (bool).
        destination_xy: ``(x, y)`` of the destination marker, or None to omit it.
        destination_label: Text label for the destination marker.
        recommended_gate: id of the gate to ring as the recommended entry.
        avoid_gates: ids to mark with a muted ✕.
        aria_summary: Plain-language description for the SVG's accessible name.

    The 0–100 coordinate space is scaled into a 100×100 SVG ``viewBox`` (with a
    small pad), so callers never deal in pixels.
    """
    avoid_set = {str(a) for a in avoid_gates}
    canvas_w = canvas_h = 100.0

    # --- Field: the oval pitch in the centre. ---
    parts: list[str] = [
        '<ellipse cx="50" cy="50" rx="30" ry="22" '
        'fill="rgba(52,211,153,0.06)" stroke="rgba(232,238,246,0.18)" '
        'stroke-width="0.5"/>',
        '<line x1="50" y1="28" x2="50" y2="72" stroke="rgba(232,238,246,0.14)" stroke-width="0.4"/>',
        '<circle cx="50" cy="50" r="6" fill="none" stroke="rgba(232,238,246,0.14)" stroke-width="0.4"/>',
        # Concourse ring the gates sit on (dotted, decorative).
        '<ellipse cx="50" cy="50" rx="42" ry="38" fill="none" '
        'stroke="rgba(232,238,246,0.10)" stroke-width="0.4" stroke-dasharray="1.5 2"/>',
    ]

    # --- Route line: recommended gate → destination (drawn under the nodes). ---
    rec_node = next((g for g in gate_nodes if str(g.get("id")) == recommended_gate), None)
    if rec_node is not None and destination_xy is not None:
        dx, dy = float(destination_xy[0]), float(destination_xy[1])
        parts.append(
            f'<line x1="{float(rec_node["x"]):.1f}" y1="{float(rec_node["y"]):.1f}" '
            f'x2="{dx:.1f}" y2="{dy:.1f}" stroke="{BRAND}" stroke-width="0.9" '
            f'stroke-dasharray="2 1.6" opacity="0.9"/>'
        )

    # --- Destination marker. ---
    if destination_xy is not None:
        dx, dy = float(destination_xy[0]), float(destination_xy[1])
        parts.append(
            f'<circle cx="{dx:.1f}" cy="{dy:.1f}" r="2.4" fill="{BRAND}" stroke="#04222B" stroke-width="0.5"/>'
        )
        parts.append(
            f'<text x="{dx:.1f}" y="{dy - 3.4:.1f}" text-anchor="middle" '
            f'font-size="3" fill="{TEXT}" font-family="Space Mono, monospace">'
            f"{_esc(destination_label)}</text>"
        )

    # --- Gate nodes. ---
    for g in gate_nodes:
        gid = str(g.get("id", ""))
        x, y = float(g.get("x", 50)), float(g.get("y", 50))
        color = str(g.get("color", MUTED))
        label = str(g.get("label", ""))
        is_rec = gid == recommended_gate
        is_avoid = gid in avoid_set

        # Recommended gate gets an outer highlight ring.
        if is_rec:
            parts.append(
                f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4.6" fill="none" '
                f'stroke="{BRAND}" stroke-width="0.8"><animate '
                f'attributeName="r" values="4.6;5.4;4.6" dur="2s" '
                f'repeatCount="indefinite"/></circle>'
            )
        node_stroke = "#04222B"
        parts.append(
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.1" fill="{color}" '
            f'stroke="{node_stroke}" stroke-width="0.5" opacity='
            f'"{0.45 if is_avoid else 1}"/>'
        )
        # Step-free wheelchair glyph tint (a tiny ring) if applicable.
        if g.get("step_free"):
            parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="1.4" fill="none" stroke="#04222B" stroke-width="0.4"/>')
        # Avoided gate gets a muted ✕.
        if is_avoid:
            parts.append(
                f'<text x="{x:.1f}" y="{y + 1.1:.1f}" text-anchor="middle" '
                f'font-size="3.4" fill="#04222B" font-weight="700">✕</text>'
            )
        # Gate id + reading, placed outside the node (away from centre).
        cx, _cy = STADIUM_CENTER
        out = 6.2
        lx = x + (out if x >= cx else -out)
        anchor = "start" if x >= cx else "end"
        parts.append(
            f'<text x="{lx:.1f}" y="{y - 0.4:.1f}" text-anchor="{anchor}" '
            f'font-size="3.2" fill="{TEXT}" font-family="Hanken Grotesk, sans-serif" '
            f'font-weight="700">{_esc(gid)}</text>'
        )
        if label:
            parts.append(
                f'<text x="{lx:.1f}" y="{y + 3.0:.1f}" text-anchor="{anchor}" '
                f'font-size="2.6" fill="{MUTED}" font-family="Space Mono, monospace">'
                f"{_esc(label)}</text>"
            )

    body = "".join(parts)
    aria = _esc(aria_summary or "Schematic stadium map with gates coloured by crowd density.")
    return (
        '<div class="fl-map">'
        f'<svg viewBox="-8 -8 {canvas_w + 16:.0f} {canvas_h + 16:.0f}" role="img" '
        f'aria-label="{aria}" preserveAspectRatio="xMidYMid meet">'
        f"<title>Stadium navigation map</title><desc>{aria}</desc>"
        f"{body}</svg></div>"
    )
