
"""Shared workflow state and presentation components.

This module provides a consistent interface for displaying workflow progress,
validation results, changes between runs, and summary figures.

Every tool uses the same four presentation elements:

    Step strip:
        Shows progress through Upload → Validate → Review → Export.

    Status panel:
        Identifies blockers or confirms that processing can continue.

    Change panel:
        Highlights inputs and reported figures that changed between runs.

    Headline:
        Displays one key figure with a short explanatory sentence.

Design principles
-----------------
1. Presentation only:
   This module does not calculate accounting balances, validate transactions,
   or determine whether an accounting process has succeeded.

2. Explicit inputs:
   Calling tools supply the workflow status, figures, and messages.

3. Predictable output:
   Functions return HTML strings without modifying external state.

4. Safe presentation:
   Dynamic text is HTML-escaped before being inserted into markup.

5. Consistent styling:
   Components use the ``pp-wf-*`` CSS classes defined in:
   shared/styles/base/workflow.css

The comparison utilities can be unit tested independently of the HTML
presentation functions.
"""

from __future__ import annotations

import html
import math
from typing import Any, Callable, Mapping, Optional, Sequence


# =============================================================================
# 1. SHARED CONSTANTS AND CONFIGURATION
# =============================================================================

# Workflow steps have three possible presentation states.
STATE_COMPLETE = "complete"
STATE_ACTIVE = "active"
STATE_TODO = "todo"

# Restrict tone values to those supported by the shared CSS.
VALID_STATUS_TONES = frozenset({"blocked", "fail"})
VALID_HEADLINE_TONES = frozenset({"neutral", "good", "warn", "bad"})

# Changes below half a cent are ignored to prevent insignificant
# floating-point differences from appearing as accounting changes.
DEFAULT_CHANGE_TOLERANCE = 0.005

# Short alias for escaping text inserted into HTML.
_E = html.escape


# =============================================================================
# 2. WORKFLOW PROGRESS LOGIC
# =============================================================================

def step_states(steps: int, current: int) -> list[str]:
    """Determine the presentation state of every workflow step.

    Args:
        steps:
            Total number of workflow steps.

        current:
            Zero-based index of the currently active step. Pass ``steps``
            when every step is finished.

    Returns:
        A list containing "complete", "active", or "todo" for each step.

    Example:
        >>> step_states(4, 1)
        ['complete', 'active', 'todo', 'todo']

    Raises:
        ValueError:
            If the number of steps or active index is invalid.
    """

    # A workflow cannot have a negative number of steps.
    if steps < 0:
        raise ValueError("The number of workflow steps cannot be negative.")

    # An empty workflow has no active step.
    if steps == 0:
        if current != 0:
            raise ValueError("An empty workflow requires current=0.")
        return []

    # Prevent an invalid active step from silently producing an
    # incomplete or misleading progress indicator. ``current == steps`` is
    # valid: it means every step is finished (all complete, none active).
    if not 0 <= current <= steps:
        raise ValueError(
            f"Active step {current} is outside the valid range "
            f"0 through {steps} (use {steps} when every step is done)."
        )

    # Everything before the active step is complete.
    # Everything after the active step still needs to be completed.
    return [
        (
            STATE_COMPLETE
            if index < current
            else STATE_ACTIVE
            if index == current
            else STATE_TODO
        )
        for index in range(steps)
    ]


# =============================================================================
# 3. INPUT CHANGE DETECTION
# =============================================================================

def changed_inputs(
    previous: Mapping[str, Any],
    current: Mapping[str, Any],
) -> list[str]:
    """Identify inputs that changed between two workflow snapshots.

    An input is considered changed when:
        - Its value differs between runs.
        - It was added in the current run.
        - It was removed since the previous run.

    The resulting list follows the order of the current snapshot,
    followed by inputs that were removed.

    Example:
        previous = {"File": "old.xlsx", "Period": 1}
        current = {"File": "new.xlsx", "Period": 1}

        changed_inputs(previous, current)
        # Returns: ["File"]
    """

    # Detect modified inputs and newly added inputs.
    changed = [
        name
        for name, value in current.items()
        if name not in previous or previous[name] != value
    ]

    # Also detect inputs that existed previously but are now missing.
    # These are appended after the current snapshot's changes.
    changed.extend(
        name
        for name in previous
        if name not in current
    )

    return changed


