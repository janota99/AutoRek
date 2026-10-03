"""Upload-card helpers for the Recon page: file hashing, cached ingestion wrappers,
and the compact dataset summary shown under each uploaded file."""

from __future__ import annotations

import html
from typing import Any, Optional

import pandas as pd
import streamlit as st

from .ingestion import (
    INF_COLUMN_PATTERNS,
    QB_COLUMN_PATTERNS,
    build_source_validation_report,
    detect_header_row,
    file_sha256,
    filter_qb_subtotal_rows,
    fiscal_period_column_profile,
    infer_column,
    list_source_sheets,
    read_source_file,
)
from .matching import parse_amount_cents


def render_dataset_summary(summary: dict[str, Any]) -> None:
    """Render compact upload metadata without requiring a UI-module update."""
    items = [
        ("Rows", f"{int(summary.get('rows', 0)):,}"),
        ("Columns", f"{int(summary.get('columns', 0)):,}"),
    ]

    worksheet = summary.get("worksheet")
    if worksheet:
        items.append(("Worksheet", str(worksheet)))

    date_start = summary.get("date_start")
    date_end = summary.get("date_end")
    if date_start and date_end:
        date_value = date_start if date_start == date_end else f"{date_start}–{date_end}"
        items.append(("Dates", date_value))

    if summary.get("amount_total_cents") is not None:
        cents = int(summary["amount_total_cents"])
        sign = "-" if cents < 0 else ""
        items.append(("Signed total", f"{sign}${abs(cents) / 100:,.2f}"))

    missing = summary.get("missing_fields") or []
    found_label = "Required fields"
    status_markup = (
        f'<span class="dataset-summary-item dataset-summary-warn">'
        f'<span class="dataset-summary-label">Missing</span>'
        f'<strong>{html.escape(", ".join(missing))}</strong></span>'
        if missing else
        f'<span class="dataset-summary-item dataset-summary-ok">'
        f'<span class="dataset-summary-label">{found_label}</span><strong>&#10003; Found</strong></span>'
    )
    filename = summary.get("filename")
    name_markup = (
        f'<span class="dataset-summary-item"><span class="dataset-summary-label">File</span>'
        f'<strong>{html.escape(str(filename))}</strong></span>' if filename else ""
    )

    item_markup = name_markup + "".join(
        f'<span class="dataset-summary-item">'
        f'<span class="dataset-summary-label">{html.escape(label)}</span>'
        f'<strong>{html.escape(value)}</strong>'
        f'</span>'
        for label, value in items
    ) + status_markup
    st.markdown(
        f'<div class="dataset-summary" aria-label="Uploaded dataset summary">'
        f'{item_markup}</div>',
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# UI Caching & Memoization Wrappers
# ---------------------------------------------------------------------------

def get_true_file_hash(file_obj: Any) -> str:
    """Cache the cryptographic hash using Streamlit's native file ID to prevent O(N) re-hashing on every UI click."""
    if file_obj is None:
        return "NONE"
    file_id = getattr(file_obj, "file_id", file_obj.name)
    cache_key = f"__file_hash_{file_id}_{file_obj.size}"
    if cache_key not in st.session_state:
        st.session_state[cache_key] = file_sha256(file_obj.getvalue())
    return st.session_state[cache_key]


@st.cache_data(show_spinner=False, max_entries=8)
def cached_filter_qb_subtotal_rows(
    frame: pd.DataFrame, mapping: dict
) -> tuple[pd.DataFrame, int, dict]:
    """Cache subtotal filtering. Explicitly returns attrs dict to prevent PyArrow serialization drops."""
    filtered, excluded = filter_qb_subtotal_rows(frame, mapping)
    audit_dict = dict(filtered.attrs.get("qb_subtotal_filter_audit", {}))
    return filtered, excluded, audit_dict


@st.cache_data(show_spinner=False, max_entries=8)
def cached_build_source_validation_report(
    frame: pd.DataFrame, mapping: dict, source: str, audit: dict, **kwargs: Any
) -> pd.DataFrame:
    return build_source_validation_report(frame, mapping, source, audit, **kwargs)


@st.cache_data(show_spinner=False, max_entries=16)
def cached_fiscal_period_column_profile(series: pd.Series) -> tuple[int, int, bool]:
    return fiscal_period_column_profile(series)


@st.cache_data(show_spinner=False, max_entries=16)
def cached_dataset_summary(
    file_bytes: bytes,
    filename: str,
    source: str,
    selected_sheet: Optional[str] = None,
) -> dict[str, Any]:
    """Read one source once and return reliable pre-reconciliation metadata."""
    sheets = list_source_sheets(file_bytes, filename)
    worksheet = selected_sheet if selected_sheet in sheets else (sheets[0] if sheets else None)
    header_row = detect_header_row(file_bytes, filename, source, worksheet)
    frame = read_source_file(file_bytes, filename, header_row, worksheet)
    frame = frame.dropna(how="all")

    summary: dict[str, Any] = {
        "rows": int(len(frame)),
        "columns": int(len(frame.columns)),
        "worksheet": worksheet if len(sheets) > 0 else None,
    }

    summary["filename"] = filename

    # Required fields (PO, invoice, amount), found the same way the mapping panel's defaults are.
    patterns = QB_COLUMN_PATTERNS if source == "QB" else INF_COLUMN_PATTERNS
    columns = list(frame.columns)
    found = {field: infer_column(columns, patterns[field]) for field in ("po", "invoice", "amount")}
    labels = {"po": "PO", "invoice": "Invoice", "amount": "Amount"}
    summary["missing_fields"] = [labels[f] for f, col in found.items() if col is None]

    # Signed total of the amount column. QuickBooks subtotal rows are dropped first (the same
    # filter the reconciliation applies), or the total would double count. Display only.
    if found["amount"]:
        try:
            detail = frame
            if source == "QB":
                detail, _ = filter_qb_subtotal_rows(
                    frame, {"amount": found["amount"], "quantity": infer_column(columns, QB_COLUMN_PATTERNS["quantity"])}
                )
            cents = detail[found["amount"]].map(parse_amount_cents).dropna()
            summary["amount_total_cents"] = int(cents.sum())
        except Exception:
            pass

    date_columns = [
        column for column in columns if "date" in str(column).strip().casefold()
    ]
    inferred_date = infer_column(columns, INF_COLUMN_PATTERNS["date"]) if source != "QB" else None
    if inferred_date and inferred_date not in date_columns:
        date_columns.append(inferred_date)
    for column in date_columns:
        parsed_dates = pd.to_datetime(frame[column], errors="coerce")
        parsed_dates = parsed_dates.dropna()
        if parsed_dates.empty:
            continue
        summary["date_start"] = parsed_dates.min().strftime("%b %d, %Y")
        summary["date_end"] = parsed_dates.max().strftime("%b %d, %Y")
        break

    return summary


def render_uploaded_dataset_summary(
    file_obj: Any,
    source: str,
    sheet_state_prefix: str,
) -> None:
    """Render metadata without allowing a preview issue to block an upload."""
    if file_obj is None:
        return

    file_hash = get_true_file_hash(file_obj)
    selected_sheet = st.session_state.get(f"{sheet_state_prefix}_{file_hash[:12]}")
    try:
        summary = cached_dataset_summary(
            file_obj.getvalue(),
            file_obj.name,
            source,
            selected_sheet,
        )
        render_dataset_summary(summary)
    except Exception:
        st.caption("Dataset summary will appear after the import settings are confirmed.")
