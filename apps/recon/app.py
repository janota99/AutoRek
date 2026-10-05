"""Professional QuickBooks-to-Infinium sales reconciliation.

This is one page of the combined app. Run from the repository root with:
    streamlit run app.py

This module is the application layer only: top-level Streamlit page flow
and orchestration of ingestion, matching, and workbook preparation. It
intentionally contains no styling, sheet-building, or generic-helper code
of its own -- that logic lives in dedicated modules so this file stays
focused on "what happens, in what order":

    config.py          Global color palette and shared constants.
    utils.py           Generic data-sanitization and formatting helpers.
    ingestion.py       Source file reading, header detection, column mapping.
    uploads.py         File hashing, cached ingestion wrappers, upload summaries.
    matching/          Normalization, matching, and analytical rules (a package).
    duplicates.py      Duplicate detection, classification, and disposition.
    excel_styles.py    OpenPyXL formatting primitives (borders, bands, autofit).
    workpapers/        Excel workbook/sheet builders (a package).
    ui_components.py   Streamlit UI blocks that consume workpapers + utils.

The app produces one accounting workpaper from one controlled reconciliation
run, plus an optional simplified export:
    1. Sales_Reconciliation_<run>.xlsx
       Posting Summary, Reconciliation Detail, Unresolved Exceptions,
       Aggregates, and Raw Data.
    2. Sales_Reconciliation_Legacy_<run>.xlsx (optional)
       A simplified accountant's legacy-format export.
"""

from __future__ import annotations

import hashlib
import time
from datetime import datetime
from typing import Optional, Any

import pandas as pd
import streamlit as st

from apps.recon.config import CENTRAL_TIMEZONE
from apps.recon.ingestion import (
    detect_header_row,
    list_source_sheets,
    mapping_panel,
    read_source_file,
    secondary_mapping_panel,
)
from apps.recon.matching import APP_VERSION, MATCHING_RULE_VERSION, build_reconciliation
from apps.recon.vendor_aliases import load_vendor_aliases
from apps.recon.ui_components import (
    load_app_css,
    render_friendly_error,
    render_ingestion_flow,
    render_notice_panel,
    render_pre_execution_controls,
    render_result,
    render_section_heading,
    render_source_status,
    render_upload_source_heading,
    show_toast_once,
)
from apps.recon.utils import (
    clear_prepared_workbooks,
    clear_results_if_signature_changed,
    format_central_timestamp,
    run_id_for_inputs,
)
from apps.recon.assets.ui_assets import INFOR_LOGO_URI, QUICKBOOKS_LOGO_URI
from shared.sample_data import sample_downloads, sample_uploader
from apps.recon.uploads import (
    render_uploaded_dataset_summary,
    cached_build_source_validation_report,
    cached_filter_qb_subtotal_rows,
    cached_fiscal_period_column_profile,
    cached_dataset_summary,
    get_true_file_hash,
)


# ---------------------------------------------------------------------------
# Main Application Flow
# ---------------------------------------------------------------------------

