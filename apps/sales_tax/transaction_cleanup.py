"""The Transaction Cleanup screen: uploads, options, the Run Cleanup flow, and the results display."""

from __future__ import annotations


import html

import pandas as pd
import streamlit as st

from shared.messages import friendly_error
from shared.sample_data import sample_downloads, sample_uploader

from .ingestion import (
    clear_trial_balance_cache,
    IngestionError,
    load_trial_balance_cache_snapshot,
    parse_excluded_ids,
    read_excel_upload,
    restore_trial_balance_cache_backup,
    save_trial_balance_cache,
)
from .cleanup import (
    AMOUNT_COL_IDX,
    clean_key_series,
    compute_input_fingerprint,
    DEFAULT_EXCLUDED_IDS,
    detect_mapping_conflicts,
    detect_trial_balance_conflicts,
    get_controlled_options,
    MAIN_SHEET_NAME,
    process_transactions,
    validate_inputs,
    validate_new_vendor_classifications,
    VENDOR_COL_IDX,
)
from .excel_output import (
    build_updated_mapping_workbook,
    build_workbook,
    group_output_sheets,
)


@st.cache_data(show_spinner=False, max_entries=8)
def _upload_summary(data: bytes, kind: str) -> dict:
    """Facts for the check-your-file strip under an upload. Display only: nothing here feeds the cleanup."""
    try:
        df = read_excel_upload(data)
    except IngestionError as exc:
        return {"error": str(exc)}
    summary = {"rows": len(df), "columns": df.shape[1], "missing": []}
    if kind == "source":
        if df.shape[1] != 11:
            summary["missing"].append(f"11 columns (found {df.shape[1]})")
        if df.shape[1] > AMOUNT_COL_IDX:
            amounts = pd.to_numeric(df.iloc[:, AMOUNT_COL_IDX], errors="coerce")
            bad = int(amounts.isna().sum())
            if bad:
                summary["missing"].append(f"{bad} blank or non-numeric Amount")
            summary["total_cents"] = int((amounts.dropna() * 100).round().sum())
    elif df.shape[1] < 5:
        summary["missing"].append(f"5 columns (found {df.shape[1]})")
    for column in df.columns:
        if "date" in str(column).casefold():
            dates = pd.to_datetime(df[column], errors="coerce").dropna()
            if not dates.empty:
                summary["dates"] = f"{dates.min():%b %d, %Y} to {dates.max():%b %d, %Y}"
                break
    return summary


def _render_upload_summary(uploaded, kind: str) -> None:
    summary = _upload_summary(uploaded.getvalue(), kind)
    if "error" in summary:
        st.markdown(
            f"<div class='st-chips'><span class='st-chip st-chip-warn'><b>Could not read</b> "
            f"{html.escape(summary['error'])}</span></div>",
            unsafe_allow_html=True,
        )
        return
    chips = [("File", uploaded.name), ("Rows", f"{summary['rows']:,}"), ("Columns", str(summary["columns"]))]
    if "dates" in summary:
        chips.append(("Dates", summary["dates"]))
    if "total_cents" in summary:
        cents = summary["total_cents"]
        chips.append(("Signed total", f"{'-' if cents < 0 else ''}${abs(cents) / 100:,.2f}"))
    markup = "".join(
        f"<span class='st-chip'><b>{label}</b> {html.escape(value)}</span>" for label, value in chips
    )
    if summary["missing"]:
        markup += (
            "<span class='st-chip st-chip-warn'><b>Missing</b> "
            + html.escape("; ".join(summary["missing"])) + "</span>"
        )
    else:
        markup += "<span class='st-chip st-chip-ok'><b>Required fields</b> &#10003; Found</span>"
    st.markdown(f"<div class='st-chips'>{markup}</div>", unsafe_allow_html=True)


