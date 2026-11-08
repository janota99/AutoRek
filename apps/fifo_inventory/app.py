# app.py — a page of the combined Streamlit app; run via ../../app.py
from datetime import timedelta

import pandas as pd
import streamlit as st

# ==========================================
# IMPORT SEPARATED MODULES
# ==========================================
from apps.fifo_inventory.user_inputs import (
    OPENING_SEED_FISCAL_YEAR, PRODUCTS,
    PERIOD_12_OPENING_LAYERS
)

from apps.fifo_inventory.ingestion import _excel_sheet_names, _read_uploaded_table
from apps.fifo_inventory.intake import (
    empty_ending_grid, failed_ending_report, input_exceptions, calendar_applies, clamp_late_receipts, period_window, prepare_period_receipts, receipt_register_counts, register_entries,
    product_exception_rows, read_ending_report,
)
from apps.fifo_inventory.materials import current_matcher, save_aliases
from shared.fiscal_calendar import locate
from apps.fifo_inventory.workbook_import import (
    list_sheets, plan_sheets, read_operations_workbook,
)

from apps.fifo_inventory.upload_templates import (
    build_master_grid_template, build_receipts_template, build_upload_templates_zip
)

from apps.fifo_inventory.excel_export import (
    export_master_excel, _compute_summary_control_status,
)

from apps.fifo_inventory import app_settings

from apps.fifo_inventory.fifo_layer_store import FIFOLayerStore, SNAPSHOT_DIR

from apps.fifo_inventory.fifo_calculations import (
    _uploaded_file_digest, _build_run_signature, _dataframe_digest,
    stage_batch_calculation, summarize_control_counts
)

from apps.fifo_inventory.sample_data import (
    build_master_grid_sample, build_receipts_sample, sample_token,
)

from apps.fifo_inventory.sidebar import render_sidebar, render_history
from apps.fifo_inventory.insights import render_insights
from shared.messages import friendly_error
from shared.sample_data import sample_downloads, sample_uploader
from apps.fifo_inventory.period_status import STEPS, period_status
from shared import fiscal_session, workflow
from shared.layout import DASHBOARD_SCRIPT
from shared.status import status_styler, status_tile
from shared.styles import inject_page

# ==========================================
# UI LAYOUT & INGESTION
# ==========================================
# Page title, icon, and wide layout come from st.navigation in the root app.py.
st.title(":material/inventory_2: Inventory Costing & Analytics")
st.caption("Example workflow: 13-period batch FIFO inventory tracker. Upload the two operations reports; every material is calculated at once.")

inject_page("fifo")

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
            friendly_error(
                "A saved snapshot couldn't be loaded automatically",
                "Your saved FIFO layers were not changed. Restore one manually from the sidebar, or check that "
                "the snapshot file isn't open in another program.",
                exc,
            )




_SUMMARY_LABELS = {
    'PASS': "Products passing", 'REVIEW': "Products to review", 'FAIL': "Products failing",
    'receipt_rows': "Receipt rows", 'beg_val': "Beginning value", 'purch_val': "Purchases value",
    'usage_val': "Usage value", 'end_val': "Ending value",
}
_MONEY_KEYS = ('beg_val', 'purch_val', 'usage_val', 'end_val')
_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _preview_summary(all_results, counts, receipt_rows):
    """The few figures worth comparing between two previews of the same period."""
    totals = {key: sum(res['metrics'][key] for res in all_results) for key in _MONEY_KEYS}
    return {**{k: counts[k] for k in ('PASS', 'REVIEW', 'FAIL')}, 'receipt_rows': receipt_rows, **totals}


def _input_labels(fiscal_year, period, as_of, grid, receipts):
    """Each input the preview depends on, named and digested, so a recalculation can say what changed."""
    return {
        'Fiscal year': str(fiscal_year), 'Period': f"{int(period):02d}", 'As-of date': as_of.isoformat(),
        'Ending inventory': _dataframe_digest(grid)[:12],
        'Receipts': _dataframe_digest(receipts)[:12],
    }


