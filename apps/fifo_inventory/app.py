# app.py — a page of the combined Streamlit app; run via ../../app.py
from pathlib import Path
import pandas as pd
import streamlit as st

# ==========================================
# IMPORT SEPARATED MODULES
# ==========================================
from apps.fifo_inventory.user_inputs import (
    OPENING_SEED_FISCAL_YEAR, PRODUCTS,
    PERIOD_12_OPENING_LAYERS
)

from apps.fifo_inventory.ingestion import (
    _excel_sheet_names, _read_uploaded_table,
    parse_master_grid_upload, apply_updates_to_master_grid,
    validate_master_grid, prepare_receipts_upload
)

from apps.fifo_inventory.upload_templates import (
    build_master_grid_template, build_receipts_template, build_upload_templates_zip
)

from apps.fifo_inventory.excel_export import (
    export_master_excel, value_variance_drift_is_acceptable,
)

from apps.fifo_inventory import app_settings

from apps.fifo_inventory.fifo_layer_store import FIFOLayerStore, SNAPSHOT_DIR

from apps.fifo_inventory.fifo_calculations import (
    _uploaded_file_digest, _build_run_signature,
    stage_batch_calculation, summarize_control_counts
)

from apps.fifo_inventory.sample_data import (
    build_master_grid_sample, build_receipts_sample, sample_token,
)

from apps.fifo_inventory.sidebar import render_sidebar, render_history
from apps.fifo_inventory.insights import render_insights
from shared.sample_data import sample_downloads, sample_uploader

# ==========================================
# UI LAYOUT & INGESTION
# ==========================================
# Page title, icon, and wide layout come from st.navigation in the root app.py.
st.title(":material/inventory_2: 13-Period Batch FIFO Inventory Tracker")

try:
    with open(Path(__file__).resolve().parent / "styles.css") as f:
        st.markdown(f"<style>{f.read()}</style>", unsafe_allow_html=True)
except FileNotFoundError:
    st.warning("styles.css could not be found next to the FIFO app.")

layer_store = FIFOLayerStore(PRODUCTS)

# ------------------------------------------------------------------
# Disk-backed recovery on startup
# ------------------------------------------------------------------
# Session state does not survive a server restart, redeploy, or a
# sleeping/waking host. Every committed period is written to disk
# automatically (see FIFOLayerStore.commit_period), so a brand-new,
# otherwise-empty session should recover that saved state before falling
# back to the Period 12 opening seed below — "close period" is what
# protects the data now, not "remember to click download."
#
# The `disk_snapshot_checked` flag makes this run at most once per session
# (on the first script execution after session state is created), rather
# than re-checking disk on every widget interaction. It only acts when
# session state is genuinely empty, so it can never clobber in-progress
# work within an active session.
if not st.session_state.get('disk_snapshot_checked', False):
    st.session_state['disk_snapshot_checked'] = True
    if not st.session_state.get('fifo_period_history') and all(
        len(layer_store.get(alias)) == 0 for alias in PRODUCTS
    ):
        try:
            loaded_key = layer_store.load_latest_snapshot_from_disk()
            if loaded_key:
                st.session_state['period12_autoseeded'] = True  # skip the seed further below
                st.session_state['app_flash'] = f"Restored FIFO layers from the last saved snapshot ({loaded_key})."
                st.rerun()
        except Exception as exc:
            st.error(
                f"A snapshot was found on disk but could not be loaded automatically: {exc}. "
                f"You can still restore one manually from the sidebar below."
            )

def _blank_master_grid():
    rows = []
    for alias, name in PRODUCTS.items():
        row = {'PRODUCT ALIAS': alias, 'DESCRIPTION': name}
        for period in range(1, 14):
            row[f"{period:02d}"] = 0.0
            row[f"{period:02d}V"] = 0.0
        rows.append(row)
    return pd.DataFrame(rows)


fiscal_year, current_period, period_end_date, selected_key = render_sidebar(layer_store)