def main() -> None:
    # For the Downloads tab's "page refreshed in" timing readout.
    run_started = time.perf_counter()
    # Page title and wide layout come from st.navigation in the root app.py.
    load_app_css()
    st.markdown(
        """
        <div class="rec-title">
            <h1>Sales Reconciliation</h1>
            <p>QuickBooks-to-Infinium matching, data summarization, and reconciliation analytics</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # Keep the single progress indicator above every upload control even though
    # its final state is determined later in the application run.
    progress_container = st.container()

    render_section_heading(
        "Upload data sources",
        "Add the two current-period exports required for reconciliation.",
    )
    upload_col1, upload_col2 = st.columns(2, gap="medium")

    with upload_col1:
        with st.container(border=True):
            render_upload_source_heading(
                "Upload QuickBooks Data",
                QUICKBOOKS_LOGO_URI,
                "quickbooks",
                context="Primary source",
                required=True,
            )
            st.markdown(
                '<p class="upload-card-copy">Current-period sales detail used as the QuickBooks side of the reconciliation.</p>',
                unsafe_allow_html=True,
            )
            qb_file, _ = sample_uploader(
                "Upload primary QuickBooks sales data",
                key="qb_file", sample_file="recon_quickbooks_sales.xlsx",
                types=["xlsx", "csv"],
                help="Drag and drop or click to upload the current QuickBooks sales export.",
            )
            render_source_status(
                qb_file.name if qb_file else None,
                "Awaiting QuickBooks file",
            )
            render_uploaded_dataset_summary(qb_file, "QB", "qb_sheet")

    with upload_col2:
        with st.container(border=True):
            render_upload_source_heading(
                "Upload Infinium Data",
                INFOR_LOGO_URI,
                "infor",
                context="Primary source",
                required=True,
            )
            st.markdown(
                '<p class="upload-card-copy">Current-period sales detail used as the Infinium side of the reconciliation.</p>',
                unsafe_allow_html=True,
            )
            inf_file, _ = sample_uploader(
                "Upload primary Infinium sales data",
                key="inf_file", sample_file="recon_infinium_sales.xlsx",
                types=["xlsx", "csv"],
                help="Drag and drop or click to upload the current Infinium sales export.",
            )
            render_source_status(
                inf_file.name if inf_file else None,
                "Awaiting Infinium file",
            )
            render_uploaded_dataset_summary(inf_file, "INF", "inf_sheet")

    sample_downloads([
        ("QuickBooks sales (.xlsx)", "recon_quickbooks_sales.xlsx"),
        ("Infinium sales (.xlsx)", "recon_infinium_sales.xlsx"),
        ("Historical QuickBooks (.xlsx)", "recon_quickbooks_historical.xlsx"),
        ("Historical Infinium (.xlsx)", "recon_infinium_historical.xlsx"),
    ], key="recon")

    # These variables must exist on every Streamlit rerun, including before
    # both primary files have been uploaded.
    qb_secondary_file: Optional[Any] = None
    inf_secondary_file: Optional[Any] = None

    if qb_file and inf_file:
        st.markdown('<div class="section-spacer"></div>', unsafe_allow_html=True)
        with st.expander("Optional: Upload Historical Data", expanded=False):
            st.markdown(
                '<p class="historical-upload-intro">Use prior-period files only to clear '
                'timing differences in the opposing primary source. Unused historical rows '
                'remain background data and never generate exceptions.</p>',
                unsafe_allow_html=True,
            )
            sec_col1, sec_col2 = st.columns(2, gap="medium")

            with sec_col1:
                with st.container(border=True):
                    render_upload_source_heading(
                        "Historical QuickBooks",
                        QUICKBOOKS_LOGO_URI,
                        "quickbooks",
                        context="Optional prior-period source",
                    )
                    st.markdown(
                        '<p class="upload-card-copy">May clear unresolved primary Infinium items; unused rows are excluded.</p>',
                        unsafe_allow_html=True,
                    )
                    qb_secondary_file, _ = sample_uploader(
                        "Upload historical QuickBooks data",
                        key="qb_secondary_file", sample_file="recon_quickbooks_historical.xlsx",
                        types=["xlsx", "csv"],
                        help="Drag and drop or click to upload optional historical QuickBooks data.",
                    )
                    if qb_secondary_file:
                        render_source_status(qb_secondary_file.name, "")
                        render_uploaded_dataset_summary(
                            qb_secondary_file, "QB", "qb_secondary_sheet"
                        )

            with sec_col2:
                with st.container(border=True):
                    render_upload_source_heading(
                        "Historical Infinium",
                        INFOR_LOGO_URI,
                        "infor",
                        context="Optional prior-period source",
                    )
                    st.markdown(
                        '<p class="upload-card-copy">May clear unresolved primary QuickBooks items; unused rows are excluded.</p>',
                        unsafe_allow_html=True,
                    )
                    inf_secondary_file, _ = sample_uploader(
                        "Upload historical Infinium data",
                        key="inf_secondary_file", sample_file="recon_infinium_historical.xlsx",
                        types=["xlsx", "csv"],
                        help="Drag and drop or click to upload optional historical Infinium data.",
                    )
                    if inf_secondary_file:
                        render_source_status(inf_secondary_file.name, "")
                        render_uploaded_dataset_summary(
                            inf_secondary_file, "INF", "inf_secondary_sheet"
                        )

    st.markdown('<div class="major-section-gap"></div>', unsafe_allow_html=True)

    # Shift configuration cleanly into the sidebar
    st.sidebar.markdown("## Configuration Settings")

    if qb_file is None or inf_file is None:
        # Settings that don't depend on the files are usable before the upload. They are remembered
        # under plain (non-widget) keys because Streamlit drops the state of widgets that are not
        # rendered, and the post-upload controls below start from these values.
        st.sidebar.markdown("### Reconciliation settings")
        st.session_state["recon_fiscal_year"] = int(st.sidebar.number_input(
            "Fiscal year", min_value=1900, max_value=2199,
            value=st.session_state.get("recon_fiscal_year", datetime.now(CENTRAL_TIMEZONE).year),
            step=1, key="pre_fiscal_year",
            help="Optional input used only for fiscal period identification. This field never restricts matching.",
        ))
        pre_period = st.sidebar.selectbox(
            "Current fiscal period", options=[None, *range(1, 14)],
            index=st.session_state.get("recon_fiscal_period_index", min(datetime.now(CENTRAL_TIMEZONE).month, 13)),
            format_func=lambda value: "All periods" if value is None else f"Period {value:02d}",
            key="pre_fiscal_period",
            help="Limits the Product Aggregate Summary to this period. Transaction matching stays unrestricted.",
        )
        st.session_state["recon_fiscal_period_index"] = 0 if pre_period is None else pre_period
        st.sidebar.caption("Column mapping and worksheet choices appear here once both files are uploaded.")

        st.session_state.pop("reconciliation_result", None)
        clear_prepared_workbooks()

        with progress_container:
            render_ingestion_flow(
                qb_loaded=qb_file is not None,
                inf_loaded=inf_file is not None,
                mappings_ready=False,
                reconciliation_complete=False,
            )

        st.markdown(
            '<p class="matching-summary">Amounts must agree exactly to the signed cent. '
            'Ambiguous matches are never forced: they stay unresolved and available for review.</p>',
            unsafe_allow_html=True,
        )
        with st.expander("How matching works"):
            st.markdown(
                "Matching runs in this order, and each rule needs the amounts to agree exactly:\n\n"
                "1. Unique PO + invoice + exact signed amount\n"
                "2. Unique PO + exact signed amount\n"
                "3. Unique invoice + exact signed amount\n"
                "4. Unique grouped aggregate by PO and/or invoice, after one-to-one matching\n\n"
                "Ambiguous combinations and invalid values remain unresolved for review. "
                "Files are processed within the active application session."
            )
        return

    # The guard above guarantees that both required UploadedFile objects are
    # available before any attempt is made to read their bytes.
    qb_bytes = qb_file.getvalue()
    inf_bytes = inf_file.getvalue()
    qb_secondary_bytes = (
        qb_secondary_file.getvalue()
        if qb_secondary_file is not None
        else None
    )
    inf_secondary_bytes = (
        inf_secondary_file.getvalue()
        if inf_secondary_file is not None
        else None
    )
    
    qb_hash = get_true_file_hash(qb_file)
    inf_hash = get_true_file_hash(inf_file)
    qb_secondary_hash = get_true_file_hash(qb_secondary_file)
    inf_secondary_hash = get_true_file_hash(inf_secondary_file)
    
    try:
        qb_sheets = list_source_sheets(qb_bytes, qb_file.name)
        inf_sheets = list_source_sheets(inf_bytes, inf_file.name)
        qb_secondary_sheets = (
            list_source_sheets(qb_secondary_bytes, qb_secondary_file.name)
            if qb_secondary_bytes and qb_secondary_file else ()
        )
        inf_secondary_sheets = (
            list_source_sheets(inf_secondary_bytes, inf_secondary_file.name)
            if inf_secondary_bytes and inf_secondary_file else ()
        )
    except Exception as exc:
        render_friendly_error("We couldn't open one of your files", "Check that each upload is an unprotected .xlsx, .xlsm, or .csv export and not a corrupted or password-protected file, then upload it again.", exc)
        return

    qb_sheet_name: Optional[str] = qb_sheets[0] if qb_sheets else None
    inf_sheet_name: Optional[str] = inf_sheets[0] if inf_sheets else None
    qb_secondary_sheet_name: Optional[str] = (
        qb_secondary_sheets[0] if qb_secondary_sheets else None
    )
    inf_secondary_sheet_name: Optional[str] = (
        inf_secondary_sheets[0] if inf_secondary_sheets else None
    )
    if any(len(sheets) > 1 for sheets in (
        qb_sheets, inf_sheets, qb_secondary_sheets, inf_secondary_sheets
    )):
        st.sidebar.markdown("### Worksheet selection")
    if len(qb_sheets) > 1:
        st.sidebar.warning(
            f"The QuickBooks workbook contains {len(qb_sheets)} worksheets. Select the worksheet containing the reconciliation data."
        )
        qb_sheet_name = st.sidebar.selectbox(
            "QuickBooks worksheet",
            options=list(qb_sheets),
            key=f"qb_sheet_{qb_hash[:12]}",
        )
    if len(inf_sheets) > 1:
        st.sidebar.warning(
            f"The Infinium workbook contains {len(inf_sheets)} worksheets. Select the worksheet containing the reconciliation data."
        )
        inf_sheet_name = st.sidebar.selectbox(
            "Infinium worksheet",
            options=list(inf_sheets),
            key=f"inf_sheet_{inf_hash[:12]}",
        )
    if len(qb_secondary_sheets) > 1:
        qb_secondary_sheet_name = st.sidebar.selectbox(
            "QuickBooks secondary worksheet",
            options=list(qb_secondary_sheets),
            key=f"qb_secondary_sheet_{qb_secondary_hash[:12]}",
        )
    if len(inf_secondary_sheets) > 1:
        inf_secondary_sheet_name = st.sidebar.selectbox(
            "Infinium secondary worksheet",
            options=list(inf_secondary_sheets),
            key=f"inf_secondary_sheet_{inf_secondary_hash[:12]}",
        )

    qb_sheet_key = hashlib.sha256((qb_sheet_name or "CSV").encode()).hexdigest()[:8]
    inf_sheet_key = hashlib.sha256((inf_sheet_name or "CSV").encode()).hexdigest()[:8]
    qb_secondary_sheet_key = hashlib.sha256(
        (qb_secondary_sheet_name or "CSV").encode()
    ).hexdigest()[:8]
    inf_secondary_sheet_key = hashlib.sha256(
        (inf_secondary_sheet_name or "CSV").encode()
    ).hexdigest()[:8]
    key_suffix = (
        f"{qb_hash[:8]}_{qb_sheet_key}_{inf_hash[:8]}_{inf_sheet_key}_"
        f"{qb_secondary_hash[:8]}_{qb_secondary_sheet_key}_"
        f"{inf_secondary_hash[:8]}_{inf_secondary_sheet_key}"
    )
    try:
        detected_qb_header = detect_header_row(
            qb_bytes, qb_file.name, "QB", qb_sheet_name
        )
        detected_inf_header = detect_header_row(
            inf_bytes, inf_file.name, "INF", inf_sheet_name
        )
        detected_qb_secondary_header = (
            detect_header_row(
                qb_secondary_bytes, qb_secondary_file.name, "QB",
                qb_secondary_sheet_name,
            )
            if qb_secondary_bytes and qb_secondary_file else None
        )
        detected_inf_secondary_header = (
            detect_header_row(
                inf_secondary_bytes, inf_secondary_file.name, "INF",
                inf_secondary_sheet_name,
            )
            if inf_secondary_bytes and inf_secondary_file else None
        )
    except Exception as exc:
        render_friendly_error("We couldn't read the selected worksheet", "Pick a different worksheet in the sidebar, or re-export the file so the data sits on a normal sheet.", exc)
        return

    st.sidebar.markdown("### Import settings")
    with st.sidebar.expander("Header rows", expanded=False):
        st.caption("Excel row numbers are one-based. The detected rows can be overridden if needed.")
        qb_header_number = int(
            st.number_input("QuickBooks header row", min_value=1, max_value=100,
                            value=detected_qb_header + 1, step=1,
                            key=f"qb_header_{qb_hash[:8]}_{qb_sheet_key}")
        )
        inf_header_number = int(
            st.number_input("Infinium header row", min_value=1, max_value=100,
                            value=detected_inf_header + 1, step=1,
                            key=f"inf_header_{inf_hash[:8]}_{inf_sheet_key}")
        )
        qb_secondary_header_number = (
            int(st.number_input(
                "QuickBooks secondary header row", min_value=1, max_value=100,
                value=int(detected_qb_secondary_header) + 1, step=1,
                key=f"qb_secondary_header_{qb_secondary_hash[:8]}_{qb_secondary_sheet_key}",
            ))
            if detected_qb_secondary_header is not None else None
        )
        inf_secondary_header_number = (
            int(st.number_input(
                "Infinium secondary header row", min_value=1, max_value=100,
                value=int(detected_inf_secondary_header) + 1, step=1,
                key=f"inf_secondary_header_{inf_secondary_hash[:8]}_{inf_secondary_sheet_key}",
            ))
            if detected_inf_secondary_header is not None else None
        )

    try:
        qb_raw = read_source_file(
            qb_bytes, qb_file.name, qb_header_number - 1, qb_sheet_name
        )
        inf_raw = read_source_file(
            inf_bytes, inf_file.name, inf_header_number - 1, inf_sheet_name
        )
        qb_secondary_raw = (
            read_source_file(
                qb_secondary_bytes,
                qb_secondary_file.name,
                int(qb_secondary_header_number) - 1,
                qb_secondary_sheet_name,
            )
            if qb_secondary_bytes and qb_secondary_file
            and qb_secondary_header_number is not None else None
        )
        inf_secondary_raw = (
            read_source_file(
                inf_secondary_bytes,
                inf_secondary_file.name,
                int(inf_secondary_header_number) - 1,
                inf_secondary_sheet_name,
            )
            if inf_secondary_bytes and inf_secondary_file
            and inf_secondary_header_number is not None else None
        )
    except Exception as exc:
        render_friendly_error("The header row looks wrong", "Change the header row number in the sidebar to the row that holds the column names (PO, invoice, amount).", exc)
        return
    if qb_raw.empty or inf_raw.empty:
        render_friendly_error("No data found under the header row", "Both files need at least one data row below the header row. Check the header row number in the sidebar, or that the right file was uploaded.")
        return
    if qb_secondary_raw is not None and qb_secondary_raw.empty:
        render_friendly_error("The prior-period QuickBooks file looks empty", "Check its header row number, or remove the file: prior-period files are optional.")
        return
    if inf_secondary_raw is not None and inf_secondary_raw.empty:
        render_friendly_error("The prior-period Infinium file looks empty", "Check its header row number, or remove the file: prior-period files are optional.")
        return
    qb_import_audit = dict(qb_raw.attrs.get("ingestion_audit", {}))
    inf_import_audit = dict(inf_raw.attrs.get("ingestion_audit", {}))
    qb_secondary_import_audit = (
        dict(qb_secondary_raw.attrs.get("ingestion_audit", {}))
        if qb_secondary_raw is not None else {}
    )
    inf_secondary_import_audit = (
        dict(inf_secondary_raw.attrs.get("ingestion_audit", {}))
        if inf_secondary_raw is not None else {}
    )

    qb_mapping, inf_mapping = mapping_panel(qb_raw, inf_raw, key_suffix)
    qb_secondary_mapping, inf_secondary_mapping = secondary_mapping_panel(
        qb_secondary_raw, inf_secondary_raw, key_suffix
    )
    st.sidebar.markdown("### Reconciliation settings")
    current_central = datetime.now(CENTRAL_TIMEZONE)
    fiscal_year = int(st.sidebar.number_input(
        "Fiscal year", min_value=1900, max_value=2199,
        value=st.session_state.get("recon_fiscal_year", current_central.year), step=1,
        help="Optional input used only for fiscal period identification. This field never restricts matching.",
        key=f"fiscal_year_{key_suffix}",
    ))
    fiscal_period = st.sidebar.selectbox(
        "Current fiscal period", options=[None, *range(1, 14)],
        index=st.session_state.get("recon_fiscal_period_index", min(current_central.month, 13)),
        format_func=lambda value: "All periods" if value is None else f"Period {value:02d}",
        help=(
            "When selected, the Product Aggregate Summary includes only primary QuickBooks "
            "rows whose first-column period equals this value. Transaction matching remains unrestricted."
        ),
        key=f"fiscal_period_{key_suffix}",
    )
    st.session_state["recon_fiscal_year"] = fiscal_year
    st.session_state["recon_fiscal_period_index"] = 0 if fiscal_period is None else fiscal_period

    if fiscal_period is not None and len(qb_raw.columns):
        # The primary QuickBooks first column is the authoritative fiscal-period
        # field for aggregate filtering and exception-period reporting.
        qb_mapping["period"] = str(qb_raw.columns[0])

    required_values = [qb_mapping.get(key) for key in ("po", "invoice", "amount")] + [
        inf_mapping.get(key) for key in ("po", "invoice", "amount", "period")
    ]
    if qb_secondary_mapping is not None:
        required_values.extend(qb_secondary_mapping.get(key) for key in ("po", "invoice", "amount"))
    if inf_secondary_mapping is not None:
        required_values.extend(inf_secondary_mapping.get(key) for key in ("po", "invoice", "amount"))
    mappings_ready = not any(value is None for value in required_values)
    signature = (
        qb_hash, inf_hash, qb_secondary_hash, inf_secondary_hash,
        qb_sheet_name, inf_sheet_name, qb_secondary_sheet_name, inf_secondary_sheet_name,
        qb_header_number, inf_header_number, qb_secondary_header_number, inf_secondary_header_number,
        tuple(qb_mapping.items()), tuple(inf_mapping.items()), fiscal_year, fiscal_period,
        tuple((qb_secondary_mapping or {}).items()),
        tuple((inf_secondary_mapping or {}).items()),
        APP_VERSION, MATCHING_RULE_VERSION,
    )
    clear_results_if_signature_changed(signature)
    reconciliation_complete = "reconciliation_result" in st.session_state

    if not mappings_ready:
        with progress_container:
            render_ingestion_flow(True, True, False, reconciliation_complete)

        render_friendly_error(
            "Some columns still need to be matched",
            "Pick the PO, invoice, and amount columns for every uploaded file in the sidebar. "
            "The primary Infinium file also needs its fiscal-period column.",
        )
        return

    qb_detail, qb_subtotal_rows_excluded, qb_filter_audit = cached_filter_qb_subtotal_rows(qb_raw, qb_mapping)
    qb_detail.attrs["qb_subtotal_filter_audit"] = qb_filter_audit # Safe restoration
    
    qb_secondary_detail: Optional[pd.DataFrame] = None
    qb_secondary_subtotal_rows_excluded = 0
    if qb_secondary_raw is not None and qb_secondary_mapping is not None:
        qb_secondary_detail, qb_secondary_subtotal_rows_excluded, _ = cached_filter_qb_subtotal_rows(
            qb_secondary_raw, qb_secondary_mapping
        )
        
    if qb_detail.empty:
        with progress_container:
            render_ingestion_flow(
                True, True, True, reconciliation_complete, source_validation_failed=True
            )

        render_friendly_error(
            "No usable QuickBooks transaction rows",
            "Every transaction row must be filled in apart from Quantity and Amount; subtotal and blank rows are "
            "removed automatically. Check that the header row and column mapping are right.",
        )
        return

    first_period_column = str(qb_detail.columns[0])
    period_populated, period_valid, period_strict = cached_fiscal_period_column_profile(
        qb_detail[first_period_column]
    )
    
    if fiscal_period is None:
        period_validation = pd.DataFrame([{
            "Dataset": "QuickBooks",
            "Check": "First-column aggregate fiscal period",
            "Status": "PASS",
            "Details": "No current fiscal period was selected; Product Aggregate Summary includes all primary QuickBooks periods.",
        }])
    else:
        selected_period_rows = int(
            pd.to_numeric(qb_detail[first_period_column], errors="coerce")
            .eq(int(fiscal_period))
            .sum()
        )
        period_validation = pd.DataFrame([{
            "Dataset": "QuickBooks",
            "Check": "First-column aggregate fiscal period",
            "Status": "FAIL" if not period_strict else ("WARNING" if selected_period_rows == 0 else "PASS"),
            "Details": (
                f"{period_valid:,} of {period_populated:,} populated first-column value(s) are valid periods 1-13; "
                f"{selected_period_rows:,} row(s) belong to selected Period {int(fiscal_period):02d}."
            ),
        }])

    validation_frames = [
        cached_build_source_validation_report(qb_detail, qb_mapping, "QB", qb_import_audit),
        period_validation,
        cached_build_source_validation_report(inf_raw, inf_mapping, "INF", inf_import_audit),
    ]
    if qb_secondary_detail is not None and qb_secondary_mapping is not None:
        validation_frames.append(cached_build_source_validation_report(
            qb_secondary_detail,
            qb_secondary_mapping,
            "QB",
            qb_secondary_import_audit,
            require_period=False,
            dataset_label="QuickBooks Secondary (Historical)",
        ))
    if inf_secondary_raw is not None and inf_secondary_mapping is not None:
        validation_frames.append(cached_build_source_validation_report(
            inf_secondary_raw,
            inf_secondary_mapping,
            "INF",
            inf_secondary_import_audit,
            require_period=False,
            dataset_label="Infinium Secondary (Historical)",
        ))
        
    validation_report = pd.concat(validation_frames, ignore_index=True)
    validation_failed = validation_report["Status"].eq("FAIL").any()
    validation_warnings = int(validation_report["Status"].eq("WARNING").sum())

    with progress_container:
        render_ingestion_flow(
            True, True, True, reconciliation_complete,
            source_validation_failed=validation_failed,
        )

    if validation_failed:
        st.error(
            "Source validation failed. Correct the selected header row, required mappings, or malformed source data before matching."
        )
    elif validation_warnings:
        render_notice_panel(
            "Validation completed with review items",
            f"Source validation passed with {validation_warnings:,} review warning(s). Harmless blank columns and repeated header rows were ignored.",
            tone="warning",
            icon="!",
        )
    else:
        show_toast_once(
            f"source_validation_{key_suffix}_{qb_header_number}_{inf_header_number}",
            "Source validation passed. Both datasets are ready for reconciliation.",
        )
    with st.expander("Source validation details", expanded=validation_failed):
        st.dataframe(
    validation_report,
    width="stretch",
    hide_index=True,
)
    if validation_failed:
        return

    def _coverage(file_obj: Any, source: str, sheet_prefix: str, sheet: Optional[str]) -> Optional[dict]:
        try:
            chosen = st.session_state.get(f"{sheet_prefix}_{get_true_file_hash(file_obj)[:12]}", sheet)
            return cached_dataset_summary(file_obj.getvalue(), file_obj.name, source, chosen)
        except Exception:
            return None

    render_pre_execution_controls(
        qb_detail, inf_raw, qb_mapping, inf_mapping,
        period_alignment={
            "qb_summary": _coverage(qb_file, "QB", "qb_sheet", qb_sheet_name),
            "inf_summary": _coverage(inf_file, "INF", "inf_sheet", inf_sheet_name),
            "fiscal_period": fiscal_period,
            "fiscal_year": fiscal_year,
            "selected_period_rows": (
                None if fiscal_period is None else selected_period_rows
            ),
        },
    )
    if qb_secondary_detail is not None or inf_secondary_raw is not None:
        st.caption(
            "Secondary historical scope: "
            f"{len(qb_secondary_detail) if qb_secondary_detail is not None else 0:,} QuickBooks row(s) may clear only primary Infinium exceptions; "
            f"{len(inf_secondary_raw) if inf_secondary_raw is not None else 0:,} Infinium row(s) may clear only primary QuickBooks exceptions. "
            "Unused historical rows are ignored."
        )
    if qb_subtotal_rows_excluded:
        st.caption(
            f"Excluded {qb_subtotal_rows_excluded:,} QuickBooks incomplete or subtotal row(s). "
            "Every retained QuickBooks detail row is populated in all fields other than Quantity and Amount."
        )

    _, run_button_col, _ = st.columns([1.25, 1, 1.25])
    with run_button_col:
        run_requested = st.button("Run Reconciliation", type="primary", width="stretch")
        
    if run_requested:
        run_id, run_timestamp = run_id_for_inputs(
            qb_hash, inf_hash, qb_secondary_hash, inf_secondary_hash
        )
        metadata = {
            "run_id": run_id,
            "run_timestamp_dt": run_timestamp,
            "run_timestamp": format_central_timestamp(run_timestamp),
            "qb_filename": qb_file.name,
            "qb_sheet_name": qb_sheet_name,
            "qb_sha256": qb_hash,
            "inf_filename": inf_file.name,
            "inf_sheet_name": inf_sheet_name,
            "inf_sha256": inf_hash,
            "qb_secondary_filename": qb_secondary_file.name if qb_secondary_file else None,
            "qb_secondary_sheet_name": qb_secondary_sheet_name,
            "qb_secondary_sha256": qb_secondary_hash if qb_secondary_file else None,
            "inf_secondary_filename": inf_secondary_file.name if inf_secondary_file else None,
            "inf_secondary_sheet_name": inf_secondary_sheet_name,
            "inf_secondary_sha256": inf_secondary_hash if inf_secondary_file else None,
            "fiscal_year": fiscal_year,
            "fiscal_period": fiscal_period,
            "qb_subtotal_rows_excluded": qb_subtotal_rows_excluded,
            "qb_subtotal_filter_audit": qb_filter_audit,
            "qb_import_audit": qb_import_audit,
            "inf_import_audit": inf_import_audit,
            "qb_secondary_import_audit": qb_secondary_import_audit,
            "inf_secondary_import_audit": inf_secondary_import_audit,
            "qb_secondary_subtotal_rows_excluded": qb_secondary_subtotal_rows_excluded,
            "source_validation_warnings": validation_warnings,
        }
        try:
            with st.spinner("Reconciling source rows and validating accounting controls..."):
                result = build_reconciliation(
                    qb_detail, inf_raw, qb_mapping, inf_mapping, metadata,
                    fiscal_year,
                    qb_secondary_detail,
                    inf_secondary_raw,
                    qb_secondary_mapping,
                    inf_secondary_mapping,
                    vendor_aliases=load_vendor_aliases(),
                )
            st.session_state.reconciliation_result = result
            # Workbook bytes are generated only from the Downloads tab. This
            # keeps the reconciliation action focused on matching and controls.
            clear_prepared_workbooks()
            st.rerun()
        except Exception as exc:
            render_friendly_error("Reconciliation stopped safely", "Nothing was changed or posted. Check the column mappings and header rows, then run it again. If it keeps failing, share the technical details below.", exc)
            return

    if "reconciliation_result" in st.session_state:
        render_result(st.session_state.reconciliation_result, run_started=run_started)


if __name__ == "__main__":
    main()