# =============================================================================
# 4. VALUE FORMATTING
# =============================================================================

def _plain(value: Any) -> str:
    """Format a value for readable display in workflow messages.

    Formatting rules:
        - Floats: two decimal places with thousands separators.
        - Integers: thousands separators without decimal places.
        - Other values: standard string representation.

    Boolean values are intentionally excluded from integer formatting,
    even though bool is a subclass of int in Python.
    """

    if isinstance(value, float):
        return f"{value:,.2f}"

    if isinstance(value, int) and not isinstance(value, bool):
        return f"{value:,}"

    return str(value)


def _is_number(value: Any) -> bool:
    """Return whether a value is a supported numeric comparison type.

    Booleans are excluded so a change from True to False is treated
    as a status change rather than an arithmetic difference.
    """

    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
    )


# =============================================================================
# 5. NUMERIC AND NONNUMERIC CHANGE REPORTING
# =============================================================================

def changes_since(
    previous: Mapping[str, Any],
    current: Mapping[str, Any],
    labels: Mapping[str, str],
    formatters: Optional[Mapping[str, Callable[[Any], str]]] = None,
    cent: float = DEFAULT_CHANGE_TOLERANCE,
) -> list[str]:
    """Generate readable descriptions of changes between two runs.

    Only figures explicitly included in ``labels`` are reported.
    The ordering of ``labels`` controls the ordering of the output.

    Numeric values:
        Differences smaller than ``cent`` are ignored.

    Nonnumeric values:
        Changes are reported using their string representations.

    Missing values:
        Figures absent from either snapshot are skipped because
        a meaningful before-and-after comparison is unavailable.

    Example:
        previous = {"rows": 1203}
        current = {"rows": 1210}
        labels = {"rows": "Rows kept"}

        changes_since(previous, current, labels)

        # Returns:
        # ["Rows kept: 1,203 → 1,210 (+7)"]

    Args:
        previous:
            Values from the previous execution.

        current:
            Values from the current execution.

        labels:
            Mapping of internal field names to display labels.

        formatters:
            Optional custom formatting functions by field name.

        cent:
            Minimum absolute numeric difference to display.

    Returns:
        A list of human-readable change descriptions.
    """

    if not math.isfinite(cent) or cent < 0:
        raise ValueError(
            "Change tolerance must be finite and nonnegative."
        )

    # Use custom formatters when supplied; otherwise use _plain().
    formatters = formatters or {}

    changes: list[str] = []

    for key, label in labels.items():

        # Do not report a change unless both snapshots contain
        # the figure being evaluated.
        if key not in previous or key not in current:
            continue

        before = previous[key]
        after = current[key]

        formatter = formatters.get(key, _plain)

        # -------------------------------------------------------------
        # Numeric comparisons
        # -------------------------------------------------------------
        if _is_number(before) and _is_number(after):

            # Floating-point values can contain rounding noise.
            # Ignore insignificant differences when both values
            # and their arithmetic difference are finite.
            delta = after - before

            if (
                math.isfinite(before)
                and math.isfinite(after)
                and math.isfinite(delta)
            ):
                if abs(delta) < cent:
                    continue

                direction = "+" if delta > 0 else "-"

                changes.append(
                    f"{label}: "
                    f"{formatter(before)} → "
                    f"{formatter(after)} "
                    f"({direction}{formatter(abs(delta))})"
                )

                continue

        # -------------------------------------------------------------
        # Nonnumeric and non-finite comparisons
        # -------------------------------------------------------------
        # These values cannot reliably be compared using the
        # standard arithmetic tolerance.
        if before != after or (
            isinstance(before, float)
            and math.isnan(before)
        ) or (
            isinstance(after, float)
            and math.isnan(after)
        ):
            # Show as-is: a formatter is written for the figure's numbers
            # (money, counts) and must not be applied to text such as PASS.
            changes.append(f"{label}: {before} → {after}")

    return changes