def _sheet_choice(upload, label, key):
    """The sheet to read from an uploaded workbook; asks only when there is a real choice."""
    if upload is None:
        return None
    try:
        sheets = _excel_sheet_names(upload)
    except ValueError as exc:
        st.error(str(exc))
        return None
    if sheets and len(sheets) > 1:
        return st.selectbox(f"This workbook has several sheets. Which one has the {label}?", sheets, key=key)
    return sheets[0] if sheets else None


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
    # Filled at the end of this tab, once the page knows what is loaded, validated, and previewed.
    flow_slot = st.empty()

    matcher = current_matcher()
    window = period_window(fiscal_year, current_period, fiscal_session.calendar_for_year(fiscal_year), period_end_date,
                           layer_store.last_closed_as_of())

    stored_cutoff = layer_store.receipt_cutoff()
    default_cutoff = window.start - timedelta(days=1) if window.start is not None else None

    # ------------------------------------------------------------------ 1. uploads
    with st.container(border=True):
        st.subheader(":material/upload_file: Upload the period's reports")
        st.caption(
            f"Calculating {selected_key}. Materials are matched by name or alias. Receipts count when their delivery "
            f"date is {window.describe()}. Beginning balances come from the last closed period's saved FIFO layers. "
            "Nothing is saved until you close the period.")

        # A sample is built from the layers as they are now, so it is rebuilt when the period, the
        # as-of date, or the layers change.
        _sample_token = sample_token(fiscal_year, current_period, period_end_date, layer_store)

        def _forget_preview():
            """A sample's numbers fed the preview; drop it with the sample."""
            st.session_state.pop('staged_fifo_run', None)

        st.markdown("**Operations workbook** (Raw Goods & Receivings)")
        ops_upload = st.file_uploader(
            "Upload the Raw Goods & Receivings workbook", type=['xlsx', 'xlsm'], key='ops_workbook_upload',
            label_visibility="collapsed",
            help="One upload fills both the receipts (RECEIVED LOG) and the ending quantities (PERIOD END sheet).")
        ops_received_override = ops_period_end_override = None
        if ops_upload is not None:
            try:
                _first_plan = plan_sheets(list_sheets(ops_upload.getvalue()), fiscal_year)
            except Exception as exc:
                _first_plan = None
                st.error(f"We couldn't open that workbook ({exc}).")
            if _first_plan is not None and _first_plan.problems:
                with st.expander("Choose the sheets to read", expanded=True):
                    for problem in _first_plan.problems:
                        st.warning(problem)
                    _auto = "(choose a sheet)"
                    picked_received = st.selectbox("Received log sheet", [_auto, *_first_plan.received_choices],
                                                   key='ops_received_override')
                    picked_period_end = st.selectbox("Period-end sheet", [_auto, *_first_plan.period_end_choices],
                                                     key='ops_period_end_override')
                    ops_received_override = None if picked_received == _auto else picked_received
                    ops_period_end_override = None if picked_period_end == _auto else picked_period_end
        st.caption("Receipts are read from the received log by delivery date; ending quantities come from the "
                   "PERIOD END sheet for this fiscal year. Operations' quantities are used exactly as reported.")

        _separate_open = st.query_params.get("demo") == "1" or bool(
            st.session_state.get('_sample_loaded_master_grid_upload')
            or st.session_state.get('_sample_loaded_receipts_upload'))
        with st.expander("Or upload the two reports separately", expanded=bool(_separate_open)):
            if ops_upload is not None:
                st.info("The workbook above is being used. Remove it to use separate files.")
            upload_col1, upload_col2 = st.columns(2)
            with upload_col1:
                st.markdown("**Period-ending inventory report**")
                master_upload, master_is_sample = sample_uploader(
                    "Upload the period-ending inventory report", key='master_grid_upload', types=['csv', 'xlsx', 'xlsm'],
                    sample_builder=lambda: build_master_grid_sample(layer_store, fiscal_year, current_period, period_end_date),
                    cache_token=_sample_token, on_clear=_forget_preview, label_visibility="collapsed",
                )
                master_sheet_choice = _sheet_choice(master_upload, "ending quantities", 'master_sheet_select')
            with upload_col2:
                st.markdown("**Receiving report**")
                upload_receipts, receipts_is_sample = sample_uploader(
                    "Upload the receiving report", key='receipts_upload', types=['csv', 'xlsx', 'xlsm'],
                    sample_builder=lambda: build_receipts_sample(layer_store, fiscal_year, current_period, period_end_date),
                    cache_token=_sample_token, on_clear=_forget_preview, label_visibility="collapsed",
                )
                receipts_sheet_choice = _sheet_choice(upload_receipts, "receipts", 'receipts_sheet_select')
                st.caption("The cost column is each receipt's total value. The whole year's log is fine.")

        if (master_is_sample or receipts_is_sample) and ops_upload is None:
            st.warning(
                "Demo data is loaded. The calculation works as usual, but closing is disabled so demo "
                "numbers can never become the official FIFO layers. Clear the sample files to close a period."
            )

        with st.expander("Receipt cutoff", expanded=False):
            if stored_cutoff is not None:
                st.caption(f"Receipts are tracked since {stored_cutoff:%b %d, %Y}. The opening FIFO layers are the "
                           "inventory position on that day and it does not change.")
            elif default_cutoff is None:
                st.caption("No fiscal calendar or closed period dates this period, so no receipt cutoff applies: "
                           "receipts are recognized by delivery date and period label.")
            else:
                st.caption("The opening FIFO layers are the inventory position on this day. Receipts dated on or before "
                           "it are presumed already included unless their period label assigns them to this period or a "
                           "later one. The first period you close fixes it permanently.")
                st.date_input("Receipt cutoff", value=default_cutoff, key='fifo_receipt_cutoff')

        with st.expander("Templates and sample files", expanded=False):
            st.caption("Correctly shaped workbooks prepared for the selected period, each with an Instructions sheet. "
                       "Any report with a material name or alias and a quantity also works.")
            tmpl_col1, tmpl_col2, tmpl_col3 = st.columns(3)
            with tmpl_col1:
                st.download_button(
                    "Ending inventory template",
                    data=build_master_grid_template(layer_store, fiscal_year, current_period),
                    file_name=f"Master_Grid_Upload_Template_P{current_period:02d}.xlsx", mime=_XLSX,
                )
            with tmpl_col2:
                st.download_button(
                    "Receiving template", data=build_receipts_template(current_period),
                    file_name=f"Receipts_Upload_Template_P{current_period:02d}.xlsx", mime=_XLSX,
                )
            with tmpl_col3:
                st.download_button(
                    "Both (.zip)", data=build_upload_templates_zip(layer_store, fiscal_year, current_period),
                    file_name="FIFO_Upload_Templates.zip", mime="application/zip",
                )
            sample_downloads([
                ("Ending inventory sample (.xlsx)", f"fifo_master_grid_P{current_period:02d}.xlsx",
                 build_master_grid_sample(layer_store, fiscal_year, current_period, period_end_date)[1]),
                ("Receiving sample (.xlsx)", f"fifo_receipts_P{current_period:02d}.xlsx",
                 build_receipts_sample(layer_store, fiscal_year, current_period, period_end_date)[1]),
            ], key="fifo")

    # ------------------------------------------------------------------ 2. read the reports
    ending = None
    ops = None
    known_receipts = receipt_register_counts(layer_store.receipt_register())
    # The explicit receipt cutoff: the opening layers are the position on this day, the register covers what
    # follows. Set once, by the first period closed with a register; until then it can be chosen here.
    stored_cutoff = layer_store.receipt_cutoff()
    default_cutoff = window.start - timedelta(days=1) if window.start is not None else None
    receipt_cutoff = stored_cutoff
    if stored_cutoff is None and default_cutoff is not None:
        receipt_cutoff = st.session_state.get('fifo_receipt_cutoff', default_cutoff)
    cutoff_layers = layer_store.cutoff_layers()
    cutoff_period = None
    if receipt_cutoff is not None:
        _calendar = fiscal_session.calendar_for_year(fiscal_year)
        if calendar_applies(_calendar, fiscal_year):
            cutoff_period = locate(_calendar, receipt_cutoff) or 0
        elif receipt_cutoff == default_cutoff:
            cutoff_period = int(current_period) - 1
    receipt_decisions = st.session_state.setdefault('receipt_decisions', {})
    receipts_data = pd.DataFrame()
    receipt_errors, receipt_warnings, receipt_debug = [], [], {}
    source_notes, sheet_problems = [], []

    if ops_upload is not None:
        master_is_sample = receipts_is_sample = False        # a real workbook replaces any demo files
        try:
            ops = read_operations_workbook(
                ops_upload.getvalue(), fiscal_year, current_period, matcher, window, known_receipts=known_receipts,
                cutoff=receipt_cutoff, cutoff_period=cutoff_period, cutoff_layers=cutoff_layers,
                decisions=receipt_decisions, calendar=fiscal_session.calendar_for_year(fiscal_year),
                received_sheet=ops_received_override,
                period_end_sheet=ops_period_end_override)
            ending, receipts_data = ops.ending, ops.receipts
            receipt_errors, receipt_warnings, receipt_debug = ops.receipt_errors, ops.receipt_warnings, ops.receipt_debug
            source_notes, sheet_problems = ops.notes, ops.problems
            receipts_source_present = ops.plan.received is not None
        except Exception as exc:
            sheet_problems = [f"We couldn't read the operations workbook ({exc}). Use the separate uploads if "
                              "it is a different kind of file."]
            receipts_source_present = False
    else:
        receipts_source_present = upload_receipts is not None
        if master_upload is not None:
            try:
                ending = read_ending_report(
                    _read_uploaded_table(master_upload, sheet_name=master_sheet_choice), matcher, current_period)
            except Exception as exc:
                ending = failed_ending_report(
                    current_period,
                    f"We couldn't read the ending inventory report ({exc}). Check that it lists each material with "
                    "its ending quantity.")
        if upload_receipts is not None:
            try:
                receipts_data, receipt_errors, receipt_warnings, receipt_debug = prepare_period_receipts(
                    _read_uploaded_table(upload_receipts, sheet_name=receipts_sheet_choice), matcher, window,
                    current_period, fiscal_year=fiscal_year, known_receipts=known_receipts,
                    cutoff=receipt_cutoff, cutoff_period=cutoff_period, cutoff_layers=cutoff_layers,
                    decisions=receipt_decisions)
                source_notes = receipt_debug.get('notes', [])
            except Exception as exc:
                receipt_errors = [f"We couldn't read the receiving report ({exc}). Check that it has a material, "
                                  "delivery date, quantity and total cost for each row."]
                receipt_debug = {'structural': list(receipt_errors)}
    if ops is None and ending is not None:
        source_notes = source_notes + ending.notes
    # Late receipts join the queue after the layers already carried (see clamp_late_receipts); the register
    # keeps the delivery date as reported.
    receipts_engine = clamp_late_receipts(receipts_data, layer_store.all_layers())
    ending_grid = ending.grid if ending is not None else empty_ending_grid(current_period)
    have_input = ending is not None or receipts_source_present

    sequence_error = layer_store.sequence_error(fiscal_year, current_period)
    already_closed = layer_store.period_is_closed(fiscal_year, current_period)
    no_layers_error = None
    if all(len(layer_store.get(alias)) == 0 for alias in PRODUCTS) and not already_closed:
        no_layers_error = ("No beginning FIFO layers are saved. Close the prior period, or restore a snapshot in "
                           "History & Snapshots, so this period has a beginning balance.")

    fatal_preview_errors = list(dict.fromkeys(
        (ending.errors if ending is not None else []) + receipt_errors + sheet_problems
        + ([no_layers_error] if no_layers_error else [])))

    # ------------------------------------------------------------------ 3. what needs a person
    exceptions = input_exceptions(ending, receipt_debug)

    def _status_line():
        parts = []
        if ending is not None:
            parts.append(f"{ending.matched} ending count(s) supplied; the other materials carry forward")
        if receipts_source_present and not receipts_data.empty:
            parts.append(f"{len(receipts_data)} receipt(s) in this period")
        elif receipts_source_present and not receipt_errors:
            parts.append("this period's receipts are already recognized in the register" if already_closed
                         else "no receipts dated in this period")
        return " · ".join(parts)

    with st.container(border=True):
        st.subheader(":material/fact_check: Results")
        if not have_input:
            st.info("Upload the operations workbook (or the two reports separately). Every material "
                    "carries its saved layers forward; receipts are added to the materials they name, and an ending "
                    "quantity depletes the oldest layers first.")
        elif not (ending is not None and ending.calendar_mismatch):
            # (while the calendar disagrees with the workbook, counts built on the wrong window would only mislead)
            st.caption(_status_line())
            if not receipts_source_present:
                st.caption("No receipts source was used: no receipts are added this period.")
            _unplaced = receipt_debug.get('unplaced_review')
            if isinstance(_unplaced, pd.DataFrame) and not _unplaced.empty:
                with st.expander(f"Old rows with unreadable dates, not used ({len(_unplaced)})", expanded=False):
                    st.caption("The delivery date could not be read and no period label names this fiscal year, so these "
                               "cannot be placed. They are listed only so you can check none belongs to this period.")
                    st.dataframe(_unplaced, width="stretch", hide_index=True)
            if source_notes:
                with st.expander(f"What was read ({len(source_notes)} note(s))", expanded=False):
                    for note in source_notes:
                        st.markdown(f"- {note}")
        _mismatch = ending.calendar_mismatch if ending is not None else None
        if _mismatch:
            with st.container(border=True):
                st.error("**Fiscal Period Mismatch — Action Required**\n\n"
                         "The selected fiscal period's dates do not match the reporting dates in the uploaded operations workbook.",
                         icon=":material/event_busy:")
                st.markdown(
                    "| Selected fiscal year | Selected period | Workbook period-end date | Workspace calendar period-end date |\n"
                    "|---|---|---|---|\n"
                    f"| FY{_mismatch['fiscal_year']} | Period {_mismatch['period']:02d} | "
                    f"**{_mismatch['workbook_date']:%b %d, %Y}** (sheet '{_mismatch['sheet']}') | "
                    f"**{_mismatch['calendar_date']:%b %d, %Y}** |")
                st.markdown(
                    "Update the fiscal calendar in Workspace to resolve this discrepancy. FIFO calculations and Excel "
                    "downloads will become available once the dates match.")
                if _mismatch['periods_differing'] > 1:
                    st.caption(f"{_mismatch['periods_differing']} of {_mismatch['periods_compared']} periods in the workbook "
                               "disagree with the saved calendar, which usually means the calendar for this fiscal year "
                               "has not been entered yet.")
                try:
                    st.page_link(DASHBOARD_SCRIPT, label="Open Workspace to update the fiscal calendar",
                                 icon=":material/calendar_month:")
                except Exception:   # a page run on its own, outside the suite's navigation, has no Workspace to link to
                    st.markdown("[Open Workspace to update the fiscal calendar](/workspace)")
        if no_layers_error and have_input:
            st.error(no_layers_error)
        if sequence_error and not already_closed:
            st.error(sequence_error)

        if exceptions:
            blocking = sum(1 for e in exceptions if e['Severity'] == 'ERROR')
            title = (f"Needs your attention: {blocking} to fix" if blocking else "Heads up (nothing blocks closing)")
            with st.expander(title, expanded=True):
                table = pd.DataFrame(exceptions)
                table['Severity'] = table['Severity'].map({'ERROR': 'Fix', 'WARNING': 'Heads up'})
                st.dataframe(table, width="stretch", hide_index=True)

        review_frame = pd.concat([f for f in (receipt_debug.get('review_rows'), receipt_debug.get('decided_rows'))
                                  if isinstance(f, pd.DataFrame) and not f.empty], ignore_index=True) \
            if any(isinstance(f, pd.DataFrame) and not f.empty
                   for f in (receipt_debug.get('review_rows'), receipt_debug.get('decided_rows'))) else pd.DataFrame()
        if not review_frame.empty and not _mismatch:
            def _decide(widget_key, receipt_key):
                choice = st.session_state.get(widget_key)
                mapped = {"Include in this period": 'include', "Already in the opening layers": 'opening'}.get(choice)
                if mapped:
                    receipt_decisions[receipt_key] = mapped
                else:
                    receipt_decisions.pop(receipt_key, None)

            _choices = ["(choose)", "Include in this period", "Already in the opening layers"]
            with st.expander(f"Receipts to confirm once ({len(review_frame)})", expanded=True):
                st.caption("These deliveries are dated on or before the receipt cutoff but are assigned to this period, and "
                           "the opening layers cannot show whether they were already included. Each answer is remembered "
                           "when the period closes; nothing else needs a manual assignment.")
                for _, row in review_frame.iterrows():
                    current = receipt_decisions.get(row['_KEY'])
                    st.selectbox(
                        f"{row['MATERIAL']} - {row['QUANTITY']:,.0f} units, ${row['TOTAL COST']:,.2f}, delivered "
                        f"{row['DELIVERED']} ({row['REPORTED PERIOD']}). {row['WHY']}",
                        _choices, index={None: 0, 'include': 1, 'opening': 2}[current],
                        key=f"rv::{row['KEY']}", on_change=_decide, args=(f"rv::{row['KEY']}", row['_KEY']))
        late_rows = receipt_debug.get('late_rows')
        if isinstance(late_rows, pd.DataFrame) and not late_rows.empty and not _mismatch:
            with st.expander(f"Late receipts picked up ({len(late_rows)})", expanded=True):
                st.caption("These deliveries are dated before this period but were never processed in an earlier one, "
                           f"so they are recognized in {selected_key}. Delivery date and reported period are kept.")
                st.dataframe(late_rows.astype(str), width="stretch", hide_index=True)
        for warning in receipt_warnings + (ending.warnings if ending is not None else []):
            st.warning(warning)

        unknown_names = sorted({u['name'] for u in (ending.unmapped if ending is not None else [])}
                               | set(receipt_debug.get('unmapped', [])))
        if unknown_names:
            with st.expander(f"Teach the app {len(unknown_names)} unrecognized name(s)", expanded=bool(
                    receipt_debug.get('unmapped'))):
                st.caption("Pick the material each spelling means. It is remembered for every later period. "
                           "A name is never matched by guesswork.")
                options = [""] + [f"{alias} - {name}" for alias, name in PRODUCTS.items()]
                picks = {}
                for index, name in enumerate(unknown_names):
                    hint = matcher.suggestions(name, limit=1)
                    picks[name] = st.selectbox(
                        f"'{name}'" + (f" (maybe {hint[0]})" if hint else ""), options, key=f"alias_pick_{index}")
                if st.button("Save spellings", disabled=not any(picks.values())):
                    try:
                        save_aliases({n: int(p.split(' - ', 1)[0]) for n, p in picks.items() if p})
                        st.session_state['app_flash'] = "Spellings saved. They apply to this and later periods."
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Spellings were not saved: {exc}")

        # -------------------------------------------------------------- 4. calculate when inputs change
        grid_ready = have_input and not fatal_preview_errors and not already_closed and not sequence_error
        if grid_ready:
            run_signature = _build_run_signature(
                fiscal_year, current_period, period_end_date, ending_grid, receipts_engine)
            existing = st.session_state.get('staged_fifo_run')
            if not (existing and existing.get('period_key') == selected_key
                    and existing.get('run_signature') == run_signature):
                all_results, staged_layers, processing_errors = stage_batch_calculation(
                    PRODUCTS, ending_grid, receipts_engine, layer_store,
                    fiscal_year, current_period, period_end_date,
                )
                counts = summarize_control_counts(all_results)
                run_issues = [('ERROR', message) for message in receipt_errors]
                run_issues.extend(('WARNING', message) for message in receipt_warnings)
                run_issues.extend(('WARNING', message) for message in (ending.warnings if ending else []))
                run_issues.extend(
                    ('ERROR', f"Alias {item['Alias']} — {item['Product']}: {item['Error']}")
                    for item in processing_errors
                )
                excel_data = export_master_excel(
                    all_results, current_period, fiscal_year=fiscal_year, run_issues=run_issues,
                    tolerances=app_settings.get_effective_tolerances(),
                    context={
                        'receipts': receipts_engine, 'receipt_debug': receipt_debug, 'control_counts': counts,
                        'ending_supplied': ending.matched if ending is not None else 0,
                        'exceptions': exceptions + product_exception_rows(
                            all_results, app_settings.get_effective_tolerances()),
                        'notes': source_notes, 'history': layer_store.period_history(),
                        'run_signature': run_signature,
                    },
                )
                summary_book = st.session_state.setdefault('fifo_preview_summaries', {})
                previous_summary = summary_book.get(selected_key)
                summary = _preview_summary(all_results, counts, len(receipts_data))
                summary_book[selected_key] = summary
                st.session_state['staged_fifo_run'] = {
                    'input_labels': _input_labels(fiscal_year, current_period, period_end_date,
                                                  ending_grid, receipts_engine),
                    'summary': summary, 'previous_summary': previous_summary,
                    'period_key': selected_key, 'fiscal_year': fiscal_year, 'period': current_period,
                    'as_of_date': period_end_date, 'run_signature': run_signature,
                    'all_results': all_results, 'staged_layers': staged_layers,
                    'processing_errors': processing_errors, 'input_errors': receipt_errors,
                    'input_warnings': receipt_warnings + (ending.warnings if ending else []),
                    'control_counts': counts, 'excel_data': excel_data,
                    'register_entries': register_entries(receipts_engine, selected_key) + register_entries(
                        receipt_debug.get('opening_rows'), selected_key, status='in opening layers'),
                }
        elif st.session_state.get('staged_fifo_run', {}).get('period_key') == selected_key:
            # Inputs are no longer valid: a calculation made from earlier inputs must not be closable.
            st.session_state.pop('staged_fifo_run', None)

        staged = st.session_state.get('staged_fifo_run')
        preview_state = None
        if staged and staged.get('period_key') == selected_key:
            counts = staged['control_counts']
            all_results = staged['all_results']
            product_rows = product_exception_rows(all_results, app_settings.get_effective_tolerances())
            needs_decision = counts['FAIL'] + counts['REVIEW']
            if counts['FAIL']:
                st.html(workflow.headline_html(
                    str(needs_decision), "material(s) need a decision",
                    f"{counts['FAIL']} fail their controls, so closing is blocked until they are fixed; "
                    f"{counts['REVIEW']} need review.", "bad"))
            elif counts['REVIEW']:
                st.html(workflow.headline_html(
                    str(needs_decision), "material(s) to review",
                    "Review them below. Closing accepts them.", "warn"))
            else:
                st.html(workflow.headline_html(
                    str(counts['PASS']), "materials pass every control", "The period is ready to close.", "good"))

            if product_rows:
                with st.expander(f"Materials needing a decision ({len(product_rows)})", expanded=True):
                    attention = pd.DataFrame(product_rows).rename(columns={'Item': 'Material', 'What': 'Why'})
                    attention['Severity'] = attention['Severity'].map({'ERROR': 'Fix', 'WARNING': 'Heads up'})
                    st.dataframe(attention[['Severity', 'Material', 'Why']], width="stretch", hide_index=True)

            totals = {k: sum(r['metrics'][k] for r in all_results) for k in _MONEY_KEYS}
            total_cols = st.columns(4)
            for col, (label, key) in zip(total_cols, (("Beginning", 'beg_val'), ("Purchases", 'purch_val'),
                                                       ("Usage", 'usage_val'), ("Ending", 'end_val'))):
                col.metric(label, f"${totals[key]:,.2f}")

            with st.expander(f"All {len(all_results)} materials", expanded=False):
                st.dataframe(pd.DataFrame([{
                    'Alias': r['alias'], 'Material': r['prod_name'],
                    'Beginning qty': r['metrics']['beg_qty'], 'Receipts qty': r['metrics']['purch_qty'],
                    'Usage qty': r['metrics']['usage_qty'], 'Ending qty': r['metrics']['end_qty'],
                    'Beginning $': round(r['metrics']['beg_val'], 2), 'Purchases $': round(r['metrics']['purch_val'], 2),
                    'Usage $': round(r['metrics']['usage_val'], 2), 'Ending $': round(r['metrics']['end_val'], 2),
                } for r in all_results]), width="stretch", hide_index=True)

            if staged['processing_errors']:
                st.error(f"{len(staged['processing_errors'])} material(s) couldn't be calculated, so they are not in this result.")
                st.dataframe(pd.DataFrame(staged['processing_errors']), width="stretch", hide_index=True)

            st.download_button(
                label="Download FIFO workpaper (Excel)", data=staged['excel_data'],
                file_name=f"FIFO_Master_{selected_key}.xlsx", mime=_XLSX,
            )

            accepted = counts['REVIEW'] + len(staged['input_warnings'])
            commit_blocked = bool(
                already_closed or sequence_error or staged['input_errors'] or staged['processing_errors']
                or counts['FAIL'] > 0 or master_is_sample or receipts_is_sample
            )
            preview_state = {
                'stale': False, 'changed': [],
                'fail': counts['FAIL'], 'review': counts['REVIEW'], 'warnings': len(staged['input_warnings']),
                'processing_errors': len(staged['processing_errors']), 'receipt_errors': len(staged['input_errors']),
                'review_ack': True, 'demo': bool(master_is_sample or receipts_is_sample),
            }
            if commit_blocked:
                why = period_status(
                    period_key=selected_key, has_grid_data=True, input_errors=fatal_preview_errors,
                    sequence_error=sequence_error if not already_closed else None, already_closed=already_closed,
                    preview=preview_state)['blockers']
                st.caption("Closing is blocked: " + " ".join(why))
            elif accepted:
                st.caption(f"Closing accepts the {counts['REVIEW']} review item(s) and "
                           f"{len(staged['input_warnings'])} warning(s) shown above.")
            if not commit_blocked and not already_closed:
                _opening_value = sum(r['metrics']['beg_val'] for r in all_results)
                if stored_cutoff is None and receipt_cutoff is not None:
                    st.warning(
                        f"**First close: this fixes the receipt cutoff at {receipt_cutoff:%b %d, %Y}.** The beginning "
                        f"layers above (${_opening_value:,.2f}) become the permanent opening inventory position; "
                        f"receipts dated on or before {receipt_cutoff:%b %d, %Y} are presumed already included in them. "
                        "To use a different day, change it under Receipt cutoff before closing. It cannot be changed "
                        "afterwards (only reopening this first period releases it).", icon=":material/lock_clock:")
                elif stored_cutoff is None:
                    st.warning("First close: no receipt cutoff applies (no fiscal calendar or closed period dates this "
                               "period).")
                else:
                    st.caption(f"Receipt cutoff {stored_cutoff:%b %d, %Y} (fixed). Closing adds this period's receipts to "
                               "the register; the opening position is not touched.")
            commit_col, discard_col = st.columns(2)
            with commit_col:
                if st.button(f"Close {selected_key} & save ending layers", disabled=commit_blocked, type="primary",
                             icon=":material/lock:"):
                    try:
                        # Captured per-product so the Trends and Product Lookup tabs can
                        # read historical turnover/days-of-supply straight from period
                        # history rather than needing a fresh calculation for every period.
                        product_period_summary = {
                            res['alias']: {
                                'beg_qty': res['metrics']['beg_qty'], 'beg_val': res['metrics']['beg_val'],
                                'purch_qty': res['metrics']['purch_qty'], 'purch_val': res['metrics']['purch_val'],
                                'usage_qty': res['metrics']['usage_qty'], 'usage_val': res['metrics']['usage_val'],
                                'end_qty': res['metrics']['end_qty'], 'end_val': res['metrics']['end_val'],
                            }
                            for res in all_results
                        }
                        layer_store.commit_period(
                            fiscal_year, current_period, staged['staged_layers'], staged['run_signature'],
                            counts, period_end_date, product_period_summary=product_period_summary,
                            recognized_receipts=staged['register_entries'],
                            receipt_cutoff=receipt_cutoff,
                        )
                        st.session_state['last_fifo_report'] = {
                            'period_key': selected_key, 'excel_data': staged['excel_data'],
                            'control_counts': counts,
                        }
                        st.session_state.pop('staged_fifo_run', None)
                        st.session_state['app_flash'] = (
                            f"{selected_key} closed: {counts['PASS']} PASS, "
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
                            "'Save Current Layers Snapshot' in History & Snapshots as an immediate manual fallback, and "
                            f"re-check the `{SNAPSHOT_DIR}/` folder's permissions before closing another period."
                        )
            with discard_col:
                if st.button("Discard this calculation", help="Clears it with nothing saved. It is recalculated "
                             "the next time an input changes.", icon=":material/delete:"):
                    st.session_state.pop('staged_fifo_run', None)
                    st.session_state['app_flash'] = "Calculation discarded. Nothing was saved for future periods."
                    st.rerun()

        last_report = st.session_state.get('last_fifo_report')
        if last_report and last_report.get('period_key') == selected_key and already_closed:
            st.success(f"{selected_key} is closed. The committed workpaper remains available below.")
            st.download_button(
                label="Download committed FIFO workpaper (Excel)", data=last_report['excel_data'],
                file_name=f"FIFO_Master_{selected_key}_COMMITTED.xlsx", mime=_XLSX,
            )

        with st.expander("Input details", expanded=False):
            if ending is not None:
                st.markdown(f"**Ending report:** read as {ending.layout or 'unrecognized layout'}; "
                            f"identifying columns {ending.detected.get('identifying columns')}.")
            if receipt_debug.get('qty_col') is not None:
                st.markdown(
                    f"**Receiving report columns:** material {receipt_debug.get('id_columns')}, "
                    f"date `{receipt_debug.get('date_col')}`, quantity `{receipt_debug.get('qty_col')}`, "
                    f"total cost `{receipt_debug.get('price_col')}`.")
            if not receipts_data.empty:
                st.markdown("**Receipts used in this period**")
                st.dataframe(receipts_data, width="stretch", hide_index=True)
            duplicates = receipt_debug.get('duplicate_rows', pd.DataFrame())
            if isinstance(duplicates, pd.DataFrame) and not duplicates.empty:
                st.markdown("**Possible duplicate receipts**")
                st.dataframe(duplicates, width="stretch", hide_index=True)
            for key, title in (('unplaced_rows', "Rows with no readable date (not used)"),
                               ('mislabelled_rows', "Rows labelled for this period but dated outside it (not used)"),
                               ('rejected_rows', "Rejected rows")):
                frame = receipt_debug.get(key)
                if isinstance(frame, pd.DataFrame) and not frame.empty:
                    st.markdown(f"**{title}**")
                    st.dataframe(frame.astype(str), width="stretch", hide_index=True)
            if ending is not None and not ending.grid.empty:
                st.markdown("**Ending quantities used**")
                st.dataframe(ending.grid, width="stretch", hide_index=True)

_has_grid_data = have_input
_flow = period_status(
    period_key=selected_key, has_grid_data=bool(_has_grid_data), input_errors=fatal_preview_errors,
    sequence_error=sequence_error if not already_closed else None, already_closed=already_closed,
    preview=preview_state,
)
_changes = []
if staged and staged.get('period_key') == selected_key and staged.get('previous_summary'):
    _changes = workflow.changes_since(
        staged['previous_summary'], staged['summary'], _SUMMARY_LABELS,
        {k: (lambda v: f"${v:,.2f}") for k in _MONEY_KEYS})
flow_slot.html(workflow.workflow_html(
    [(title, detail) for title, detail in zip(STEPS, _flow['details'])], _flow['step'],
    _flow['blockers'], _flow['ready'], tone=_flow['tone'], next_step=_flow['next_step'],
    changes=_changes, changes_title="Since the previous calculation of this period", label="Period progress",
))

with tab_analysis:
    render_insights(layer_store)

with tab_history:
    render_history(layer_store, fiscal_year, current_period, selected_key)