app_flash = st.session_state.pop('app_flash', None)
if app_flash:
    st.success(app_flash)

if (fiscal_year == OPENING_SEED_FISCAL_YEAR and current_period == 12
        and not st.session_state.get('period12_autoseeded', False)
        and not st.session_state.get('fifo_period_history')
        and all(len(layer_store.get(alias)) == 0 for alias in PRODUCTS)):
    layer_store.seed_opening_layers(PERIOD_12_OPENING_LAYERS)
    st.session_state['period12_autoseeded'] = True
    st.session_state['app_flash'] = "Period 12 opening FIFO layers were seeded automatically."
    st.rerun()

tab_processing, tab_analysis, tab_history = st.tabs([
    ":material/edit_note: Period Processing",
    ":material/insights: Inventory Analysis",
    ":material/history: History & Snapshots",
])

with tab_processing:
    with st.container(border=True):
        st.subheader(":material/upload_file: Data Uploads")
        st.caption("Uploads only create a preview. FIFO history changes when you close the period.")

        with st.expander("📄 Need a starting template?", expanded=False):
            st.caption(
                "Blank, correctly-shaped workbooks with the exact column headers and data types this app expects — "
                "every product alias pre-filled for the Master Grid, plus a filled-in example row for Receipts. Each "
                "includes an Instructions sheet."
            )
            tmpl_col1, tmpl_col2, tmpl_col3 = st.columns(3)
            with tmpl_col1:
                st.download_button(
                    "📋 Master Grid Template", data=build_master_grid_template(),
                    file_name="Master_Grid_Upload_Template.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            with tmpl_col2:
                st.download_button(
                    "🧾 Receipts Template", data=build_receipts_template(),
                    file_name="Receipts_Upload_Template.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            with tmpl_col3:
                st.download_button(
                    "📦 Both (.zip)", data=build_upload_templates_zip(),
                    file_name="FIFO_Upload_Templates.zip", mime="application/zip",
                )

        # A sample is built from the layers as they are now, so it is rebuilt when the period, the
        # as-of date, or the layers change.
        _sample_token = sample_token(fiscal_year, current_period, period_end_date, layer_store)

        def _reset_master_grid_state():
            """A sample's numbers were merged into the session's Master Grid; drop them with it."""
            st.session_state['master_grid'] = _blank_master_grid()
            for stale in ('master_grid_upload_sig', 'master_grid_upload_debug',
                          'master_grid_editor_widget', 'staged_fifo_run'):
                st.session_state.pop(stale, None)

        upload_col1, upload_col2 = st.columns(2)
        with upload_col1:
            st.markdown("**Step 1: Ending Inventory**")
            master_upload, master_is_sample = sample_uploader(
                "Upload Ending Inventory Workbook", key='master_grid_upload', types=['csv', 'xlsx'],
                sample_builder=lambda: build_master_grid_sample(layer_store, fiscal_year, current_period, period_end_date),
                cache_token=_sample_token, on_clear=_reset_master_grid_state, label_visibility="visible",
            )
            master_sheet_choice = None
            if master_upload is not None:
                try:
                    master_sheets = _excel_sheet_names(master_upload)
                except ValueError as exc:
                    master_sheets = None
                    st.error(str(exc))
                if master_sheets and len(master_sheets) > 1:
                    master_sheet_choice = st.selectbox(
                        "This workbook has multiple sheets — choose the one with your Ending Inventory data:",
                        master_sheets, key='master_sheet_select'
                    )
                elif master_sheets:
                    master_sheet_choice = master_sheets[0]
        with upload_col2:
            st.markdown("**Step 2: Current Period Receipts**")
            upload_receipts, receipts_is_sample = sample_uploader(
                "Upload Current Receipts", key='receipts_upload', types=['csv', 'xlsx'],
                sample_builder=lambda: build_receipts_sample(layer_store, fiscal_year, current_period, period_end_date),
                cache_token=_sample_token, label_visibility="visible",
            )
            st.caption("PRICE/AMOUNT is read as the receipt's total value, not a unit rate.")
            receipts_sheet_choice = None
            if upload_receipts is not None:
                try:
                    receipts_sheets = _excel_sheet_names(upload_receipts)
                except ValueError as exc:
                    receipts_sheets = None
                    st.error(str(exc))
                if receipts_sheets and len(receipts_sheets) > 1:
                    receipts_sheet_choice = st.selectbox(
                        "This workbook has multiple sheets — choose the one with your receipts data:",
                        receipts_sheets, key='receipts_sheet_select'
                    )
                elif receipts_sheets:
                    receipts_sheet_choice = receipts_sheets[0]
        sample_downloads([
            ("Master Grid sample (.xlsx)", f"fifo_master_grid_P{current_period:02d}.xlsx",
             build_master_grid_sample(layer_store, fiscal_year, current_period, period_end_date)[1]),
            ("Receipts sample (.xlsx)", f"fifo_receipts_P{current_period:02d}.xlsx",
             build_receipts_sample(layer_store, fiscal_year, current_period, period_end_date)[1]),
        ], key="fifo")
        if master_is_sample or receipts_is_sample:
            st.warning(
                "Demo data is loaded. The preview works as usual, but Close & Commit is disabled so demo "
                "numbers can never become the official FIFO layers. Clear the sample files to close a period."
            )

    with st.container(border=True):
        st.subheader(":material/table_chart: Master Inventory Quantities")
        st.caption("Products are matched by Alias.")
        with st.expander("Help: how periods and beginning balances work"):
            st.markdown(
                "- Products are matched strictly by Alias.\n"
                "- Period 1 uses Period 13 as its beginning quantity, so the Period 13 column must hold the "
                "preceding fiscal year's ending balance."
            )

        if 'master_grid' not in st.session_state:
            st.session_state['master_grid'] = _blank_master_grid()

        master_upload_errors = []
        if master_upload is not None:
            upload_sig = (_uploaded_file_digest(master_upload), fiscal_year, current_period, master_sheet_choice)
            if st.session_state.get('master_grid_upload_sig') != upload_sig:
                debug = {'errors': [], 'warnings': []}
                try:
                    uploaded_grid = _read_uploaded_table(master_upload, sheet_name=master_sheet_choice)
                    parsed = parse_master_grid_upload(uploaded_grid)
                    updates, period_cols, alias_col, unmatched, duplicates, invalid_cells, value_period_cols = parsed

                    required_periods = {current_period, 13 if current_period == 1 else current_period - 1}
                    missing_period_headers = sorted(required_periods - set(period_cols))
                    if missing_period_headers:
                        debug['errors'].append(
                            "Upload is missing required quantity period header(s): " +
                            ", ".join(f"{p:02d}" for p in missing_period_headers)
                        )
                    missing_value_headers = sorted(required_periods - set(value_period_cols))
                    if missing_value_headers:
                        debug['errors'].append(
                            "Upload is missing required value period header(s): " +
                            ", ".join(f"{p:02d}V" for p in missing_value_headers)
                        )
                    missing_aliases = [alias for alias in PRODUCTS if alias not in updates]
                    if missing_aliases:
                        debug['errors'].append(f"Upload is missing known product alias(es): {missing_aliases}")
                    missing_required_values = [
                        f"Alias {alias} / P{period:02d} quantity"
                        for alias in PRODUCTS for period in required_periods
                        if alias in updates and 'qty' not in updates[alias].get(period, {})
                    ]
                    if missing_required_values:
                        debug['errors'].append(
                            "Required inventory quantities are blank or invalid: " + ", ".join(missing_required_values[:20])
                        )
                    missing_required_value_figures = [
                        f"Alias {alias} / P{period:02d}V value"
                        for alias in PRODUCTS for period in required_periods
                        if alias in updates and 'value' not in updates[alias].get(period, {})
                    ]
                    if missing_required_value_figures:
                        debug['errors'].append(
                            "Required inventory values are blank or invalid: " + ", ".join(missing_required_value_figures[:20])
                        )
                    if duplicates:
                        debug['errors'].append(f"Duplicate aliases are not allowed: {sorted(set(duplicates))}")
                    if invalid_cells:
                        debug['errors'].append(f"{len(invalid_cells)} inventory cell(s) are invalid or negative.")
                    if unmatched:
                        debug['warnings'].append(f"Unknown aliases were ignored: {unmatched}")

                    new_grid, applied_count = apply_updates_to_master_grid(
                        st.session_state['master_grid'], updates, period_cols, value_period_cols
                    )
                    st.session_state['master_grid'] = new_grid
                    debug.update({
                        'alias_col': alias_col, 'period_cols': period_cols, 'value_period_cols': value_period_cols,
                        'applied_count': applied_count,
                        'unmatched': unmatched, 'duplicates': duplicates, 'invalid_cells': invalid_cells,
                        'preview': uploaded_grid,
                    })
                except Exception as exc:
                    debug['errors'].append(str(exc))
                    debug.update({'alias_col': None, 'period_cols': {}, 'value_period_cols': {}, 'applied_count': 0,
                                  'unmatched': [], 'duplicates': [], 'invalid_cells': [], 'preview': pd.DataFrame()})

                st.session_state['master_grid_upload_sig'] = upload_sig
                st.session_state['master_grid_upload_debug'] = debug
                st.session_state.pop('master_grid_editor_widget', None)
                st.session_state.pop('staged_fifo_run', None)
                st.rerun()

            debug = st.session_state.get('master_grid_upload_debug', {})
            master_upload_errors = debug.get('errors', [])
            if master_upload_errors:
                for issue in master_upload_errors:
                    st.error(issue)
            elif debug:
                st.success(f"✅ {debug.get('applied_count', 0)} of {len(PRODUCTS)} products imported")
            for warning in debug.get('warnings', []):
                st.warning(warning)

            if debug:
                with st.expander("🔍 Master Grid Upload Debug Info", expanded=bool(master_upload_errors)):
                    st.write(f"Alias column detected: `{debug.get('alias_col')}`")
                    detected = debug.get('period_cols', {})
                    detected_values = debug.get('value_period_cols', {})
                    st.write("Quantity period columns detected: " +
                             (', '.join(f"{p:02d}" for p in sorted(detected)) if detected else "None"))
                    st.write("Value period columns detected: " +
                             (', '.join(f"{p:02d}V" for p in sorted(detected_values)) if detected_values else "None"))
                    if debug.get('invalid_cells'):
                        st.dataframe(pd.DataFrame(debug['invalid_cells']), width="stretch", hide_index=True)
                    if not debug.get('preview', pd.DataFrame()).empty:
                        st.dataframe(debug['preview'], width="stretch", hide_index=True)

        with st.expander("✏️ Manual Entry / Paste (Advanced — use only in select cases)", expanded=False):
            st.caption("Alias and Description are locked control fields. Only fiscal-period quantities can be edited.")
            edited_master_grid = st.data_editor(
                st.session_state['master_grid'], num_rows="fixed", width="stretch", hide_index=True,
                disabled=['PRODUCT ALIAS', 'DESCRIPTION'], key='master_grid_editor_widget'
            )
            st.session_state['master_grid'] = edited_master_grid

        edited_master_grid = st.session_state['master_grid']

        _effective_tolerances = app_settings.get_effective_tolerances()
        with st.expander("Help: Beginning Value Variance"):
            st.markdown(
                "Computed automatically each period: this Master Grid's beginning-period value column (`" + (
                    "13V" if current_period == 1 else f"{current_period - 1:02d}V"
                ) + "` right now) is compared with the FIFO layers actually carried forward. Nothing to enter. "
                "Drift outside the accepted range ("
                f"${_effective_tolerances['drift_min']:,.3f} to ${_effective_tolerances['drift_max']:,.2f}, "
                "adjustable under Inventory Analysis → Settings) versus the last period close is flagged after you "
                "calculate a preview."
            )

        global_receipts_data = pd.DataFrame()
        receipt_errors = []
        receipt_warnings = []
        receipt_debug = {}
        if upload_receipts is not None:
            try:
                raw_receipts_upload = _read_uploaded_table(upload_receipts, sheet_name=receipts_sheet_choice)
                global_receipts_data, receipt_errors, receipt_warnings, receipt_debug = prepare_receipts_upload(
                    raw_receipts_upload, current_period
                )
            except Exception as exc:
                receipt_errors = [str(exc)]

            for issue in receipt_errors:
                st.error(issue)
            for warning in receipt_warnings:
                st.warning(warning)
            if not global_receipts_data.empty:
                st.success(f"✅ {len(global_receipts_data)} valid receipt row(s) staged for Period {current_period:02d}")
            else:
                st.warning(f"0 valid receipt rows are staged for Period {current_period:02d}.")

            with st.expander("🔍 Receiving Upload Debug Info", expanded=bool(receipt_errors)):
                if receipt_debug:
                    st.write(
                        f"Detected columns — Alias: `{receipt_debug.get('alias_col')}`, Date: `{receipt_debug.get('date_col')}`, "
                        f"Quantity: `{receipt_debug.get('qty_col')}`, Total Value: `{receipt_debug.get('price_col')}`, "
                        f"Period: `{receipt_debug.get('period_col')}`"
                    )
                    rejected = receipt_debug.get('rejected_rows', pd.DataFrame())
                    duplicates = receipt_debug.get('duplicate_rows', pd.DataFrame())
                    if not rejected.empty:
                        st.markdown("**Rejected selected-period rows**")
                        st.dataframe(rejected, width="stretch", hide_index=True)
                    if not duplicates.empty:
                        st.markdown("**Possible duplicate rows**")
                        st.dataframe(duplicates, width="stretch", hide_index=True)
                if not global_receipts_data.empty:
                    st.markdown("**Rows included in the preview**")
                    st.dataframe(global_receipts_data, width="stretch", hide_index=True)

    with st.container(border=True):
        st.subheader(":material/settings: Batch Processing")
        st.caption("Preview changes nothing. Close Period saves the ending layers.")

        grid_errors = validate_master_grid(edited_master_grid, current_period)
        fatal_preview_errors = list(dict.fromkeys(master_upload_errors + grid_errors))
        sequence_error = layer_store.sequence_error(fiscal_year, current_period)
        already_closed = layer_store.period_is_closed(fiscal_year, current_period)

        for issue in fatal_preview_errors:
            st.error(issue)
        if sequence_error and not already_closed:
            st.error(sequence_error)

        preview_disabled = bool(fatal_preview_errors or sequence_error or already_closed)
        if st.button("🚀 Calculate Preview & Generate Master Report", type="primary", disabled=preview_disabled):
            run_signature = _build_run_signature(
                fiscal_year, current_period, period_end_date, edited_master_grid, global_receipts_data
            )
            all_results, staged_layers, processing_errors = stage_batch_calculation(
                PRODUCTS, edited_master_grid, global_receipts_data, layer_store,
                fiscal_year, current_period, period_end_date,
            )
            counts = summarize_control_counts(all_results)
            run_issues = [('ERROR', message) for message in receipt_errors]
            run_issues.extend(('WARNING', message) for message in receipt_warnings)
            run_issues.extend(
                ('ERROR', f"Alias {item['Alias']} — {item['Product']}: {item['Error']}")
                for item in processing_errors
            )
            excel_data = export_master_excel(
                all_results, current_period, fiscal_year=fiscal_year, run_issues=run_issues,
                tolerances=app_settings.get_effective_tolerances(),
            )
            st.session_state['staged_fifo_run'] = {
                'period_key': selected_key, 'fiscal_year': fiscal_year, 'period': current_period,
                'as_of_date': period_end_date, 'run_signature': run_signature,
                'all_results': all_results, 'staged_layers': staged_layers,
                'processing_errors': processing_errors, 'input_errors': receipt_errors,
                'input_warnings': receipt_warnings, 'control_counts': counts,
                'excel_data': excel_data,
            }

        staged = st.session_state.get('staged_fifo_run')
        if staged and staged.get('period_key') == selected_key:
            current_signature = _build_run_signature(
                fiscal_year, current_period, period_end_date, edited_master_grid, global_receipts_data
            )
            stale_preview = current_signature != staged['run_signature']
            counts = staged['control_counts']

            st.markdown("#### Preview Results")
            st.caption("Nothing here is saved yet.")
            with st.expander("Help: preview vs. close"):
                st.markdown(
                    "**Close Period & Commit** saves these ending balances as the next period's beginning balances. "
                    "**Discard This Preview** clears it with nothing saved. Official FIFO layers are untouched until you commit."
                )
            metric_cols = st.columns(3)
            metric_cols[0].metric("PASS", counts['PASS'])
            metric_cols[1].metric("REVIEW", counts['REVIEW'])
            metric_cols[2].metric("FAIL", counts['FAIL'])

            if stale_preview:
                st.error("Inputs or settings changed after this preview was calculated. Recalculate before closing the period.")
            if staged['processing_errors']:
                st.error(f"{len(staged['processing_errors'])} product(s) encountered a calculation error.")
                st.dataframe(pd.DataFrame(staged['processing_errors']), width="stretch", hide_index=True)
            if staged['input_errors']:
                st.error("The preview excludes rejected receipt rows. Correct them before closing the period.")

            variance_rows = [{
                'Alias': res['alias'], 'Product': res['prod_name'],
                'Beginning Qty (Layers)': res['metrics']['beg_qty'],
                'Variance Qty': res['beg_variance_qty'],
                'Estimated Value Effect ($)': round(res['beg_variance_val'], 2),
            } for res in staged['all_results'] if abs(res['beg_variance_qty']) > app_settings.get_effective_tolerances()['qty_tolerance']]
            if variance_rows:
                with st.expander(f"⚖️ Beginning Inventory Review — {len(variance_rows)} product(s)", expanded=True):
                    st.caption("A beginning-quantity variance of 0 units is required before this period can be closed — "
                               "this blocks the commit below, it does not just require acknowledgment. The estimated "
                               "value effect prices the quantity difference at the stored layer average; it is not an "
                               "independent comparison to a book-value source.")
                    st.dataframe(pd.DataFrame(variance_rows), width="stretch", hide_index=True)

            _tol_for_preview = app_settings.get_effective_tolerances()
            value_variance_alert_rows = [{
                'Alias': res['alias'], 'Product': res['prod_name'],
                'Prior Accepted Variance ($)': round(res.get('value_variance_prior', 0.0), 2),
                'This Period Variance ($)': round(res.get('value_variance_current', 0.0), 2),
                'Drift ($)': round(res.get('value_variance_drift', 0.0), 2),
            } for res in staged['all_results']
              if not value_variance_drift_is_acceptable(res.get('value_variance_drift', 0.0), tolerances=_tol_for_preview)]
            if value_variance_alert_rows:
                with st.expander(f"🧾 Known Value Variance Changed — {len(value_variance_alert_rows)} product(s)", expanded=True):
                    st.caption(f"These products' accepted value variance moved outside the normal, accepted range of "
                               f"${_tol_for_preview['drift_min']:,.3f} to ${_tol_for_preview['drift_max']:,.2f} (adjustable "
                               "under Inventory Analysis → Settings) since the last period close. Drift inside that range (small rounding-level "
                               "movement in either direction) is treated as normal and isn't flagged. This requires "
                               "acknowledgment below but does not block closing the period — confirm it reflects a "
                               "deliberate reconciliation, not an unexplained change.")
                    st.dataframe(pd.DataFrame(value_variance_alert_rows), width="stretch", hide_index=True)

            st.download_button(
                label="📥 Download Preview Excel Report", data=staged['excel_data'],
                file_name=f"FIFO_Master_{selected_key}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )

            requires_review_ack = counts['REVIEW'] > 0 or bool(staged['input_warnings'])
            review_ack = True
            if requires_review_ack:
                review_ack = st.checkbox(
                    "I reviewed and accept the REVIEW items and input warnings for this period.",
                    key=f"review_ack_{selected_key}",
                )

            commit_blocked = bool(
                stale_preview or already_closed or sequence_error or staged['input_errors'] or
                staged['processing_errors'] or counts['FAIL'] > 0 or not review_ack or
                master_is_sample or receipts_is_sample
            )
            commit_col, discard_col = st.columns(2)
            with commit_col:
                if st.button(f"🔒 Close {selected_key} & Commit Ending Layers", disabled=commit_blocked, type="primary"):
                    try:
                        # Captured per-product so the Trends and Product Lookup tabs can
                        # read historical turnover/days-of-supply straight from period
                        # history rather than needing a fresh preview for every period.
                        product_period_summary = {
                            res['alias']: {
                                'beg_qty': res['metrics']['beg_qty'], 'beg_val': res['metrics']['beg_val'],
                                'purch_qty': res['metrics']['purch_qty'], 'purch_val': res['metrics']['purch_val'],
                                'usage_qty': res['metrics']['usage_qty'], 'usage_val': res['metrics']['usage_val'],
                                'end_qty': res['metrics']['end_qty'], 'end_val': res['metrics']['end_val'],
                            }
                            for res in staged['all_results']
                        }
                        layer_store.commit_period(
                            fiscal_year, current_period, staged['staged_layers'], staged['run_signature'],
                            counts, period_end_date, product_period_summary=product_period_summary,
                        )
                        st.session_state['last_fifo_report'] = {
                            'period_key': selected_key, 'excel_data': staged['excel_data'],
                            'control_counts': counts,
                        }
                        st.session_state.pop('staged_fifo_run', None)
                        st.session_state['app_flash'] = (
                            f"{selected_key} closed atomically: {counts['PASS']} PASS, "
                            f"{counts['REVIEW']} REVIEW, {counts['FAIL']} FAIL. These ending balances are now saved as "
                            f"the beginning balances for the next period, and a snapshot was auto-saved to disk at "
                            f"`{SNAPSHOT_DIR}/`."
                        )
                        st.rerun()
                    except Exception as exc:
                        st.error(
                            f"Period was not committed: {exc}\n\n"
                            "If this happened after the in-memory close but during the disk-backup step, the current "
                            "session may show the period as closed while it is NOT yet safely backed up to disk — use "
                            "'Save Current Layers Snapshot' above as an immediate manual fallback, and re-check the "
                            f"`{SNAPSHOT_DIR}/` folder's permissions before closing another period."
                        )
            with discard_col:
                if st.button("🗑️ Discard This Preview", help="Clears this preview without saving anything to memory."):
                    st.session_state.pop('staged_fifo_run', None)
                    st.session_state.pop(f"review_ack_{selected_key}", None)
                    st.session_state['app_flash'] = "Preview discarded. Nothing was saved for future periods."
                    st.rerun()

        last_report = st.session_state.get('last_fifo_report')
        if last_report and last_report.get('period_key') == selected_key and already_closed:
            st.success(f"{selected_key} is closed. The committed report remains available below.")
            st.download_button(
                label="📥 Download Committed Excel Report", data=last_report['excel_data'],
                file_name=f"FIFO_Master_{selected_key}_COMMITTED.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )

with tab_analysis:
    render_insights(layer_store)

with tab_history:
    render_history(layer_store, fiscal_year, current_period, selected_key)
