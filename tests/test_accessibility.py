"""Accessibility (WCAG) regression tests for the presentation layer.

These pin the specific a11y guarantees added to the theme so they can't silently
regress: real heading tags, an accessible SVG map, an OS-reduced-motion guard,
visible keyboard focus, a labelled live-status pill, and correct language/dir
tagging of non-English answers (WCAG 1.3.1, 2.3.3, 2.4.7, 3.1.2, 4.1.2).
"""

from simulator.config import language_bcp47
from ui import theme as T


def test_topbar_emits_real_heading():
    """The title must be a genuine <h1> (heading landmark), not a styled div."""
    html = T.topbar(eyebrow="Ops", title="Command Center", subtitle="sub", heading_level=1)
    assert '<h1 class="fl-title">Command Center</h1>' in html
    # Eyebrow/subtitle are paragraphs, and the decorative crest is hidden from AT.
    assert '<p class="fl-eyebrow">' in html
    assert 'aria-hidden="true"' in html


def test_topbar_heading_level_configurable():
    html = T.topbar(eyebrow="x", title="Fan Guide", subtitle="s", heading_level=2)
    assert '<h2 class="fl-title">Fan Guide</h2>' in html


def test_live_pill_has_status_role_and_label():
    """The LIVE/OFFLINE state must be exposed to AT, not conveyed by colour alone."""
    html = T.topbar(eyebrow="x", title="y", subtitle="z", live=False, live_label="OFFLINE")
    assert 'role="status"' in html
    assert 'aria-label="Data status: OFFLINE"' in html


def test_theme_has_reduced_motion_guard():
    """A prefers-reduced-motion block must neutralise decorative animation."""
    css = T.inject_theme()
    assert "prefers-reduced-motion: reduce" in css


def test_theme_has_visible_focus_style():
    """A visible keyboard focus indicator must exist (WCAG 2.4.7)."""
    css = T.inject_theme()
    assert ":focus-visible" in css
    assert "outline" in css


def test_stadium_map_is_accessible_image():
    """The SVG must be a labelled role=img with title+desc for screen readers."""
    nodes = [{"id": "Gate A", "x": 50, "y": 12, "color": "#34D399", "label": "20% Low", "step_free": True}]
    svg = T.stadium_map(gate_nodes=nodes, recommended_gate="Gate A", aria_summary="Use Gate A.")
    assert 'role="img"' in svg
    assert 'aria-label="Use Gate A."' in svg
    assert "<title>" in svg and "<desc>" in svg


def test_localized_text_tags_language_and_direction():
    """Non-English answers carry lang + dir so AT pronounces/lays them out right."""
    lang, direction = language_bcp47("Arabic")
    assert (lang, direction) == ("ar", "rtl")
    html = T.localized_text("مرحبا", lang=lang, direction=direction)
    assert 'lang="ar"' in html and 'dir="rtl"' in html


def test_localized_text_escapes_and_preserves_newlines():
    html = T.localized_text("a < b\nnext", lang="en", direction="ltr")
    assert "&lt;" in html  # escaped
    assert "<br>" in html  # newline preserved


def test_language_bcp47_defaults_to_english():
    assert language_bcp47("Klingon") == ("en", "ltr")


def test_all_supported_languages_have_bcp47_mapping():
    """Every selectable language must resolve to a real (non-fallback) tag."""
    from simulator.config import SUPPORTED_LANGUAGES

    for lang in SUPPORTED_LANGUAGES:
        tag, direction = language_bcp47(lang)
        assert tag and direction in ("ltr", "rtl")
        # English is the only one allowed to map to "en"; others must differ.
        if lang != "English":
            assert tag != "en", f"{lang} fell back to English"
