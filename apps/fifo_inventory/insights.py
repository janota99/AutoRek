"""FIFO Insights & Settings tabs: layer aging, closed-period trends, product lookup, tolerances.

Everything here reads the official layers and closed-period history; nothing requires a preview.
"""
from datetime import date

import pandas as pd
import streamlit as st

from . import app_settings
from .excel_export import parse_layer_date
from .user_inputs import PRODUCTS


def render_insights(layer_store):
    st.divider()
    st.subheader("📊 Insights & Settings")
    st.caption("Reads directly from the FIFO layers and closed-period history on record — none of this requires "
               "running a preview first.")

    tabs = st.tabs(["📅 Inventory Aging", "📈 Trends", "🔎 Product Lookup", "⚙️ Settings"])
    with tabs[0]:
        _aging_tab(layer_store)
    with tabs[1]:
        _trends_tab(layer_store)
    with tabs[2]:
        _product_lookup_tab(layer_store)
    with tabs[3]:
        _settings_tab()


def _aging_tab(layer_store):
    st.caption("Ages every currently on-hand FIFO layer as of a chosen date, bucketed the way most inventory "
               "systems report aging: 0–30 / 31–60 / 61–90 / 90+ days.")
    aging_as_of = st.date_input("Age layers as of", value=date.today(), key="aging_as_of_date")

    aging_rows = []
    for alias, prod_name in PRODUCTS.items():
        for layer in layer_store.get(alias):
            if layer['qty'] <= 0:
                continue
            parsed_date = parse_layer_date(layer.get('date'))
            if parsed_date is not None:
                age_days = (aging_as_of - parsed_date).days
                bucket = ("0–30 days" if age_days <= 30 else
                          "31–60 days" if age_days <= 60 else
                          "61–90 days" if age_days <= 90 else
                          "90+ days")
            else:
                age_days = None
                bucket = "Unknown / unparsed date"
            aging_rows.append({
                'Alias': alias, 'Product': prod_name, 'Layer Date': layer.get('date'),
                'Age (Days)': age_days, 'Age Bucket': bucket,
                'Quantity': layer['qty'], 'Unit Cost': layer['unit_cost'], 'Value': layer['total_value'],
            })

    if not aging_rows:
        st.info("No on-hand FIFO layers are currently recorded — nothing to age yet.")
    else:
        aging_df = pd.DataFrame(aging_rows)
        bucket_order = ["0–30 days", "31–60 days", "61–90 days", "90+ days", "Unknown / unparsed date"]
        aging_df['Age Bucket'] = pd.Categorical(aging_df['Age Bucket'], categories=bucket_order, ordered=True)

        bucket_totals = aging_df.groupby('Age Bucket', observed=True)['Value'].sum().reindex(bucket_order).fillna(0.0)
        total_value = bucket_totals.sum()

        st.markdown("**By age bucket — total on-hand value**")
        st.bar_chart(bucket_totals)

        metric_cols = st.columns(len(bucket_order))
        for col, bucket in zip(metric_cols, bucket_order):
            val = float(bucket_totals.get(bucket, 0.0))
            pct = (val / total_value * 100) if total_value else 0.0
            col.metric(bucket, f"${val:,.0f}", f"{pct:.0f}% of on-hand")

        st.markdown("**By product**")
        pivot = aging_df.pivot_table(
            index=['Alias', 'Product'], columns='Age Bucket', values='Value',
            aggfunc='sum', observed=True, fill_value=0.0
        ).reindex(columns=bucket_order, fill_value=0.0)
        pivot['Total'] = pivot.sum(axis=1)
        st.dataframe(pivot.reset_index(), width="stretch", hide_index=True)

        with st.expander("🔍 Layer-level detail"):
            st.dataframe(
                aging_df.sort_values(['Alias', 'Age (Days)'], na_position='last'),
                width="stretch", hide_index=True
            )


