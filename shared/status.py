
"""Shared status classification and presentation utilities.

This module establishes a consistent status vocabulary across the
application suite.

Supported status categories:
    PASS        - A control or validation completed successfully.
    REVIEW      - A condition requires attention or investigation.
    FAIL        - A control or validation failed.
    MATCHED     - A transaction or record has been matched.
    UNRESOLVED  - A transaction or exception remains unresolved.
    NEUTRAL     - An unrecognized or informational status.

The module provides three presentation components:

    1. DataFrame styling:
       Applies standardized status colors to selected columns.

    2. HTML status badges:
       Displays a status label with its associated color and icon.

    3. HTML status tiles:
       Displays a status category and a formatted count.

Design principles
-----------------
1. Presentation only:
   Statuses are determined by the calling application or accounting
   engine. This module does not perform accounting validations.

2. Shared vocabulary:
   Every application interprets status labels consistently.

3. Accessibility:
   Status badges include readable text and a decorative icon.
   Color is never the only means of conveying status.

4. Safe HTML:
   Dynamic status labels are escaped before rendering.

5. Consistent styling:
   HTML components use classes defined in:
       shared/styles/base/status.css

   Color variables are defined in:
       shared/styles/base/tokens.css

   Pandas Styler requires literal colors rather than CSS variables,
   so its palette mirrors the values maintained in tokens.css.
"""

from __future__ import annotations

import html
from typing import Any, Iterable, Mapping, Optional

import pandas as pd


# =============================================================================
# 1. SHARED STATUS CONSTANTS
# =============================================================================

# Define status names once to avoid inconsistent string literals
# throughout the module.
STATUS_PASS = "pass"
STATUS_REVIEW = "review"
STATUS_FAIL = "fail"
STATUS_MATCHED = "matched"
STATUS_UNRESOLVED = "unresolved"
STATUS_NEUTRAL = "neutral"


# =============================================================================
# 2. STATUS LABEL CLASSIFICATION
# =============================================================================

# Map status prefixes from the accounting engine to presentation kinds.
#
# Longer, more specific prefixes appear before shorter alternatives.
# This ensures that labels such as "TRUE UNMATCHED" are interpreted
# according to their intended status category.
#
# Prefix matching also allows descriptive labels:
#     "PASS - No Activity"  -> pass
#     "REVIEW REQUIRED"     -> review
#     "TRUE_UNMATCHED"      -> unresolved
#
# Underscores are normalized to spaces before classification.

_STATUS_PREFIXES = (
    ("TRUE UNMATCHED", STATUS_UNRESOLVED),
    ("UNRESOLVED", STATUS_UNRESOLVED),
    ("WARNING", STATUS_REVIEW),
    ("UNMATCHED", STATUS_UNRESOLVED),
    ("MATCHED", STATUS_MATCHED),
    ("REVIEW", STATUS_REVIEW),
    ("FAIL", STATUS_FAIL),
    ("PASS", STATUS_PASS),
)


def _normalize_status(label: object) -> str:
    """Normalize a status label for consistent classification.

    Normalization includes:
        - Converting the value to a string.
        - Removing leading and trailing whitespace.
        - Converting text to uppercase.
        - Replacing underscores with spaces.
        - Collapsing repeated whitespace.

    Example:
        >>> _normalize_status("  review_hold  ")
        'REVIEW HOLD'
    """

    # Missing values should not accidentally become recognized statuses.
    if label is None:
        return ""

    # Pandas NA values and floating-point NaN also indicate missing data.
    # Handle common scalar missing values without introducing
    # ambiguous comparisons for unsupported collection types.
    if isinstance(label, (str, int, float, bool)):
        if pd.isna(label):
            return ""

    normalized = str(label).strip().upper().replace("_", " ")

    # Joining split words removes repeated spaces and tabs.
    return " ".join(normalized.split())


