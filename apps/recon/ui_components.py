"""Reusable Streamlit interface blocks.

Every function that renders something onto the page lives here: CSS
loading, KPI cards, dataframe previews, the ingestion-flow stepper, upload
status badges, and the full tabbed results view. Workbook bytes themselves
are still produced by the ``workpapers`` package; this module only calls those
builders from the Downloads tab and displays the result.
"""

from __future__ import annotations

import html
import re
import time
import traceback
from pathlib import Path
from typing import Any, Optional

import pandas as pd
import streamlit as st

from shared.status import status_badge, status_styler
from shared.styles import inject_page

from .matching import QB_ID, ReconciliationResult, numeric_sum
from .ui_review import render_review_panel
from .utils import format_currency
from .workpapers import (
    build_legacy_workbook,
    build_primary_workbook,
    paired_display_frames,
)


def _render_workbook_exception(label: str, exc: Exception) -> None:
    """Show a concise failure plus reproducible module/version diagnostics."""
    from . import excel_styles
    import openpyxl
    from . import workpapers

    st.error(f"The {label} could not be prepared: {exc}")
    details = "".join(
        traceback.format_exception(type(exc), exc, exc.__traceback__)
    )
    with st.expander("Technical details", expanded=True):
        st.code(
            "\n".join(
                [
                    f"OpenPyXL version: {openpyxl.__version__}",
                    f"OpenPyXL loaded from: {Path(openpyxl.__file__).resolve()}",
                    f"workpapers loaded from: {Path(workpapers.__file__).resolve()}",
                    f"excel_styles.py loaded from: {Path(excel_styles.__file__).resolve()}",
                    "",
                    details,
                ]
            ),
            language="text",
        )


def load_app_css() -> None:
    """Load Recon's stylesheet (shared/styles/pages/recon.css)."""
    inject_page("recon")