def _trends_tab(layer_store):
    _trend_history = layer_store.period_history()
    if not _trend_history:
        st.info("No period has been closed yet — trends will appear here once at least one period is closed.")
    else:
        trend_rows = []
        for record in _trend_history:
            totals = record.get('ending_control_totals', {})
            counts = record.get('control_counts', {})
            trend_rows.append({
                'Period': record.get('period_key'),
                'As Of': record.get('as_of_date'),
                'Ending Qty': totals.get('quantity', 0.0),
                'Ending Value': totals.get('value', 0.0),
                'PASS': counts.get('PASS', 0), 'REVIEW': counts.get('REVIEW', 0), 'FAIL': counts.get('FAIL', 0),
            })
        trend_df = pd.DataFrame(trend_rows).set_index('Period')

        st.markdown("**Ending inventory value by period**")
        st.line_chart(trend_df[['Ending Value']])

        st.markdown("**Ending inventory quantity by period**")
        st.area_chart(trend_df[['Ending Qty']])

        st.markdown("**Control outcomes by period**")
        st.bar_chart(trend_df[['PASS', 'REVIEW', 'FAIL']])

        with st.expander("🔍 Underlying period-history table"):
            st.dataframe(trend_df.reset_index(), width="stretch", hide_index=True)

        turnover_rows = []
        for record in _trend_history:
            summary = record.get('product_period_summary', {})
            if not summary:
                continue
            total_usage_val = sum(p['usage_val'] for p in summary.values())
            total_beg_val = sum(p['beg_val'] for p in summary.values())
            total_end_val = sum(p['end_val'] for p in summary.values())
            avg_inv_val = (total_beg_val + total_end_val) / 2
            turnover_rows.append({
                'Period': record.get('period_key'),
                'Portfolio Turnover Ratio': (total_usage_val / avg_inv_val) if avg_inv_val > 0 else None,
            })

        st.markdown("**Portfolio-wide FIFO usage-to-average-inventory turnover, by period**")
        if turnover_rows:
            st.caption("Only available for periods closed after this feature was added — earlier periods didn't "
                       "persist the per-product usage/beginning/ending detail this needs.")
            turnover_df = pd.DataFrame(turnover_rows).set_index('Period')
            st.line_chart(turnover_df[['Portfolio Turnover Ratio']].dropna())
        else:
            st.caption("Will appear here once at least one period is closed under this version of the app — "
                       "older closed periods didn't record the per-product detail this needs.")


def _product_lookup_tab(layer_store):
    alias_options = sorted(PRODUCTS.keys())
    lookup_alias = st.selectbox(
        "Product", alias_options, format_func=lambda a: f"{a:02d} — {PRODUCTS[a]}", key="lookup_alias"
    )

    st.markdown(f"#### Alias {lookup_alias} — {PRODUCTS[lookup_alias]}")

    current_layers = layer_store.get(lookup_alias)
    current_qty = sum(l['qty'] for l in current_layers)
    current_val = sum(l['total_value'] for l in current_layers)
    lookup_metric_cols = st.columns(3)
    lookup_metric_cols[0].metric("On-Hand Qty", f"{current_qty:,.0f}")
    lookup_metric_cols[1].metric("On-Hand Value", f"${current_val:,.2f}")
    lookup_metric_cols[2].metric("Avg Unit Cost", f"${(current_val / current_qty):,.6f}" if current_qty else "—")

    st.markdown("**Current on-hand FIFO layers**")
    if not current_layers:
        st.info("No on-hand layers currently recorded for this product.")
    else:
        lookup_as_of = st.date_input("Age these layers as of", value=date.today(), key="lookup_as_of_date")
        layer_rows = []
        for layer in sorted(current_layers, key=lambda l: str(l.get('date', ''))):
            parsed_date = parse_layer_date(layer.get('date'))
            age_days = (lookup_as_of - parsed_date).days if parsed_date else None
            layer_rows.append({
                'Layer Date': layer.get('date'), 'Age (Days)': age_days,
                'Quantity': layer['qty'], 'Unit Cost': layer['unit_cost'], 'Value': layer['total_value'],
            })
        st.dataframe(pd.DataFrame(layer_rows), width="stretch", hide_index=True)

    st.markdown("**Period-over-period history**")
    history_rows = layer_store.product_period_history(lookup_alias)
    if not history_rows:
        st.caption("No closed-period history is available yet for this product under this version of the app.")
    else:
        hist_df = pd.DataFrame(history_rows)
        hist_df['Avg Inventory Value'] = (hist_df['beg_val'] + hist_df['end_val']) / 2
        hist_df['Turnover Ratio'] = hist_df.apply(
            lambda r: (r['usage_val'] / r['Avg Inventory Value']) if r['Avg Inventory Value'] > 0 else None, axis=1
        )

        as_of_dates = pd.to_datetime(hist_df['as_of_date'], errors='coerce')
        DEFAULT_PERIOD_DAYS = 28  # ~13 periods/year; used only when no prior period is available to measure from
        period_days = as_of_dates.diff().dt.days.fillna(DEFAULT_PERIOD_DAYS)
        hist_df['_period_days'] = period_days

        def _days_of_supply(row):
            if row['usage_qty'] <= 0 or row['_period_days'] <= 0:
                return None
            daily_usage = row['usage_qty'] / row['_period_days']
            return row['end_qty'] / daily_usage if daily_usage > 0 else None

        hist_df['Days of Supply'] = hist_df.apply(_days_of_supply, axis=1)

        display_df = hist_df[[
            'period_key', 'beg_qty', 'beg_val', 'purch_qty', 'purch_val',
            'usage_qty', 'usage_val', 'end_qty', 'end_val', 'Turnover Ratio', 'Days of Supply'
        ]].rename(columns={
            'period_key': 'Period', 'beg_qty': 'Beg Qty', 'beg_val': 'Beg Value',
            'purch_qty': 'Purch Qty', 'purch_val': 'Purch Value',
            'usage_qty': 'Usage Qty', 'usage_val': 'Usage Value',
            'end_qty': 'End Qty', 'end_val': 'End Value',
        })
        st.caption("Days of Supply uses the actual calendar days between this period's close and the prior one; "
                   "the first available period instead assumes a 28-day period (~13 periods/year) since there's "
                   "no prior close to measure from.")
        st.dataframe(display_df, width="stretch", hide_index=True)

        st.markdown("**Ending value over time**")
        chart_df = hist_df.set_index('period_key')[['end_val']].rename(columns={'end_val': 'Ending Value'})
        st.line_chart(chart_df)