def status_kind(label: str) -> str:
    """Return the standardized presentation category for a label.

    The function recognizes known prefixes while requiring a
    boundary after each prefix.

    This prevents unrelated words such as "PASSENGER" from being
    incorrectly classified as PASS.

    Args:
        label:
            Status text supplied by the calling application.

    Returns:
        One of:
            pass, review, fail, matched, unresolved, neutral

    Examples:
        >>> status_kind("PASS - No Activity")
        'pass'

        >>> status_kind("REVIEW_HOLD")
        'review'

        >>> status_kind("TRUE_UNMATCHED")
        'unresolved'

        >>> status_kind("Unknown Status")
        'neutral'
    """

    text = _normalize_status(label)

    # Compare the normalized label against recognized status prefixes.
    for prefix, kind in _STATUS_PREFIXES:

        # An exact match is always accepted.
        if text == prefix:
            return kind

        # A descriptive label is accepted only when the prefix
        # is followed by a separator rather than another letter.
        if text.startswith(prefix):
            remainder = text[len(prefix):]

            if remainder and not remainder[0].isalnum():
                return kind

            # Normalized spaces also separate status phrases.
            if remainder.startswith(" "):
                return kind

    # Unrecognized labels receive neutral presentation styling.
    return STATUS_NEUTRAL


# =============================================================================
# 3. SHARED COLOR PALETTE
# =============================================================================

# Streamlit's st.dataframe() uses its own rendering mechanism.
# Pandas Styler therefore needs literal hexadecimal color values
# instead of relying on CSS variables.
#
# IMPORTANT:
# These colors must remain synchronized with:
#     shared/styles/base/tokens.css
#
# The shared status tests should detect accidental palette drift.

PALETTE = {
    STATUS_PASS: (
        "#eefaf1",  # Light green background
        "#17552b",  # Dark green text
    ),
    STATUS_REVIEW: (
        "#fff8e6",  # Light amber background
        "#5c4300",  # Dark amber text
    ),
    STATUS_FAIL: (
        "#fdecea",  # Light red background
        "#8a1f17",  # Dark red text
    ),
}


# Matched and unresolved are distinct workflow statuses,
# but they share presentation colors with PASS and REVIEW.
#
# This avoids duplicating palette definitions unnecessarily.
_PALETTE_KIND = {
    STATUS_MATCHED: STATUS_PASS,
    STATUS_UNRESOLVED: STATUS_REVIEW,
}


# Decorative icons provide an additional visual indication
# of status without relying exclusively on color.
#
# Screen readers receive the status through the visible label,
# so icons are marked aria-hidden in the HTML badge.
_STATUS_ICONS = {
    STATUS_PASS: "✓",
    STATUS_REVIEW: "!",
    STATUS_FAIL: "✕",
    STATUS_MATCHED: "✓",
    STATUS_UNRESOLVED: "?",
    STATUS_NEUTRAL: "•",
}


# =============================================================================
# 4. STATUS COLOR RESOLUTION
# =============================================================================

def _status_colors(kind: str) -> Optional[tuple[str, str]]:
    """Return the background and foreground colors for a status.

    Matched statuses use PASS colors.
    Unresolved statuses use REVIEW colors.

    Neutral or unrecognized statuses return None, indicating
    that no additional cell styling should be applied.
    """

    # Resolve statuses that share another category's palette.
    palette_kind = _PALETTE_KIND.get(kind, kind)

    return PALETTE.get(palette_kind)


def _cell_status_style(value: object) -> str:
    """Generate the CSS declarations for a single status cell.

    The function returns an empty string when the cell does not
    contain a recognized status.

    Pandas Styler interprets the returned string as CSS properties
    applied to the corresponding table cell.
    """

    kind = status_kind(value)
    colors = _status_colors(kind)

    # Leave neutral or unrecognized statuses unstyled.
    if colors is None:
        return ""

    background, foreground = colors

    return (
        f"background-color: {background}; "
        f"color: {foreground}; "
        "font-weight: 700;"
    )


# =============================================================================
# 5. PANDAS DATAFRAME STATUS STYLING
# =============================================================================