def render_kpi(label: str, value: str, subtitle: str = "") -> None:
    st.markdown(
        f"""
        <div class="rec-card">
            <div class="rec-kpi-label">{label}</div>
            <div class="rec-kpi-value">{value}</div>
            <div class="rec-kpi-sub">{subtitle}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_section_heading(title: str, subtitle: str = "") -> None:
    """Render a consistent section heading with restrained supporting copy."""
    subtitle_markup = (
        f'<p class="rec-section-subtitle">{html.escape(subtitle)}</p>'
        if subtitle else ""
    )
    st.markdown(
        f'<div class="rec-section-heading">'
        f'<h2>{html.escape(title)}</h2>'
        f'{subtitle_markup}'
        f'</div>',
        unsafe_allow_html=True,
    )


def render_notice_panel(
    title: str,
    body: str,
    *,
    tone: str = "info",
    icon: str = "i",
    body_is_html: bool = False,
    contained: bool = False,
) -> None:
    """Render a neutral notice panel with a restrained semantic accent.

    ``contained`` limits the panel to the upload cards' width (``--rec-content-width``) so a
    page-level callout lines up with them; leave it off beside full-width results tables.
    """
    allowed_tones = {"info", "success", "warning", "danger"}
    safe_tone = tone if tone in allowed_tones else "info"
    body_markup = body if body_is_html else html.escape(body)
    width_class = " contained" if contained else ""
    st.markdown(
        f'<div class="notice-panel {safe_tone}{width_class}">'
        f'<div class="notice-icon" aria-hidden="true">{html.escape(icon)}</div>'
        f'<div class="notice-content">'
        f'<div class="notice-title">{html.escape(title)}</div>'
        f'<div class="notice-body">{body_markup}</div>'
        f'</div>'
        f'</div>',
        unsafe_allow_html=True,
    )


def display_primary_preview(result: ReconciliationResult) -> pd.DataFrame:
    qb_display, inf_display, methods = paired_display_frames(result)
    qb_display = qb_display.add_prefix("QB | ")
    inf_display = inf_display.add_prefix("INF | ")
    # paired_display_frames walks result.paired_rows in order, so the frames
    # and these two reference columns line up row for row.
    match_panel = pd.DataFrame({
        "Match Ref.": [row.get("Match Ref.", "") for row in result.paired_rows],
        "Match Result": methods,
        "Referenced Match Ref.": [row.get("Referenced Match Ref.", "") for row in result.paired_rows],
    })
    return pd.concat([qb_display, match_panel, inf_display], axis=1)


def render_limited_dataframe(
    frame: pd.DataFrame,
    *,
    height: Optional[int] = None,
    row_limit: int = 2000,
) -> None:
    """Keep browser previews responsive while preserving full Excel outputs."""
    displayed = frame.head(row_limit)
    kwargs: dict[str, Any] = {
        "width": "stretch",
        "hide_index": True,
    }
    if height is not None:
        kwargs["height"] = height
    st.dataframe(displayed, **kwargs)
    if len(frame) > row_limit:
        st.caption(
            f"Showing the first {row_limit:,} of {len(frame):,} rows. "
            "The complete population is retained in the downloadable workbook."
        )


def show_toast_once(state_key: str, message: str, icon: str = "✅") -> None:
    """Show a native toast once without repeating it on every app rerun."""
    if not st.session_state.get(state_key, False):
        st.toast(message, icon=icon)
        st.session_state[state_key] = True


def attention_tab_label(label: str, count: int) -> str:
    """Add a compatible text badge only when a tab has review items."""
    return f"{label} ({count:,})" if count > 0 else label


def render_source_status(
    filename: Optional[str],
    next_step: str,
    *,
    pending: bool = False,
) -> None:
    """Render file state and expose a stable hook for compact loaded cards."""
    if filename:
        st.markdown(
            f'<div class="source-status complete"><span class="status-dot"></span>'
            f'<span>Loaded: {html.escape(filename)}</span></div>',
            unsafe_allow_html=True,
        )
    else:
        status_class = "pending" if pending else "active"
        st.markdown(
            f'<div class="source-status {status_class}"><span class="status-dot"></span>'
            f'<span>{html.escape(next_step)}</span></div>',
            unsafe_allow_html=True,
        )


def render_dataset_summary(summary: dict[str, Any]) -> None:
    """Render a compact, pre-reconciliation summary for an uploaded dataset."""
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

    item_markup = "".join(
        f'<span class="dataset-summary-item">'
        f'<span class="dataset-summary-label">{html.escape(label)}</span>'
        f'<strong>{html.escape(value)}</strong>'
        f'</span>'
        for label, value in items
    )
    st.markdown(
        f'<div class="dataset-summary" aria-label="Uploaded dataset summary">'
        f'{item_markup}</div>',
        unsafe_allow_html=True,
    )


def render_upload_source_heading(
    label: str,
    logo_uri: str,
    logo_class: str,
    *,
    context: str = "Data source",
    required: bool = False,
) -> None:
    """Render an integrated brand heading for an upload card."""
    requirement = (
        '<span class="upload-requirement required">Required</span>'
        if required else '<span class="upload-requirement optional">Optional</span>'
    )
    st.markdown(
        f'<div class="upload-source-heading">'
        f'<div class="upload-brand-group">'
        f'<img class="upload-source-logo {html.escape(logo_class)}" '
        f'src="{html.escape(logo_uri, quote=True)}" alt="{html.escape(label)} logo">'
        f'<div class="upload-source-copy">'
        f'<span class="upload-source-context">{html.escape(context)}</span>'
        f'<span class="upload-source-title">{html.escape(label)}</span>'
        f'</div></div>'
        f'{requirement}'
        f'</div>',
        unsafe_allow_html=True,
    )


def render_ingestion_flow(
    qb_loaded: bool,
    inf_loaded: bool,
    mappings_ready: bool,
    reconciliation_complete: bool,
    source_validation_failed: bool = False,
) -> None:
    # The two uploads are independent, so either can come first and both read as "waiting" until loaded.
    step1_class = "complete" if qb_loaded else "active"
    step1_status = "Upload confirmed" if qb_loaded else "Waiting for upload"
    step2_class = "complete" if inf_loaded else "active"
    step2_status = "Upload confirmed" if inf_loaded else "Waiting for upload"
    if reconciliation_complete:
        step3_class, step3_status = "complete", "Controls validated"
    elif qb_loaded and inf_loaded and source_validation_failed:
        step3_class, step3_status = "active", "Resolve source validation"
    elif qb_loaded and inf_loaded and mappings_ready:
        step3_class, step3_status = "active", "Ready to run"
    elif qb_loaded and inf_loaded:
        step3_class, step3_status = "active", "Confirm required mappings"
    else:
        step3_class, step3_status = "upcoming", "Waiting for both files"
    if reconciliation_complete:
        step4_class, step4_status = "active", "Review and download"
    else:
        step4_class, step4_status = "upcoming", "Available after reconciliation"

    steps = [
        (1, "Ingest QuickBooks", step1_status, step1_class),
        (2, "Ingest Infinium", step2_status, step2_class),
        (3, "Run Reconciliation", step3_status, step3_class),
        (4, "Review Results", step4_status, step4_class),
    ]
    items = "".join(
        f'<div class="stepper-item {css_class}">'
        f'<span class="stepper-node">{"✓" if css_class == "complete" else number}</span>'
        f'<span class="stepper-copy">'
        f'<span class="stepper-title">{html.escape(title)}</span>'
        f'<span class="stepper-status">{html.escape(status)}</span>'
        f'</span>'
        f'</div>'
        for number, title, status, css_class in steps
    )
    flow_html = (
        f'<nav class="rec-stepper" aria-label="Reconciliation progress">'
        f'{items}'
        f'</nav>'
    )
    st.markdown(flow_html, unsafe_allow_html=True)


def _parse_summary_date(value: Optional[str]) -> Optional[pd.Timestamp]:
    if not value:
        return None
    parsed = pd.to_datetime(value, errors="coerce")
    return None if pd.isna(parsed) else parsed


def render_source_period_alignment(
    qb_summary: Optional[dict[str, Any]],
    inf_summary: Optional[dict[str, Any]],
    fiscal_period: Optional[int],
    fiscal_year: Optional[int],
    selected_period_rows: Optional[int],
    qb_rows: int,
) -> None:
    """Show each source's date coverage beside the selected period, flagging disagreement.

    Informational only: it never changes a match or a total. The suite has no fiscal
    calendar, so the person confirms the dates against the intended period.
    """
    def coverage(summary: Optional[dict[str, Any]]) -> str:
        if not summary or not summary.get("date_start"):
            return "No date column detected"
        start, end = summary["date_start"], summary["date_end"]
        return start if start == end else f"{start} – {end}"

    period_name = "All periods" if fiscal_period is None else f"Period {int(fiscal_period):02d}"
    period_label = f"{period_name}, fiscal year {int(fiscal_year)}" if fiscal_year else period_name

    qb_start = _parse_summary_date((qb_summary or {}).get("date_start"))
    qb_end = _parse_summary_date((qb_summary or {}).get("date_end"))
    inf_start = _parse_summary_date((inf_summary or {}).get("date_start"))
    inf_end = _parse_summary_date((inf_summary or {}).get("date_end"))
    if None in (qb_start, qb_end, inf_start, inf_end):
        agreement = "Date coverage could not be compared for both sources; confirm the dates manually."
        tone = "warning"
    elif qb_start == inf_start and qb_end == inf_end:
        agreement = "QuickBooks and Infinium cover the same date range."
        tone = "success"
    else:
        agreement = (
            "QuickBooks and Infinium date ranges <strong>differ</strong> "
            f"(start {abs((qb_start - inf_start).days)} day(s) apart, "
            f"end {abs((qb_end - inf_end).days)} day(s) apart). "
            "Rows outside the other source's range will surface as exceptions; confirm this is intended."
        )
        tone = "warning"

    if selected_period_rows is None:
        period_rows = "No fiscal period selected; every primary QuickBooks period is included in the aggregates."
    elif selected_period_rows == qb_rows:
        period_rows = f"All {qb_rows:,} retained QuickBooks rows carry {period_name} in the first column."
    else:
        period_rows = (
            f"<strong>{selected_period_rows:,} of {qb_rows:,}</strong> retained QuickBooks rows carry "
            f"{period_name} in the first column; the rest belong to other periods."
        )
    render_notice_panel(
        "Source period alignment",
        (
            f"<strong>Selected scope:</strong> {html.escape(period_label)}<br>"
            f"<strong>QuickBooks dates:</strong> {html.escape(coverage(qb_summary))}<br>"
            f"<strong>Infinium dates:</strong> {html.escape(coverage(inf_summary))}<br>"
            f"{agreement}<br>{period_rows}"
        ),
        tone=tone,
        icon="✓" if tone == "success" else "!",
        body_is_html=True,
    )


def render_pre_execution_controls(
    qb_raw: pd.DataFrame,
    inf_raw: pd.DataFrame,
    qb_mapping: dict[str, Optional[str]],
    inf_mapping: dict[str, Optional[str]],
    period_alignment: Optional[dict[str, Any]] = None,
) -> None:
    qb_total = numeric_sum(qb_raw[qb_mapping["amount"]])
    inf_total = numeric_sum(inf_raw[inf_mapping["amount"]])
    render_section_heading(
        "Source verification",
        "Confirm the retained row counts and source totals before running the reconciliation.",
    )
    cols = st.columns(4)
    with cols[0]:
        render_kpi("QuickBooks rows", f"{len(qb_raw):,}", format_currency(qb_total))
    with cols[1]:
        render_kpi("Infinium rows", f"{len(inf_raw):,}", format_currency(inf_total))
    with cols[2]:
        render_kpi("Row difference", f"{len(qb_raw) - len(inf_raw):,}", "QB minus Infinium")
    with cols[3]:
        render_kpi("Source value difference", format_currency(qb_total - inf_total), "QB minus Infinium")
    st.caption(
        "Pre-reconciliation control totals include only QuickBooks rows populated in every field other than Quantity and Amount. "
        "Retained rows with invalid amounts are flagged and remain unreconciled."
    )
    if period_alignment is not None:
        render_source_period_alignment(qb_rows=len(qb_raw), **period_alignment)


_COUNT_CHECK = re.compile(r"row completeness|source quickbooks rows|source row", re.IGNORECASE)


def render_controls_table(controls: pd.DataFrame) -> None:
    """Controls as a wrapping table: counts as integers, money with separators and cents."""
    def fmt(check: str, value: Any) -> str:
        number = float(value)
        if _COUNT_CHECK.search(check):
            return f"{int(round(number)):,}"
        return f"{number:,.2f}"

    rows = []
    for record in controls.to_dict("records"):
        check = str(record["Check"])
        status = str(record["Status"])
        rows.append(
            "<tr>"
            f"<td>{html.escape(check)}</td>"
            f'<td class="num">{fmt(check, record["Expected"])}</td>'
            f'<td class="num">{fmt(check, record["Actual"])}</td>'
            f'<td class="num">{fmt(check, record["Difference"])}</td>'
            f"<td>{status_badge(status)}</td>"
            "</tr>"
        )
    st.markdown(
        '<table class="rec-controls-table"><thead><tr>'
        "<th>Check</th><th>Expected</th><th>Actual</th><th>Difference</th><th>Status</th>"
        f"</tr></thead><tbody>{''.join(rows)}</tbody></table>",
        unsafe_allow_html=True,
    )


def render_row_accounting(metrics: dict[str, Any]) -> None:
    """Spell out how the retained QuickBooks rows divide into final dispositions."""
    qb_rows = int(metrics["QuickBooks Rows"])
    parts = [
        ("matched", int(metrics.get("Final Disposition - Matched Rows", 0))),
        ("true unmatched", int(metrics.get("Final Disposition - True Unmatched Rows", 0))),
        ("review holds", int(metrics.get("Final Disposition - Review Hold Rows", 0))),
        ("confirmed duplicates excluded", int(metrics.get("Final Disposition - Duplicate Excluded Rows", 0))),
    ]
    total = sum(count for _, count in parts)
    equation = " + ".join(f"{count:,} {label}" for label, count in parts)
    tie = "ties exactly" if total == qb_rows else f"does <strong>not</strong> tie (off by {qb_rows - total:,})"
    render_notice_panel(
        "How the rows add up",
        (
            f"<strong>QuickBooks:</strong> {equation} = {total:,}, which {tie} to the "
            f"{qb_rows:,} retained QuickBooks source rows. Every row has exactly one final disposition.<br>"
            "<strong>Counts are source rows, not match groups:</strong> one match group can pair several rows. "
            f"{int(metrics['Matched QuickBooks Rows']):,} QuickBooks and "
            f"{int(metrics['Matched Infinium Rows']):,} Infinium rows are matched, out of "
            f"{int(metrics['Infinium Rows']):,} Infinium source rows. "
            f"{int(metrics['Unmatched Infinium Rows']):,} Infinium-only exceptions are reported separately "
            "and never offset the journal entry."
        ),
        tone="info",
        icon="i",
        body_is_html=True,
    )


def _workbook_timing_text(timing: dict[str, float]) -> str:
    text = f"Workbook built in {timing['build']:.2f} s"
    if "page" in timing:
        text += f" · page refreshed in {timing['page']:.2f} s"
    return text


def render_friendly_error(title: str, what_to_do: str, exc: Optional[BaseException] = None) -> None:
    """Show a plain-language problem and the next step; the raw error stays one click away.

    Display only: callers keep their own `return`, so a failure still stops the run exactly as before.
    """
    render_notice_panel(title, what_to_do, tone="danger", icon="!")
    if exc is not None:
        with st.expander("Technical details (for support)"):
            st.code(f"{type(exc).__name__}: {exc}")


def render_run_summary(result: "ReconciliationResult") -> None:
    """One plain-language banner above the result tabs: what happened and what to do next.

    Only restates figures already in `result.metrics`; it computes nothing new.
    """
    m = result.metrics
    qb_rows = int(m["QuickBooks Rows"])
    matched = int(m["Matched QuickBooks Rows"])
    unresolved = int(m["Unresolved QuickBooks Rows"])
    unmatched_inf = int(m["Unmatched Infinium Rows"])
    je_amount = format_currency(m["Proposed JE Amount"])
    controls_pass = m["Control Status"] == "PASS"
    review_hold = m.get("Posting Status", "READY TO POST") == "REVIEW REQUIRED"

    if not controls_pass:
        headline, tone, icon = "Controls failed: do not post yet", "danger", "!"
        next_step = "Open the Overview tab to see which control failed. Downloads stay locked until every control passes."
    elif review_hold:
        headline, tone, icon = "Reconciled, with items on review hold", "warning", "!"
        next_step = "Resolve the held items in the Exception Review tab, then download the workpaper."
    elif unresolved:
        headline, tone, icon = "Reconciled: some items need a journal entry", "warning", "!"
        next_step = "Review the unresolved items in the Exception Review tab, then download the workpaper."
    else:
        headline, tone, icon = "Fully reconciled: every row matched", "success", "✓"
        next_step = "Go to the Downloads tab to prepare the workpaper."

    body = (
        f"{matched:,} of {qb_rows:,} QuickBooks rows matched exactly to the cent. "
        f"{unresolved:,} QuickBooks row(s) unresolved (proposed journal entry {je_amount}); "
        f"{unmatched_inf:,} Infinium row(s) unmatched. Next: {next_step}"
    )
    render_notice_panel(headline, body, tone=tone, icon=icon)


def render_result(result: ReconciliationResult, run_started: Optional[float] = None) -> None:
    metrics = result.metrics
    control_status = metrics["Control Status"]
    if control_status == "PASS":
        show_toast_once(
            f"reconciliation_complete_{result.run_id}",
            f"Reconciliation complete. All controls passed. Run ID: {result.run_id}",
        )
    else:
        st.error("Reconciliation completed with failed controls. Downloads are withheld until controls pass.")

    render_run_summary(result)

    posting_status = metrics.get("Posting Status", "READY TO POST")
    if posting_status == "REVIEW REQUIRED":
        review_hold_qb = int(metrics.get("Final Disposition - Review Hold Rows", 0))
        review_hold_amount = metrics.get("Final Disposition - Review Hold Amount", 0.0)
        render_notice_panel(
            "Review required before posting",
            (
                f"{review_hold_qb:,} QuickBooks record(s) totaling {format_currency(review_hold_amount)} are "
                "on review hold: they could not be safely matched but have duplicate, amount, or reference "
                "evidence in Infinium, so they are excluded from the proposed journal entry pending a "
                "documented human disposition -- see the Unresolved Exceptions sheet in the downloads."
                if review_hold_qb
                else "One or more posting blockers remain -- see the Posting Summary sheet in the downloads."
            ),
            tone="warning",
            icon="!",
        )

    failed_controls = int(result.controls["Status"].ne("PASS").sum())
    invalid_amount_rows = int(
        metrics["Invalid QuickBooks Amounts"] + metrics["Invalid Infinium Amounts"]
    )
    unmatched_qb = int(metrics["Unresolved QuickBooks Rows"])
    unmatched_inf = int(metrics["Unmatched Infinium Rows"])
    # Every row in duplicate_analysis/infinium_duplicate_analysis is, by
    # construction, excluded from matching and needs review -- there is no
    # "resolved via a more specific key" status anymore (duplicates are
    # removed from the working population before matching ever runs).
    unresolved_duplicate_groups = int(metrics.get("Duplicate QuickBooks Rows", 0)) + int(
        metrics.get("Duplicate Infinium Rows", 0)
    )

    tab_labels = [
        attention_tab_label("Overview", failed_controls + invalid_amount_rows),
        attention_tab_label("Reconciled View", unmatched_qb + unmatched_inf),
        attention_tab_label("Exception Review", unmatched_qb),
        attention_tab_label("Analytics", unresolved_duplicate_groups),
        "Downloads",
    ]
    overview_tab, reconciled_tab, exceptions_tab, analytics_tab, downloads_tab = st.tabs(
        tab_labels
    )
    with overview_tab:
        cols = st.columns(4)
        with cols[0]:
            render_kpi(
                "QB match rate",
                f"{metrics['QuickBooks Match Rate by Row']:.1%}",
                f"{metrics['Matched QuickBooks Rows']:,} of {metrics['QuickBooks Rows']:,} QuickBooks source rows",
            )
        with cols[1]:
            render_kpi(
                "Proposed JE (true unmatched)",
                format_currency(metrics["Proposed JE Amount"]),
                f"{metrics['Unresolved QuickBooks Rows']:,} transactions",
            )
        with cols[2]:
            render_kpi(
                "Matched control difference",
                format_currency(metrics["Matched Amount Difference"]),
                "QuickBooks minus Infinium",
            )
        with cols[3]:
            render_kpi(
                "Automated controls",
                status_badge(control_status),
                f"{len(result.controls)} required controls",
            )
        review_holds = int(metrics.get("Final Disposition - Review Hold Rows", 0))
        if posting_status == "REVIEW REQUIRED":
            pending = f"{review_holds:,} hold(s)" if review_holds else "posting blockers remain"
            st.markdown(
                f'<div class="rec-review-status"><strong>Review status: Pending — {pending}.</strong> '
                "Passing automated controls does not approve this reconciliation; "
                "a person must disposition the items on hold before posting.</div>",
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                '<div class="rec-review-status clear"><strong>Review status: No holds.</strong> '
                "No review holds or posting blockers were raised.</div>",
                unsafe_allow_html=True,
            )
        render_row_accounting(metrics)
        st.markdown("#### Required controls")
        render_controls_table(result.controls)
        if metrics.get("Historical Clearances", 0):
            render_notice_panel(
                "Historical timing differences cleared",
                (
                    f"Historical secondary data cleared {metrics['Historical Clearances']:,} "
                    "opposing-primary exception item(s). "
                    f"{metrics['QuickBooks Secondary Rows Ignored'] + metrics['Infinium Secondary Rows Ignored']:,} "
                    "unused secondary row(s) were excluded from exception reporting."
                ),
                tone="success",
                icon="✓",
            )
        if metrics["Invalid QuickBooks Amounts"] or metrics["Invalid Infinium Amounts"]:
            st.markdown(
                '<div class="data-quality-banner">Data quality review: '
                f"{metrics['Invalid QuickBooks Amounts']} QuickBooks and "
                f"{metrics['Invalid Infinium Amounts']} Infinium rows have invalid or missing amounts. "
                "Those rows were not automatically matched.</div>",
                unsafe_allow_html=True,
            )

    with reconciled_tab:
        st.caption(
            "Matched rows appear first. Unmatched QuickBooks and unmatched Infinium rows are kept in separate "
            "sections so unrelated exceptions are never displayed as a pair. Historical clearances display the "
            "specific accepted prior-period row on the opposing side and label it in Record Context. Unused "
            "historical rows are never added to this ledger."
        )
        render_limited_dataframe(display_primary_preview(result), height=520)

    with exceptions_tab:
        unresolved = result.qb_work.loc[result.unmatched_qb, list(result.qb_raw.columns)].copy()
        if not result.candidates.empty:
            candidate_map = result.candidates.set_index("QuickBooks Row ID").to_dict("index")
            unresolved["Exception Status"] = [
                candidate_map.get(result.qb_work.at[idx, QB_ID], {}).get(
                    "Disposition", "Unmatched QuickBooks"
                )
                for idx in result.unmatched_qb
            ]
            unresolved["Reference Amount Difference"] = [
                candidate_map.get(result.qb_work.at[idx, QB_ID], {}).get(
                    "Minimum Amount Difference"
                )
                for idx in result.unmatched_qb
            ]
        if unresolved.empty:
            st.caption("No unresolved QuickBooks exceptions remain.")
        else:
            render_limited_dataframe(unresolved, height=440)
        st.metric("Proposed journal-entry support total", format_currency(metrics["Proposed JE Amount"]))
        st.caption(
            "Only TRUE UNMATCHED QuickBooks transactions -- no Infinium support after every matching pass -- "
            "feed this net signed amount. Review credits and reversals before posting."
        )
        held = result.qb_dispositions.loc[result.qb_dispositions["Final Disposition"] == "REVIEW_HOLD"]
        if not held.empty:
            st.markdown("#### Review holds (excluded from the journal entry)")
            render_limited_dataframe(
                held[["Review ID", "QBO Row ID", "Amount", "Final Reason", "Related Match Ref.", "Related Infinium Row IDs"]],
                height=260,
            )
        st.markdown("#### Final disposition of every QuickBooks row")
        disposition_summary = (
            result.qb_dispositions.groupby("Final Disposition", as_index=False)
            .agg(Rows=("QBO Row ID", "count"), Amount=("Amount", "sum"))
        )
        st.dataframe(
            status_styler(disposition_summary, ["Final Disposition"], formats={"Rows": "{:,}", "Amount": "{:,.2f}"}),
            width="stretch", hide_index=True,
        )

    with analytics_tab:
        left, right = st.columns(2)
        with left:
            st.markdown("#### Match-method distribution")
            st.dataframe(result.method_summary, width="stretch", hide_index=True)
        with right:
            st.markdown("#### Exception analysis")
            st.dataframe(result.exception_analysis, width="stretch", hide_index=True)
        with st.expander("Normalization and assessment detail", expanded=False):
            st.dataframe(result.assessments, width="stretch", hide_index=True, height=320)
    with downloads_tab:
        export_result = render_review_panel(result)
        st.markdown("#### Accounting workpaper")
        st.caption(
            "Six sheets: Posting Summary, Reconciliation Detail, Unresolved Exceptions, Aggregates, "
            "Raw Data, and Audit & Controls."
        )
        if "primary_workbook" not in st.session_state:
            if st.button(
                "Prepare Sales Reconciliation",
                type="primary",
                width="stretch",
                key=f"prepare_primary_{result.run_id}",
            ):
                try:
                    build_started = time.perf_counter()
                    with st.spinner("Preparing the accounting workpaper..."):
                        st.session_state.primary_workbook = build_primary_workbook(export_result)
                    st.session_state.primary_workbook_timing = {
                        "build": time.perf_counter() - build_started,
                    }
                    st.toast("Accounting workpaper prepared.", icon="✅")
                except Exception as exc:
                    _render_workbook_exception("accounting workpaper", exc)
        if "primary_workbook" in st.session_state:
            st.download_button(
                "Download Sales Reconciliation",
                data=st.session_state.primary_workbook,
                file_name=f"Sales_Reconciliation_{result.run_id}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                type="primary",
                width="stretch",
            )
        # Filled in at the end of this page run, once the whole run is timed.
        timing_slot = st.empty()
        st.markdown("#### Accountant's legacy format")
        st.caption(
            "A simplified export in the original hand-built layout: QuickBooks left, Infinium right, "
            "matches shaded green. Three sheets: Legacy Reconciliation, Exceptions (by fiscal period), "
            "and Product Aggregate Summary."
        )
        if "legacy_workbook" not in st.session_state:
            if st.button(
                "Prepare Legacy Format",
                width="stretch",
                key=f"prepare_legacy_{result.run_id}",
            ):
                try:
                    with st.spinner("Preparing the legacy-format workbook..."):
                        st.session_state.legacy_workbook = build_legacy_workbook(result)
                    st.toast("Legacy-format workbook prepared.", icon="✅")
                except Exception as exc:
                    _render_workbook_exception("legacy-format workbook", exc)
        if "legacy_workbook" in st.session_state:
            st.download_button(
                "Download Legacy Format",
                data=st.session_state.legacy_workbook,
                file_name=f"Sales_Reconciliation_Legacy_{result.run_id}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                width="stretch",
            )

    # The run that built the workpaper also re-rendered every tab above; its
    # total, measured here at the end, shows how much of the wait was the
    # build itself. Later runs keep showing that first measurement.
    timing = st.session_state.get("primary_workbook_timing")
    if timing is not None:
        if "page" not in timing and run_started is not None:
            timing["page"] = time.perf_counter() - run_started
        timing_slot.caption(_workbook_timing_text(timing))