def _settings_tab():
    st.caption("These tolerances drive every REVIEW/FAIL judgment call in the reconciliation report — how close "
               "a quantity or dollar comparison must be to count as tied out, and how much period-over-period "
               "movement in the accepted Known Value Variance is treated as normal rounding rather than flagged. "
               "Changing a value here takes effect on the very next preview or export; it does NOT retroactively "
               "change the PASS/REVIEW/FAIL outcome recorded on any period that's already closed — those are "
               "frozen historical fact as of when they were closed.")

    if app_settings.is_overridden():
        st.info("One or more tolerances below are currently overridden from their built-in defaults.")

    current_settings = app_settings.get_effective_tolerances()
    with st.form("tolerance_settings_form"):
        qty_tolerance_input = st.number_input(
            app_settings.field_label('qty_tolerance'), min_value=0.0,
            value=current_settings['qty_tolerance'], step=0.1, format="%.4f",
            help=app_settings.field_help('qty_tolerance'),
        )
        value_tolerance_input = st.number_input(
            app_settings.field_label('value_tolerance'), min_value=0.0,
            value=current_settings['value_tolerance'], step=0.01, format="%.4f",
            help=app_settings.field_help('value_tolerance'),
        )
        zero_cost_tolerance_input = st.number_input(
            app_settings.field_label('zero_cost_tolerance'), min_value=0.0,
            value=current_settings['zero_cost_tolerance'], step=0.0001, format="%.6f",
            help=app_settings.field_help('zero_cost_tolerance'),
        )
        drift_col1, drift_col2 = st.columns(2)
        with drift_col1:
            drift_min_input = st.number_input(
                app_settings.field_label('drift_min'),
                value=current_settings['drift_min'], step=0.001, format="%.4f",
                help=app_settings.field_help('drift_min'),
            )
        with drift_col2:
            drift_max_input = st.number_input(
                app_settings.field_label('drift_max'),
                value=current_settings['drift_max'], step=0.001, format="%.4f",
                help=app_settings.field_help('drift_max'),
            )

        settings_save_col, settings_reset_col = st.columns(2)
        with settings_save_col:
            settings_submitted = st.form_submit_button("💾 Save Settings", type="primary")
        with settings_reset_col:
            settings_reset_submitted = st.form_submit_button("↩ Reset All to Defaults")

    if settings_submitted:
        try:
            app_settings.save_settings({
                'qty_tolerance': qty_tolerance_input, 'value_tolerance': value_tolerance_input,
                'zero_cost_tolerance': zero_cost_tolerance_input,
                'drift_min': drift_min_input, 'drift_max': drift_max_input,
            })
            st.success("Settings saved. These tolerances now apply to every new preview and export.")
        except Exception as exc:
            st.error(f"Settings were not saved: {exc}")

    if settings_reset_submitted:
        app_settings.reset_to_defaults()
        st.success("Settings reset to the built-in defaults.")
        st.rerun()

    with st.expander("Built-in defaults, for reference"):
        st.json(app_settings.DEFAULT_TOLERANCES)