# =============================================================================
# 6. WORKFLOW STEP STRIP HTML
# =============================================================================

def step_strip_html(
    steps: Sequence[tuple[str, str]],
    current: int,
    label: str = "Progress",
) -> str:
    """Render a workflow progress indicator.

    Each step contains:
        - A title.
        - A short descriptive message.
        - A progress state.

    Completed steps display a checkmark.
    The active step receives the aria-current attribute.

    Args:
        steps:
            Sequence of (title, detail) tuples.

        current:
            Zero-based active step index.

        label:
            Accessible label describing the progress indicator.
    """

    # Determine each step's state before building the HTML.
    states = step_states(len(steps), current)

    items: list[str] = []

    for index, ((title, detail), state) in enumerate(
        zip(steps, states)
    ):
        # Completed steps show a checkmark; remaining steps show
        # their sequential step number.
        node = "✓" if state == STATE_COMPLETE else str(index + 1)

        # Assistive technologies can identify the active step.
        aria_current = (
            ' aria-current="step"'
            if state == STATE_ACTIVE
            else ""
        )

        # Escape titles and details to prevent arbitrary content
        # from being interpreted as HTML markup.
        items.append(
            f'<li class="pp-wf-step" '
            f'data-state="{state}"{aria_current}>'
            f'<span class="pp-wf-node">{node}</span>'
            f'<span class="pp-wf-copy">'
            f'<span class="pp-wf-title">{_E(title)}</span>'
            f'<span class="pp-wf-detail">{_E(detail)}</span>'
            f'</span>'
            f'</li>'
        )

    return (
        f'<ol class="pp-wf-steps" aria-label="{_E(label)}">'
        f'{"".join(items)}'
        f'</ol>'
    )


# =============================================================================
# 7. WORKFLOW STATUS PANEL HTML
# =============================================================================

def status_html(
    blockers: Sequence[str],
    ready: str,
    *,
    tone: str = "blocked",
    next_step: str = "",
) -> str:
    """Render the workflow status panel.

    Two display modes are supported:

    Ready:
        No blockers exist. Display a success indicator and
        optionally describe the next action.

    Blocked:
        One or more blockers exist. List each problem preventing
        the workflow from proceeding.

    Supported blocked-state tones:
        blocked: Warning requiring attention.
        fail: A failed control or validation.

    An empty blocker list always produces the ready state.
    """

    # -------------------------------------------------------------
    # Ready state: no outstanding blockers.
    # -------------------------------------------------------------
    if not blockers:

        next_html = (
            f'<span class="pp-wf-next">{_E(next_step)}</span>'
            if next_step
            else ""
        )

        return (
            '<div class="pp-wf-status" '
            'data-tone="ready" role="status">'
            '<span class="pp-wf-icon" aria-hidden="true">✓</span>'
            '<div>'
            f'<strong>{_E(ready)}</strong>'
            f'{next_html}'
            '</div>'
            '</div>'
        )

    # -------------------------------------------------------------
    # Blocked state: one or more required actions remain.
    # -------------------------------------------------------------

    # Invalid tones fall back to the standard blocked state.
    tone = tone if tone in VALID_STATUS_TONES else "blocked"

    # Use grammatically appropriate headings.
    heading = (
        "Blocking the next step"
        if len(blockers) == 1
        else f"{len(blockers)} things are blocking the next step"
    )

    # Each blocker is escaped and displayed as a list item.
    blocker_items = "".join(
        f"<li>{_E(blocker)}</li>"
        for blocker in blockers
    )

    return (
        f'<div class="pp-wf-status" '
        f'data-tone="{tone}" role="status">'
        '<span class="pp-wf-icon" aria-hidden="true">!</span>'
        '<div>'
        f'<strong>{heading}</strong>'
        f'<ul>{blocker_items}</ul>'
        '</div>'
        '</div>'
    )