def _drop_zone(title: str, hint: str, key: str, sample_file: str, kind: str):
    """A file uploader under a headline, with a status pill (Uploaded / Demo data / Missing) and,
    once a file is in, a strip of facts to check it against the export it came from."""
    status = st.empty()
    uploaded, is_sample = sample_uploader(
        title, key=key, sample_file=sample_file, types=["xlsx"], show_badge=False,
    )
    if is_sample:
        pill = "<span class='st-pill st-pill-sample'>&#9679; Demo data</span>"
    elif uploaded is not None:
        pill = "<span class='st-pill st-pill-ok'>&#10003; Uploaded</span>"
    else:
        pill = "<span class='st-pill st-pill-missing'>Missing</span>"
    status.markdown(
        f"<div class='st-drop-head'><div class='st-drop-title'>{title} {pill}</div>"
        f"<div class='st-drop-sub'>{hint}</div></div>",
        unsafe_allow_html=True,
    )
    if uploaded is not None:
        _render_upload_summary(uploaded, kind)
    return uploaded


def render_transaction_cleanup():
    st.header("Transaction Cleanup")
    st.markdown(
        "<div class='instruction-text'>Clean the source transactions against the vendor mapping and "
        "trial balance. Nothing is changed silently: every removed row is kept on its own sheet, "
        "and the download stays locked until the dollar control check is $0.00.</div>",
        unsafe_allow_html=True
    )
    with st.expander("How the cleanup works"):
        st.markdown(
            "Columns are read **by position**, so the source file must keep the original layout: "
            "Vendor = A, Transaction ID = C, Account # = E, Cost Center = F, Amount = G, Code = J.\n\n"
            "1. Rows whose Code starts with a letter are removed.\n"
            "2. Rows for excluded vendor IDs are removed.\n"
            "3. A repeated Transaction ID is a duplicate: the first row is kept, the rest are removed.\n"
            "4. Each kept row gets Taxability and Grouping from the vendor mapping. Vendors not in the "
            "mapping are flagged as new, to classify.\n"
            "5. The GL account is built from Cost Center and Account #, and its Account Name comes from the "
            "cached trial balance.\n\n"
            "Original total = retained total + removed total, to the cent."
        )

    left, right = st.columns(2, gap="large")
    with left:
        with st.container(key="st-card-uploads", border=True):
            st.markdown("<div class='st-card-title'>Upload files</div>", unsafe_allow_html=True)
            source_file = _drop_zone(
                "Source Transactions", "XLSX with 11 columns (A-K)", "source_file",
                "sales_tax_source_transactions.xlsx", "source",
            )
            mapping_file = _drop_zone(
                "Vendor Mapping", "XLSX vendor mapping file", "mapping_file",
                "sales_tax_vendor_mapping.xlsx", "mapping",
            )

            sample_downloads([
                ("Source Transactions (.xlsx)", "sales_tax_source_transactions.xlsx"),
                ("Vendor Mapping (.xlsx)", "sales_tax_vendor_mapping.xlsx"),
                ("Trial Balance (.xlsx)", "sales_tax_trial_balance.xlsx"),
            ], key="st_cleanup")
            st.caption(
                "The sample trial balance is a download only, because the real one is cached on "
                "disk and shared. Sample GL accounts will show no Account Name unless you load it."
            )

        with st.container(key="st-card-options", border=True):
            st.markdown("<div class='st-card-title'>Process options</div>", unsafe_allow_html=True)
            excluded_text = st.text_area(
                "Excluded Vendor IDs (comma or newline separated)",
                value="\n".join(DEFAULT_EXCLUDED_IDS),
                height=100,
            )
            exclusion_file = st.file_uploader(
                "Or upload a CSV of Vendor IDs to exclude",
                type=["csv"],
                help="One vendor ID per row, no header row. Added on top of the list above.",
            )
            excluded_ids, exclusion_error = parse_excluded_ids(excluded_text, exclusion_file)
            if exclusion_error:
                st.error(exclusion_error)

            legacy_pad = st.checkbox(
                "Use legacy right-padding for GL account segments",
                value=True,
                help="Uncheck to use conventional leading-zero padding instead.",
            )
            pad_side = "right" if legacy_pad else "left"

    # Load and validate the cache exactly once during this rerun. The status
    # and DataFrame describe the same byte snapshot, so clicking Run Cleanup
    # does not parse the workbook a second time.
    cached_tb_df, cached_tb_status = load_trial_balance_cache_snapshot()

    with st.sidebar:
        st.subheader("Trial Balance File")
        st.caption(
            "Looks up the Account Name for each transaction's GL account. "
            "Cached on disk - you only need to upload this when it changes."
        )

        if cached_tb_status.valid:
            st.markdown(
                "<div class='st-status-badge'><span class='st-dot'></span>"
                f"<div><b>{cached_tb_status.row_count:,} GL Accounts Cached</b>"
                f"<small>Updated {cached_tb_status.modified_at:%b %d, %Y %I:%M %p}</small></div></div>",
                unsafe_allow_html=True,
            )
            if cached_tb_status.incomplete_count:
                st.caption(
                    f"{cached_tb_status.incomplete_count} GL account(s) in this file "
                    "have no Account Name yet."
                )
            with st.expander("Replace or clear cached file"):
                replacement = st.file_uploader(
                    "Upload a new Trial Balance (.xlsx)",
                    type=["xlsx"],
                    key="tb_replace",
                    help="Replaces the cached trial balance used for GL account lookups.",
                )
                if replacement is not None:
                    try:
                        save_trial_balance_cache(replacement.getvalue())
                    except IngestionError as exc:
                        st.error(str(exc))
                    else:
                        st.success("Cache updated.")
                        st.rerun()

                col_clear, col_restore = st.columns(2)
                if col_clear.button("Clear cached Trial Balance"):
                    try:
                        clear_trial_balance_cache()
                    except IngestionError as exc:
                        st.error(str(exc))
                    else:
                        st.rerun()
                if col_restore.button("Restore previous version"):
                    try:
                        restore_trial_balance_cache_backup()
                    except IngestionError as exc:
                        st.error(str(exc))
                    else:
                        st.success("Restored the previous cached version.")
                        st.rerun()
        elif cached_tb_status.exists:
            # A cache file is present but failed validation - surfacing the
            # specific reason here matters: without it, this would look
            # identical to "nothing has ever been uploaded", which is a
            # different (and less alarming) situation than "the cached
            # file is broken and GL matching is currently unavailable".
            st.error(f"Cached Trial Balance is invalid: {cached_tb_status.error}")
            replacement = st.file_uploader(
                "Upload a replacement Trial Balance (.xlsx)",
                type=["xlsx"],
                key="tb_initial",
                help="The company trial balance export, used to match each GL account to an Account Name.",
            )
            if replacement is not None:
                try:
                    save_trial_balance_cache(replacement.getvalue())
                except IngestionError as exc:
                    st.error(str(exc))
                else:
                    st.success("Trial Balance cached for future runs.")
                    st.rerun()
        else:
            st.warning("No Trial Balance file cached yet.")
            initial_upload = st.file_uploader(
                "Upload Trial Balance (.xlsx)",
                type=["xlsx"],
                key="tb_initial",
                help="The company trial balance export, used to match each GL account to an Account Name.",
            )
            if initial_upload is not None:
                try:
                    save_trial_balance_cache(initial_upload.getvalue())
                except IngestionError as exc:
                    st.error(str(exc))
                else:
                    st.success("Trial Balance cached for future runs.")
                    st.rerun()

    ready = bool(source_file and mapping_file and cached_tb_status.valid)
    with left:
        run_clicked = st.button(
            "Run Cleanup", type="primary", disabled=not ready,
            icon=":material/play_arrow:", key="st-run-cleanup", width="stretch",
        )
        if not ready:
            missing = []
            if not source_file:
                missing.append("Source Transactions")
            if not mapping_file:
                missing.append("Vendor Mapping")
            if not cached_tb_status.valid:
                missing.append("Trial Balance (upload once in the sidebar)")
            st.caption("Still needed: " + ", ".join(missing))

    with right:
        st.markdown("<div class='st-card-title'>Output &amp; Preview</div>", unsafe_allow_html=True)
        right_card = st.container(key="st-card-output", border=True)

    with right_card:
        if run_clicked:
            try:
                df_source = read_excel_upload(source_file)
                df_mapping = read_excel_upload(mapping_file)
                validation_errors = validate_inputs(df_source, df_mapping, cached_tb_df)
                # Conflicting master-data duplicates (a vendor ID mapped to two
                # different Taxability/Grouping combos, or a GL account tied to
                # two different Account Names) must block processing - silently
                # keeping whichever row happens to come last would make the
                # result depend on worksheet row order, not on a real decision.
                validation_errors += detect_mapping_conflicts(df_mapping)
                validation_errors += detect_trial_balance_conflicts(cached_tb_df)
                if validation_errors:
                    st.error(
                        "Please fix the following before running cleanup:\n\n"
                        + "\n".join(f"- {e}" for e in validation_errors)
                    )
                else:
                    result_df, new_vendor_flags, stats, removed_df = process_transactions(
                        df_source, df_mapping, cached_tb_df, excluded_ids, pad_side
                    )
                    st.session_state["tc_result"] = result_df
                    st.session_state["tc_flags"] = new_vendor_flags
                    st.session_state["tc_stats"] = stats
                    st.session_state["tc_removed"] = removed_df
                    st.session_state["tc_fingerprint"] = compute_input_fingerprint(
                        source_file.getvalue(), mapping_file.getvalue(),
                        cached_tb_status.fingerprint, excluded_ids, pad_side,
                    )
                    # Clear any updated-mapping-file download left over from a
                    # previous run - its vendor classifications belonged to a
                    # different result set and shouldn't still be offered here.
                    st.session_state.pop("tc_updated_mapping_buf", None)
                    st.session_state.pop("tc_updated_mapping_count", None)

                    # Fires once, right when the run that found them completes - not
                    # on every later rerun of the script (e.g. from touching an
                    # unrelated widget), which is what would happen if this lived in
                    # the persistent display block below alongside a static banner.
                    if stats["new_vendor_rows"] > 0:
                        st.toast(
                            f"{stats['new_vendor_rows']} transaction row(s) belong to "
                            "vendors not found in the mapping file.",
                            icon="⚠️",
                        )
            except ValueError as e:
                friendly_error(
                    "Cleanup couldn't run",
                    f"{e}\n\nCheck the files against the sample templates, then run it again. Nothing was changed.",
                )

        if "tc_result" in st.session_state:
            # Stale-result protection: if any input has changed since this run
            # completed (different file, different exclusions, different
            # padding option, or a replaced/cleared trial balance), the
            # fingerprint won't match, and the old results are hidden entirely
            # rather than left on screen looking current.
            current_fingerprint = compute_input_fingerprint(
                source_file.getvalue() if source_file else None,
                mapping_file.getvalue() if mapping_file else None,
                cached_tb_status.fingerprint, excluded_ids, pad_side,
            )
            if current_fingerprint != st.session_state.get("tc_fingerprint"):
                st.warning("Inputs have changed. Run Cleanup again before downloading.")
                return

            result_df = st.session_state["tc_result"]
            new_vendor_flags = st.session_state["tc_flags"]
            stats = st.session_state["tc_stats"]
            removed_df = st.session_state["tc_removed"]

            st.subheader("Summary")

            # Use custom HTML for color-coded metric cards
            st.markdown(f"""
            <div style="display: flex; gap: 15px; margin-bottom: 15px; flex-wrap: wrap;">
                <div style="background-color: #fff3f3; padding: 15px; border-radius: 5px; border-left: 5px solid #ff4b4b; flex: 1; min-width: 150px;">
                    <div style="font-size: 0.95rem; color: #444;">Removed - Prefixes</div>
                    <div style="font-size: 1.5rem; font-weight: bold;">{stats['letter_rows_deleted']:,}</div>
                </div>
                <div style="background-color: #fff3f3; padding: 15px; border-radius: 5px; border-left: 5px solid #ff4b4b; flex: 1; min-width: 150px;">
                    <div style="font-size: 0.95rem; color: #444;">Removed - Duplicates</div>
                    <div style="font-size: 1.5rem; font-weight: bold;">{stats['duplicate_rows_deleted']:,}</div>
                </div>
                <div style="background-color: #fff3f3; padding: 15px; border-radius: 5px; border-left: 5px solid #ff4b4b; flex: 1; min-width: 150px;">
                    <div style="font-size: 0.95rem; color: #444;">Removed - Excluded</div>
                    <div style="font-size: 1.5rem; font-weight: bold;">{stats['excluded_rows_deleted']:,}</div>
                </div>
                <div style="background-color: #f0f8ff; padding: 15px; border-radius: 5px; border-left: 5px solid #000080; flex: 1; min-width: 150px;">
                    <div style="font-size: 0.95rem; color: #444;">Rows Kept</div>
                    <div style="font-size: 1.5rem; font-weight: bold;">{len(result_df):,}</div>
                </div>
            </div>
            <div style="display: flex; gap: 15px; margin-bottom: 20px; flex-wrap: wrap;">
                <div style="background-color: #f2f9f2; padding: 15px; border-radius: 5px; border-left: 5px solid #4caf50; flex: 1; min-width: 150px;">
                    <div style="font-size: 0.95rem; color: #444;">Vendors Matched</div>
                    <div style="font-size: 1.5rem; font-weight: bold;">{stats['matched_vendor_rows']:,}</div>
                </div>
                <div style="background-color: #f2f9f2; padding: 15px; border-radius: 5px; border-left: 5px solid #4caf50; flex: 1; min-width: 150px;">
                    <div style="font-size: 0.95rem; color: #444;">GL Accounts Matched</div>
                    <div style="font-size: 1.5rem; font-weight: bold;">{stats['matched_gl_rows']:,}</div>
                </div>
                <div style="background-color: #fff8e1; padding: 15px; border-radius: 5px; border-left: 5px solid #ffc107; flex: 1; min-width: 150px;">
                    <div style="font-size: 0.95rem; color: #444;">New Vendors</div>
                    <div style="font-size: 1.5rem; font-weight: bold;">{stats['new_vendor_rows']:,}</div>
                </div>
            </div>
            """, unsafe_allow_html=True)

            # Dollar control totals: proves Original = Retained + Removed, not
            # just row counts. A non-zero control difference would mean the
            # reconciliation itself is broken (a real bug), so it's called out
            # in red rather than blended in with the other metrics.
            control_ok = stats["control_difference"] == 0
            control_color = "#4caf50" if control_ok else "#ff4b4b"
            control_bg = "#f2f9f2" if control_ok else "#fff3f3"
            st.markdown(f"""
            <div style="display: flex; gap: 15px; margin-bottom: 20px; flex-wrap: wrap;">
                <div style="background-color: #f0f8ff; padding: 15px; border-radius: 5px; border-left: 5px solid #000080; flex: 1; min-width: 150px;">
                    <div style="font-size: 0.95rem; color: #444;">Original Total</div>
                    <div style="font-size: 1.5rem; font-weight: bold;">${stats['original_amount_total']:,.2f}</div>
                </div>
                <div style="background-color: #f0f8ff; padding: 15px; border-radius: 5px; border-left: 5px solid #000080; flex: 1; min-width: 150px;">
                    <div style="font-size: 0.95rem; color: #444;">Retained Total</div>
                    <div style="font-size: 1.5rem; font-weight: bold;">${stats['retained_amount_total']:,.2f}</div>
                </div>
                <div style="background-color: #f0f8ff; padding: 15px; border-radius: 5px; border-left: 5px solid #000080; flex: 1; min-width: 150px;">
                    <div style="font-size: 0.95rem; color: #444;">Removed Total</div>
                    <div style="font-size: 1.5rem; font-weight: bold;">${stats['removed_amount_total']:,.2f}</div>
                </div>
                <div style="background-color: {control_bg}; padding: 15px; border-radius: 5px; border-left: 5px solid {control_color}; flex: 1; min-width: 150px;">
                    <div style="font-size: 0.95rem; color: #444;">Control Difference</div>
                    <div style="font-size: 1.5rem; font-weight: bold; color: {control_color};">${stats['control_difference']:,.2f}</div>
                </div>
            </div>
            """, unsafe_allow_html=True)
            if not control_ok:
                st.error(
                    "**The dollar control check failed.** Original Total does not equal Retained + Removed, "
                    "which points to a problem in the cleanup itself. Do not use this run's output, and the "
                    "download stays disabled."
                )
            elif stats['new_vendor_rows']:
                st.info(
                    f"Control check passed ($0.00 difference). {stats['new_vendor_rows']:,} transaction row(s) "
                    "belong to vendors not in your mapping: classify them below, then build the updated mapping."
                )
            else:
                st.success("Control check passed ($0.00 difference) and every vendor was found in your mapping. The workbook is ready to download.")

            # Vendor/Amount column names, needed both for classification below
            # and for the preview table's column_config further down.
            amount_col_name = result_df.columns[AMOUNT_COL_IDX]
            vendor_col_name = result_df.columns[VENDOR_COL_IDX]

            if stats["new_vendor_rows"] > 0:
                st.subheader("Classify New Vendors")
                st.caption(
                    f"{stats['new_vendor_rows']} transaction row(s) belong to vendors not "
                    "found in the mapping file. Assign Taxability / Grouping below, then "
                    "download an updated mapping file to use next run."
                )

                # One row per DISTINCT new vendor (grouped on the same normalized
                # key used for matching, not exact text), not one row per
                # transaction - several transactions can share the same unmapped
                # vendor, and this also collapses near-duplicates that differ
                # only by casing/spacing into a single row to classify.
                new_vendor_subset = result_df.loc[new_vendor_flags, [vendor_col_name]].copy()
                new_vendor_subset["_key"] = clean_key_series(new_vendor_subset[vendor_col_name])
                new_vendor_names = (
                    new_vendor_subset.drop_duplicates(subset="_key", keep="first")[vendor_col_name]
                    .sort_values()
                    .reset_index(drop=True)
                )
                new_vendor_df = pd.DataFrame({
                    "Vendor": new_vendor_names,
                    "Taxability": "",
                    "Grouping": "",
                })

                # Reads the mapping file once for this section: used to derive
                # controlled dropdown options below, and again as the guardrail
                # / collision check just before building. A fresh read each
                # section-render keeps this in sync if the user swaps the
                # uploaded mapping file without re-running cleanup.
                df_mapping_for_editor = read_excel_upload(mapping_file) if mapping_file is not None else pd.DataFrame()

                tax_options = get_controlled_options(df_mapping_for_editor, 3)
                group_options = get_controlled_options(df_mapping_for_editor, 4)

                # Dropdowns restricted to the mapping file's own existing values
                # where any exist - prevents variations like "Taxable" /
                # "TAXABLE" / "Tax" from being entered as different values. If
                # the mapping file has no established values yet (e.g. it's
                # brand new), there's nothing to constrain against, so this
                # falls back to free text.
                tax_col_config = (
                    st.column_config.SelectboxColumn("Taxability", options=tax_options, required=True)
                    if tax_options else st.column_config.TextColumn("Taxability")
                )
                group_col_config = (
                    st.column_config.SelectboxColumn("Grouping", options=group_options, required=True)
                    if group_options else st.column_config.TextColumn("Grouping")
                )

                edited_new_vendors = st.data_editor(
                    new_vendor_df,
                    width="stretch",
                    hide_index=True,
                    num_rows="fixed",
                    key="new_vendor_editor",
                    column_config={
                        "Vendor": st.column_config.TextColumn("Vendor", disabled=True),
                        "Taxability": tax_col_config,
                        "Grouping": group_col_config,
                    },
                )

                if st.button("Build Updated Mapping File", type="primary", key="build_updated_mapping"):
                    if mapping_file is None:
                        st.error("Re-upload the Vendor Mapping file to build an updated mapping workbook.")
                    else:
                        guardrail_errors = validate_new_vendor_classifications(
                            edited_new_vendors, df_mapping_for_editor
                        )
                        if guardrail_errors:
                            st.error(
                                "Please fix the following before building the mapping file:\n\n"
                                + "\n".join(f"- {e}" for e in guardrail_errors)
                            )
                        else:
                            mapping_buf = build_updated_mapping_workbook(df_mapping_for_editor, edited_new_vendors)
                            # Stored as raw bytes (not the BytesIO object) so the
                            # download button below stays available across any
                            # later rerun (e.g. editing an unrelated widget),
                            # instead of only appearing for the single run where
                            # the button was clicked.
                            st.session_state["tc_updated_mapping_buf"] = mapping_buf.getvalue()
                            st.session_state["tc_updated_mapping_count"] = len(edited_new_vendors)

                if "tc_updated_mapping_buf" in st.session_state:
                    st.caption(f"{st.session_state['tc_updated_mapping_count']} new vendor(s) added.")
                    st.download_button(
                        "Download Updated Mapping File",
                        data=st.session_state["tc_updated_mapping_buf"],
                        file_name="Vendor_Mapping_Updated.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        key="dl_updated_mapping",
                    )

            st.subheader("Preview")
            st.caption(
                "Showing the first 10 rows of each output sheet - the downloaded "
                "workbook contains the full data."
            )

            # Same New Vendor indicator used before, but only on the All
            # Transactions tab - it's a UI-only aid (never written to the
            # workbook), so the other tabs, which are plain slices of the
            # actual output sheets, don't carry it.
            display_df = result_df.copy()
            display_df.insert(0, "New Vendor", new_vendor_flags.map({True: "New", False: ""}))

            base_col_config = {
                amount_col_name: st.column_config.NumberColumn("Amount", format="$%.2f"),
                vendor_col_name: st.column_config.TextColumn(width="medium"),
            }
            all_transactions_col_config = {
                **base_col_config,
                "New Vendor": st.column_config.TextColumn(
                    "New Vendor", width="small",
                    help="Vendor not found in the mapping file",
                ),
            }

            # Same grouping the workbook itself uses, so each tab here lines up
            # one-to-one with a sheet in the download.
            preview_sheets = {MAIN_SHEET_NAME: (display_df, all_transactions_col_config)}
            for label, sub_df in group_output_sheets(result_df, new_vendor_flags).items():
                preview_sheets[label] = (sub_df, base_col_config)
            if removed_df is not None and len(removed_df) > 0:
                preview_sheets["Removed Transactions"] = (removed_df, base_col_config)

            tabs = st.tabs(list(preview_sheets.keys()))
            for tab, (label, (sheet_df, sheet_col_config)) in zip(tabs, preview_sheets.items()):
                with tab:
                    st.caption(f"{min(10, len(sheet_df)):,} of {len(sheet_df):,} row(s) shown")
                    st.dataframe(
                        sheet_df.head(10),
                        width="stretch",
                        column_config=sheet_col_config,
                        hide_index=True,
                    )

            buf, sheet_count = build_workbook(result_df, new_vendor_flags, removed_df)
            st.download_button(
                "Download Cleaned Workbook",
                data=buf,
                file_name="Cleaned_Transactions.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                disabled=not control_ok,
            )
            if not control_ok:
                st.caption("Download disabled: the dollar control check above must reconcile to $0.00 first.")
            st.caption(f"Workbook contains {sheet_count + 1} sheet(s): {MAIN_SHEET_NAME} plus {sheet_count} grouped sheet(s).")
        else:
            st.markdown(
                "<div class='st-empty'><div class='st-empty-icon'>&#128196;</div>"
                "<b>Upload files to preview cleaned transactions</b>"
                "<span>Cleaned transactions, the summary and the download appear here after Run Cleanup.</span></div>",
                unsafe_allow_html=True,
            )
