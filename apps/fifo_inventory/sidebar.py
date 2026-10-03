"""FIFO sidebar: period selection, closed-period history, snapshots, reopen/reset, and the P12 seed."""
from datetime import date, datetime

import pandas as pd
import streamlit as st

from .fifo_layer_store import SNAPSHOT_DIR
from .user_inputs import OPENING_SEED_FISCAL_YEAR, PERIOD_12_OPENING_LAYERS


def render_sidebar(layer_store):
    """Draw the sidebar and return (fiscal_year, current_period, period_end_date, selected_key)."""
    with st.sidebar:
        st.header("Settings")
        fiscal_year = int(st.number_input("Fiscal Year", min_value=2000, max_value=2200,
                                          value=datetime.now().year, step=1))
        current_period = st.selectbox("Select Fiscal Period", range(1, 14), index=11)
        period_end_date = st.date_input("Fiscal Period End Date", value=date.today(),
                                        help="Used for reproducible FIFO layer aging.")

        last_closed = st.session_state.get('fifo_last_closed_period')
        if last_closed:
            st.success(f"Latest closed period: {last_closed['period_key']}", icon=":material/lock:")
        else:
            st.info("No fiscal period has been closed in this session or snapshot.", icon=":material/info:")

        selected_key = layer_store.period_key(fiscal_year, current_period)
        if layer_store.period_is_closed(fiscal_year, current_period):
            st.warning(f"{selected_key} is already closed. It cannot be processed twice.", icon=":material/lock:")
        else:
            sequence_message = layer_store.sequence_error(fiscal_year, current_period)
            if sequence_message:
                st.warning(sequence_message)

        st.divider()
        st.header("Period History (Saved Balances)")
        period_history = st.session_state.get('fifo_period_history', [])
        if not period_history:
            st.info("No period has been closed yet — nothing is saved for future periods to read as a beginning "
                    "balance. Closing a period below is what saves it.", icon=":material/info:")
        else:
            history_rows = []
            for record in period_history:
                totals = record.get('ending_control_totals', {})
                counts = record.get('control_counts', {})
                history_rows.append({
                    'Period': record.get('period_key'),
                    'Closed (UTC)': record.get('committed_at_utc'),
                    'Ending Qty': totals.get('quantity'),
                    'Ending Value': totals.get('value'),
                    'PASS': counts.get('PASS'), 'REVIEW': counts.get('REVIEW'), 'FAIL': counts.get('FAIL'),
                })
            st.dataframe(pd.DataFrame(history_rows), width="stretch", hide_index=True)
            st.caption("Each closed period's ending balances are what the next period automatically reads as its "
                       "beginning balances. Use Reopen below to remove the most recent one from memory if needed.")

        st.divider()
        st.header("FIFO Layer Snapshot")
        st.caption("Snapshots include layers, closed-period history, control totals, and version metadata.")

        saved_snapshots = layer_store.list_snapshot_files()
        if saved_snapshots:
            st.caption(
                f"{len(saved_snapshots)} snapshot(s) auto-saved to disk at `{SNAPSHOT_DIR}/` — most recent: "
                f"`{saved_snapshots[0].name}`. These are written automatically every time a period is closed or "
                f"reopened; the download button below is an extra manual copy, not the primary backup anymore."
            )
        else:
            st.info(
                f"No snapshots have been auto-saved to disk yet at `{SNAPSHOT_DIR}/`. One will be written "
                f"automatically the first time a period is closed.", icon=":material/info:"
            )

        st.download_button(
            "Save Current Layers Snapshot",
            data=layer_store.to_json(),
            file_name=f"fifo_layers_snapshot_{selected_key}.json",
            mime="application/json",
        )
        restore_file = st.file_uploader("Restore Layers Snapshot", type=['json'], key="restore_layers")
        if restore_file is not None and st.button("Apply Restored Snapshot"):
            try:
                layer_store.from_json(restore_file.getvalue().decode('utf-8'))
                st.session_state['period12_autoseeded'] = True
                st.session_state['app_flash'] = "FIFO layers and period history restored from the validated snapshot."
                st.rerun()
            except Exception as exc:
                st.error(f"Snapshot was not applied: {exc}")

        if saved_snapshots:
            with st.expander("Restore a specific saved snapshot from disk", expanded=False):
                st.caption("Use this to roll back to an older closed period's snapshot instead of the latest one — "
                           "for example, recovering from a bad close that's further back than Reopen alone can undo.")
                snapshot_choice = st.selectbox(
                    "Snapshot file", options=saved_snapshots, format_func=lambda p: p.name, key="disk_snapshot_choice"
                )
                if st.button("Apply Selected Disk Snapshot"):
                    try:
                        layer_store.from_json(snapshot_choice.read_text(encoding='utf-8'))
                        st.session_state['period12_autoseeded'] = True
                        st.session_state['app_flash'] = f"FIFO layers and period history restored from {snapshot_choice.name}."
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Snapshot was not applied: {exc}")

        if layer_store.can_reopen_latest(fiscal_year, current_period):
            st.caption("Reopening removes this period's committed ending balances from memory and restores its "
                       "beginning balances instead, so the next period will no longer see it as history. Only the "
                       "most recently closed period can be reopened - repeat this one period at a time, most-recent "
                       "first, to remove several - because every later period's beginning balance depends on the one "
                       "before it, and deleting one out of order would silently break that chain.")
            confirm_reopen = st.checkbox(f"Confirm reopening {selected_key}", key="confirm_reopen")
            if st.button("Reopen Latest Closed Period (Remove From Memory)", disabled=not confirm_reopen):
                try:
                    layer_store.reopen_latest(fiscal_year, current_period)
                    st.session_state['app_flash'] = f"{selected_key} reopened and removed from saved history. Its beginning layers and known value variance were restored."
                    st.rerun()
                except Exception as exc:
                    st.error(str(exc))

        confirm_reset = st.checkbox("Confirm complete layer-history reset", key="confirm_layer_reset")
        if st.button("Reset All FIFO Layers", disabled=not confirm_reset):
            layer_store.reset_all()
            st.session_state['period12_autoseeded'] = True
            st.session_state['app_flash'] = "All FIFO layers, closed-period history, and known value variances were cleared."
            st.rerun()

        st.divider()
        st.header("Period 12 Opening Layers")
        st.caption(f"The embedded opening seed belongs to FY{OPENING_SEED_FISCAL_YEAR} Period 12 only.")
        if fiscal_year == OPENING_SEED_FISCAL_YEAR and current_period == 12:
            confirm_seed = st.checkbox("Confirm opening-layer re-seed", key="confirm_seed")
            if st.button("Re-Seed Period 12 Opening Layers", disabled=not confirm_seed):
                overwritten = layer_store.seed_opening_layers(PERIOD_12_OPENING_LAYERS)
                st.session_state['period12_autoseeded'] = True
                st.session_state.pop('staged_fifo_run', None)
                message = f"Seeded opening FIFO layers for {len(PERIOD_12_OPENING_LAYERS)} products."
                if overwritten:
                    message += f" Existing layers were replaced for aliases: {', '.join(map(str, overwritten))}."
                st.session_state['app_flash'] = message
                st.rerun()
        else:
            st.caption(f"Select FY{OPENING_SEED_FISCAL_YEAR} Period 12 to access the embedded opening seed.")

    return fiscal_year, current_period, period_end_date, selected_key