def status_styler(
    df: pd.DataFrame,
    columns: Iterable[str],
    formats: Optional[Mapping[str, str]] = None,
) -> pd.io.formats.style.Styler:
    """Apply standardized status styling to DataFrame columns.

    This function returns a Pandas Styler rather than modifying
    the original DataFrame.

    Only the specified columns receive status-based cell styling.

    Recognized statuses:
        PASS / MATCHED:
            Green background with dark green text.

        REVIEW / UNRESOLVED:
            Amber background with dark amber text.

        FAIL:
            Red background with dark red text.

        Other values:
            No additional styling.

    Args:
        df:
            Source DataFrame containing accounting or workflow data.

        columns:
            Names of columns containing status labels.

        formats:
            Optional numeric and text formatting rules.

            Example:
                {
                    "Amount": "{:,.2f}",
                    "Variance": "{:,.2f}"
                }

    Returns:
        A Pandas Styler with formatting and status colors applied.

    Raises:
        ValueError:
            If a requested status column does not exist.

    Notes:
        Pandas Styler may override Streamlit's default display
        formatting. Explicit format definitions help preserve
        consistent financial presentation.

        The returned Styler is for display purposes only.
        The underlying DataFrame values remain unchanged.
    """

    # Materialize the iterable once so generators can be used safely.
    status_columns = list(columns)

    # Identify missing columns before attempting to style the data.
    missing_columns = [
        column
        for column in status_columns
        if column not in df.columns
    ]

    # A descriptive error makes integration problems easier to diagnose.
    if missing_columns:
        raise ValueError(
            "The following status columns are missing from "
            f"the DataFrame: {missing_columns}"
        )

    # Apply user-defined number formats while showing missing
    # values as blank cells.
    styler = df.style.format(
        formatter=dict(formats) if formats is not None else None,
        na_rep="",
    )

    # If no status columns were supplied, return the formatted
    # DataFrame without applying additional styling.
    if not status_columns:
        return styler

    # Apply the same status classifier used by HTML badges and tiles.
    return styler.map(
        _cell_status_style,
        subset=status_columns,
    )


# =============================================================================
# 6. HTML STATUS BADGES
# =============================================================================

def status_badge(label: str) -> str:
    """Render an HTML badge for a status label.

    Each badge contains:
        - A status-specific CSS class.
        - A decorative status icon.
        - The original status label.

    The status icon reinforces the meaning of the label visually.
    It is hidden from screen readers because the text already
    communicates the same information.

    Dynamic labels are HTML-escaped to prevent markup injection.

    Example:
        status_badge("PASS")

        Produces a badge with:
            CSS class: pp-status--pass
            Icon: ✓
            Text: PASS

    Returns:
        An HTML string suitable for rendering in a trusted
        application component that permits HTML content.
    """

    # Determine the status class from the shared vocabulary.
    kind = status_kind(label)

    # Select the icon associated with the resolved status.
    icon = _STATUS_ICONS.get(kind, _STATUS_ICONS[STATUS_NEUTRAL])

    # Escape the label so special HTML characters are displayed
    # as text rather than interpreted as markup.
    safe_label = html.escape(str(label), quote=True)

    return (
        f'<span class="pp-status pp-status--{kind}">'
        f'<span class="pp-status-icon" aria-hidden="true">'
        f'{icon}'
        f'</span>'
        f'{safe_label}'
        f'</span>'
    )


# =============================================================================
# 7. HTML STATUS COUNT TILES
# =============================================================================

def status_tile(label: str, count: int) -> str:
    """Render a status summary tile containing a label and count.

    This component can replace a plain Streamlit metric when
    displaying counts of records by processing status.

    Examples:
        PASS:       1,250
        REVIEW:        18
        FAIL:           3
        UNRESOLVED:    42

    Args:
        label:
            Human-readable status category.

        count:
            Number of records belonging to that category.

    Returns:
        An HTML string containing the styled status tile.

    Notes:
        The function does not count records or determine statuses.
        The calling application must supply the final count.

        Counts are formatted using thousands separators.
    """

    # Determine which CSS modifier applies to this tile.
    kind = status_kind(label)

    # Escape user-supplied labels before inserting them into HTML.
    safe_label = html.escape(str(label), quote=True)

    # Normalize the supplied count and make large values easier
    # to read using thousands separators.
    formatted_count = f"{int(count):,}"

    return (
        f'<div class="pp-status-tile pp-status-tile--{kind}">'
        f'<div class="pp-status-tile-label">'
        f'{safe_label}'
        f'</div>'
        f'<div class="pp-status-tile-count">'
        f'{formatted_count}'
        f'</div>'
        f'</div>'
    )
