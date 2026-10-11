"""Workflow state shared by every tool: where you are, what blocks the next step, what changed.

Each tool already knows these answers (which files are loaded, which control failed, which input
changed); this module only says them the same way everywhere, so an accountant learns one layout:

    step strip      -> where am I in upload > validate > review > export
    status panel    -> what, specifically, is blocking me (or "ready")
    change panel    -> what changed since the last run, so nothing moves silently
    headline        -> one number and one sentence; the detail sits behind an expander

Display only: nothing here decides a status or computes an accounting figure. Callers pass in values
they already hold; ``changes_since`` only compares two dictionaries of such values. Styles are
``.pp-wf-*`` in shared/styles/base/workflow.css.
"""
from __future__ import annotations

import html
from typing import Any, Callable, Mapping, Optional, Sequence

_E = html.escape

STATE_COMPLETE, STATE_ACTIVE, STATE_TODO = "complete", "active", "todo"


# --- pure logic (unit tested) --------------------------------------------------------------------

def step_states(steps: int, current: int) -> list[str]:
    """Earlier steps are complete, ``current`` is active, later steps are still to do."""
    return [STATE_COMPLETE if n < current else STATE_ACTIVE if n == current else STATE_TODO
            for n in range(steps)]


def changed_inputs(previous: Mapping[str, Any], current: Mapping[str, Any]) -> list[str]:
    """Labels of inputs whose value differs between two snapshots, in the order ``current`` lists them.

    An input present on only one side counts as changed (a file was added or removed).
    """
    changed = [label for label, value in current.items() if label not in previous or previous[label] != value]
    changed += [label for label in previous if label not in current]
    return changed


def _plain(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:,.2f}"
    if isinstance(value, int) and not isinstance(value, bool):
        return f"{value:,}"
    return str(value)


def changes_since(previous: Mapping[str, Any], current: Mapping[str, Any], labels: Mapping[str, str],
                  formatters: Optional[Mapping[str, Callable[[Any], str]]] = None,
                  cent: float = 0.005) -> list[str]:
    """One line per figure that moved between two runs, e.g. ``Rows kept: 1,203 -> 1,210 (+7)``.

    ``labels`` picks and orders the figures. Numbers closer than half a cent are treated as equal, so
    float noise never shows up as a change; non-numbers are compared as-is. A figure missing from
    ``previous`` is skipped: there is nothing to compare it with.
    """
    formatters = formatters or {}
    lines: list[str] = []
    for key, label in labels.items():
        if key not in previous or key not in current:
            continue
        before, after = previous[key], current[key]
        fmt = formatters.get(key, _plain)
        numeric = all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in (before, after))
        if numeric:
            if abs(after - before) < cent:
                continue
            delta = after - before
            sign = "+" if delta > 0 else "-"
            lines.append(f"{label}: {fmt(before)} → {fmt(after)} ({sign}{fmt(abs(delta))})")
        elif before != after:
            lines.append(f"{label}: {before} → {after}")
    return lines


# --- markup --------------------------------------------------------------------------------------

def step_strip_html(steps: Sequence[tuple[str, str]], current: int, label: str = "Progress") -> str:
    """The step strip. ``steps`` are (title, short status); ``current`` is the 0-based active step."""
    items = ""
    for n, ((title, detail), state) in enumerate(zip(steps, step_states(len(steps), current))):
        node = "✓" if state == STATE_COMPLETE else str(n + 1)
        aria = ' aria-current="step"' if state == STATE_ACTIVE else ""
        items += (f'<li class="pp-wf-step" data-state="{state}"{aria}><span class="pp-wf-node">{node}</span>'
                  f'<span class="pp-wf-copy"><span class="pp-wf-title">{_E(title)}</span>'
                  f'<span class="pp-wf-detail">{_E(detail)}</span></span></li>')
    return f'<ol class="pp-wf-steps" aria-label="{_E(label)}">{items}</ol>'


def status_html(blockers: Sequence[str], ready: str, *, tone: str = "blocked", next_step: str = "") -> str:
    """"What's blocking me". With blockers it lists each one; without, it says what is ready.

    ``tone`` is "blocked" (amber: fix these to continue) or "fail" (red: a control failed). An empty
    ``blockers`` always renders the ready state, whatever ``tone`` was passed.
    """
    if not blockers:
        nxt = f'<span class="pp-wf-next">{_E(next_step)}</span>' if next_step else ""
        return (f'<div class="pp-wf-status" data-tone="ready" role="status"><span class="pp-wf-icon" aria-hidden="true">'
                f'✓</span><div><strong>{_E(ready)}</strong>{nxt}</div></div>')
    tone = tone if tone in ("blocked", "fail") else "blocked"
    head = "Blocking the next step" if len(blockers) == 1 else f"{len(blockers)} things are blocking the next step"
    items = "".join(f"<li>{_E(b)}</li>" for b in blockers)
    return (f'<div class="pp-wf-status" data-tone="{tone}" role="status"><span class="pp-wf-icon" aria-hidden="true">'
            f'!</span><div><strong>{head}</strong><ul>{items}</ul></div></div>')


def changes_html(lines: Sequence[str], title: str = "Since the last run") -> str:
    """The change panel. Empty when there is nothing to report, so callers can skip drawing it."""
    if not lines:
        return ""
    items = "".join(f"<li>{_E(line)}</li>" for line in lines)
    return (f'<details class="pp-wf-changes" open><summary>{_E(title)}: {len(lines)} '
            f'change{"s" if len(lines) != 1 else ""}</summary><ul>{items}</ul></details>')


def headline_html(value: str, label: str, sentence: str = "", tone: str = "neutral") -> str:
    """One headline figure with its label and a single sentence. ``tone``: neutral, good, warn, bad."""
    tone = tone if tone in ("neutral", "good", "warn", "bad") else "neutral"
    text = f'<p class="pp-wf-sentence">{_E(sentence)}</p>' if sentence else ""
    return (f'<div class="pp-wf-headline" data-tone="{tone}"><span class="pp-wf-value">{_E(value)}</span>'
            f'<div><span class="pp-wf-label">{_E(label)}</span>{text}</div></div>')


def workflow_html(steps: Sequence[tuple[str, str]], current: int, blockers: Sequence[str], ready: str, *,
                  tone: str = "blocked", next_step: str = "", changes: Sequence[str] = (),
                  changes_title: str = "Since the last run", label: str = "Progress") -> str:
    """Strip, status and changes as one block, so a tool can draw all three into a single placeholder."""
    return (step_strip_html(steps, current, label) + status_html(blockers, ready, tone=tone, next_step=next_step)
            + changes_html(changes, changes_title))