# =============================================================================
# 8. CHANGES SINCE THE PREVIOUS RUN
# =============================================================================

def changes_html(
    lines: Sequence[str],
    title: str = "Since the last run",
) -> str:
    """Render an expandable list of changes between executions.

    If there are no reported changes, return an empty string
    so the calling application can omit the panel entirely.

    The HTML details element provides native expand/collapse
    functionality without requiring additional JavaScript.
    """

    if not lines:
        return ""

    # Build an escaped list of reported changes.
    items = "".join(
        f"<li>{_E(line)}</li>"
        for line in lines
    )

    count = len(lines)
    noun = "change" if count == 1 else "changes"

    return (
        '<details class="pp-wf-changes" open>'
        f'<summary>{_E(title)}: {count} {noun}</summary>'
        f'<ul>{items}</ul>'
        '</details>'
    )


# =============================================================================
# 9. HEADLINE / KEY PERFORMANCE INDICATOR HTML
# =============================================================================

def headline_html(
    value: str,
    label: str,
    sentence: str = "",
    tone: str = "neutral",
) -> str:
    """Render a headline figure with supporting context.

    This component is intended for a single important value,
    such as an accrual total, processed row count, or variance.

    Supported tones:
        neutral: Informational.
        good: Favorable or completed.
        warn: Needs attention.
        bad: Unfavorable or failed.

    The calling tool is responsible for choosing an appropriate
    tone based on its actual accounting or validation results.
    """

    # Unsupported values revert to the neutral presentation.
    tone = (
        tone
        if tone in VALID_HEADLINE_TONES
        else "neutral"
    )

    # Omit the explanatory paragraph when no sentence is supplied.
    sentence_html = (
        f'<p class="pp-wf-sentence">{_E(sentence)}</p>'
        if sentence
        else ""
    )

    return (
        f'<div class="pp-wf-headline" data-tone="{tone}">'
        f'<span class="pp-wf-value">{_E(value)}</span>'
        '<div>'
        f'<span class="pp-wf-label">{_E(label)}</span>'
        f'{sentence_html}'
        '</div>'
        '</div>'
    )


# =============================================================================
# 10. COMBINED WORKFLOW PRESENTATION
# =============================================================================

def workflow_html(
    steps: Sequence[tuple[str, str]],
    current: int,
    blockers: Sequence[str],
    ready: str,
    *,
    tone: str = "blocked",
    next_step: str = "",
    changes: Sequence[str] = (),
    changes_title: str = "Since the last run",
    label: str = "Progress",
) -> str:
    """Combine the workflow's main presentation components.

    This function acts as the primary entry point for calling tools
    that need the complete workflow interface.

    Components are rendered in this order:

        1. Workflow progress strip.
        2. Current status / blocking conditions.
        3. Changes since the previous execution.

    The calling tool can insert the resulting HTML into a single
    designated container or UI placeholder.

    No accounting calculations or validation decisions occur here.
    """

    # Display the current position within the workflow.
    progress = step_strip_html(
        steps=steps,
        current=current,
        label=label,
    )

    # Display blockers, or confirm that the workflow is ready.
    status = status_html(
        blockers=blockers,
        ready=ready,
        tone=tone,
        next_step=next_step,
    )

    # Display changes only when there is something to report.
    change_panel = changes_html(
        lines=changes,
        title=changes_title,
    )

    # Assemble the complete display without introducing new
    # accounting logic or modifying any of the supplied values.
    return progress + status + change_panel
