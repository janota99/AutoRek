"""Presentation helpers for the Sales Tax page (Transaction Preparation & Review).

Markup only: a title block, a progress stepper, an upload-card heading, and KPI tiles. Nothing here reads a
file or changes a number; the pages pass in values they already computed. Styles are in
shared/styles/pages/sales-tax.css (the ``st-*`` classes).
"""
from __future__ import annotations

import html

import streamlit as st

_E = html.escape


def icon(name: str) -> str:
    """A Material Symbols glyph (Streamlit loads the font; base/chrome.css defines the class)."""
    return f'<span class="material-symbols-rounded" aria-hidden="true">{_E(name)}</span>'


def page_header(title: str, subtitle: str, eyebrow: str = "") -> None:
    tag = f'<span class="st-eyebrow">{_E(eyebrow)}</span>' if eyebrow else ""
    st.html(f'<div class="st-title">{tag}<h1>{_E(title)}</h1><p>{_E(subtitle)}</p></div>')


def stepper_html(steps: list[tuple[str, str]], current: int) -> str:
    """Steps are (title, detail). ``current`` is the 0-based active step; earlier ones show as complete."""
    items = ""
    for n, (title, detail) in enumerate(steps):
        state = "complete" if n < current else "active" if n == current else "todo"
        node = icon("check") if state == "complete" else str(n + 1)
        items += (f'<li class="st-step" data-state="{state}"><span class="st-node">{node}</span>'
                  f'<span class="st-step-title">{_E(title)}</span><span class="st-step-detail">{_E(detail)}</span></li>')
    return f'<ol class="st-steps" aria-label="Progress">{items}</ol>'


def section(title: str, subtitle: str = "") -> None:
    sub = f"<p>{_E(subtitle)}</p>" if subtitle else ""
    st.html(f'<div class="st-section"><h2>{_E(title)}</h2>{sub}</div>')


def drop_head(number: int, title: str, hint: str, glyph: str, pill_html: str, required: bool = True) -> str:
    """Heading block above one upload: numbered icon badge, title, status pill, and what the file should be."""
    req = '<span class="st-req">Required</span>' if required else '<span class="st-req st-opt">Optional</span>'
    return (
        f'<div class="st-drop-head"><span class="st-badge">{icon(glyph)}</span>'
        f'<div class="st-drop-copy"><div class="st-drop-title"><span class="st-num">{number}</span>{_E(title)} {pill_html} {req}</div>'
        f'<div class="st-drop-sub">{_E(hint)}</div></div></div>'
    )


def kpi_row(items: list[tuple[str, str, str]]) -> str:
    """KPI tiles: (label, already formatted value, tone) with tone one of neutral, remove, keep, good, warn, bad."""
    tiles = "".join(
        f'<div class="st-kpi" data-tone="{tone}"><span class="st-kpi-l">{_E(label)}</span>'
        f'<span class="st-kpi-v">{_E(value)}</span></div>' for label, value, tone in items)
    return f'<div class="st-kpis">{tiles}</div>'
