# fifo_calculations.py
"""
FIFO calculation engine and batch staging.

Pure computation layer: given beginning layers, a period's receipts, and an
ending quantity, work out what was consumed versus what remains on hand
(`calculate_fifo`), and stage a full multi-product batch run across every
product (`stage_batch_calculation`), including the digest/signature hashing
used to detect when a preview has gone stale (`_build_run_signature`).
Ending value is always derived from the FIFO layers; no value column of the
Master Grid is read.

Nothing here touches Streamlit widgets or session state directly. Layer
storage and persistence belong to fifo_layer_store.py; `stage_batch_calculation`
receives a `layer_store` instance as a plain argument rather than importing
FIFOLayerStore, so there is no circular dependency between the two modules.
"""
import copy
import hashlib
import json
from datetime import datetime

import pandas as pd

from .user_inputs import ENGINE_VERSION, QTY_TOLERANCE, VALUE_TOLERANCE
from .ingestion import _finite_number, _excel_safe_text
from .excel_export import _parse_layer_date, _check_chronology, _compute_summary_control_status
from .fifo_layer_store import _canonicalize_layer


def _uploaded_file_digest(uploaded_file):
    if uploaded_file is None:
        return None
    return hashlib.sha256(uploaded_file.getvalue()).hexdigest()

def _dataframe_digest(df, columns=None):
    if df is None:
        return hashlib.sha256(b'').hexdigest()
    working = df.copy()
    if columns is not None:
        available = [c for c in columns if c in working.columns]
        working = working[available]
    payload = working.to_json(orient='split', date_format='iso', default_handler=str)
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()

def _build_run_signature(fiscal_year, period, as_of_date, master_grid, receipts_df):
    payload = {
        'engine_version': ENGINE_VERSION,
        'fiscal_year': int(fiscal_year),
        'period': int(period),
        'as_of_date': as_of_date.isoformat(),
        'master_grid_digest': _dataframe_digest(master_grid),
        'receipts_digest': _dataframe_digest(receipts_df),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode('utf-8')).hexdigest()


def _first_present_text(row, column_names):
    """Return a normalized optional receipt attribute without requiring it.

    Current uploads do not necessarily carry UOM/cost-basis/transaction-type
    fields.  When one is available, however, it becomes part of the layer
    consolidation boundary so economically different receipts cannot be
    collapsed merely because their calculated unit costs happen to match.
    """
    for column in column_names:
        if column in row.index:
            value = _excel_safe_text(row.get(column, ''))
            if value:
                return value
    return ''


def _joined_unique(values):
    """Join nonblank source references in first-seen order."""
    return '; '.join(dict.fromkeys(value for value in values if value))


def _receipt_reference(source_row):
    parts = []
    if source_row.get('po'):
        parts.append(f"PO {source_row['po']}")
    if source_row.get('invoice'):
        parts.append(f"Invoice {source_row['invoice']}")
    return ' / '.join(parts)


def _same_receipt_layer_run(active_layer, candidate):
    """Whether a receipt may join the immediately preceding receipt layer.

    This deliberately performs run-length consolidation, not a global group
    by unit cost.  Consequently, an intervening differently priced receipt
    starts a new FIFO layer and later receipts cannot merge backward across it.
    Unit-cost equality is intentionally exact: the engine never approximates
    strict FIFO merely to make the report shorter.
    """
    return (
        active_layer['_grouping_unit_cost'] == candidate['unit_cost']
        and active_layer.get('uom', '') == candidate.get('uom', '')
        and active_layer.get('cost_basis', '') == candidate.get('cost_basis', '')
        and active_layer.get('transaction_type', '') == candidate.get('transaction_type', '')
    )


def _consolidate_adjacent_receipt_layers(receipts):
    """Create calculation layers from sorted, validated receipt records.

    The returned layers retain receipt-level lineage in ``source_receipts``.
    Dates need not be consecutive calendar days; "adjacent" means consecutive
    in the material's FIFO receipt sequence.  Opening layers are never passed
    here, so consolidation cannot cross a fiscal-period carryforward boundary.
    """
    consolidated = []
    for receipt in receipts:
        candidate = copy.deepcopy(receipt)
        candidate['_grouping_unit_cost'] = candidate['unit_cost']
        candidate['date_end'] = candidate['date']
        candidate['date_range'] = candidate['date']
        candidate['receipt_count'] = 1
        candidate['source_receipts'] = [copy.deepcopy(candidate.pop('_source_receipt'))]

        if consolidated and _same_receipt_layer_run(consolidated[-1], candidate):
            active = consolidated[-1]
            active['qty'] += candidate['qty']
            active['total_value'] += candidate['total_value']
            active['unit_cost'] = active['total_value'] / active['qty']
            active['date_end'] = candidate['date']
            active['date_range'] = (
                active['date'] if active['date'] == active['date_end']
                else f"{active['date']} through {active['date_end']}"
            )
            active['receipt_count'] += 1
            active['source_receipts'].extend(candidate['source_receipts'])
            active['po'] = _joined_unique(r['po'] for r in active['source_receipts'])
            active['invoice'] = _joined_unique(r['invoice'] for r in active['source_receipts'])
            active['item'] = _joined_unique(r['item'] for r in active['source_receipts'])
        else:
            consolidated.append(candidate)

    for layer_number, layer in enumerate(consolidated, 1):
        layer.pop('_grouping_unit_cost', None)
        layer['layer_id'] = f"{layer['source_period']}-{layer_number:03d}"
        source_rows = layer['source_receipts']
        layer['source_references'] = _joined_unique(
            _receipt_reference(row) for row in source_rows
        )
        # Keep snapshots and generic Excel writers compatible by storing the
        # detailed lineage as JSON text rather than a nested Python object.
        layer['source_receipts'] = json.dumps(source_rows, ensure_ascii=False, separators=(',', ':'))
    return consolidated


def calculate_fifo(beginning_layers, receipts_df, ending_qty, period=None, fiscal_year=None, as_of_date=None):
    if as_of_date is None:
        as_of_date = datetime.now().date()
    ending_qty = _finite_number(ending_qty, 'Ending inventory quantity')
    if ending_qty < -QTY_TOLERANCE:
        raise ValueError("Ending inventory quantity cannot be negative.")
    fiscal_prefix = f"FY{int(fiscal_year)}-" if fiscal_year else ""
    period_label = f"P{period:02d}" if period else "Current Period"
    layer_period_label = f"{fiscal_prefix}{period_label}"

    receipt_layers = []
    raw_receipts = []
    if not receipts_df.empty:
        receipts_df = receipts_df.copy()
        if 'DATE DELIVERED' not in receipts_df.columns:
            raise ValueError("Receipt data is missing DATE DELIVERED.")
        receipts_df['DATE DELIVERED'] = pd.to_datetime(receipts_df['DATE DELIVERED'], errors='coerce')
        if receipts_df['DATE DELIVERED'].isna().any():
            bad_rows = receipts_df.index[receipts_df['DATE DELIVERED'].isna()].tolist()[:5]
            raise ValueError(f"Receipt delivery date is invalid on source row(s): {bad_rows}")
        receipts_df['_FIFO_SOURCE_ORDER'] = range(len(receipts_df))
        receipts_df = receipts_df.sort_values(
            by=['DATE DELIVERED', '_FIFO_SOURCE_ORDER'], kind='mergesort'
        )

        validated_receipts = []
        for source_order, (_, row) in enumerate(receipts_df.iterrows(), 1):
            qty = _finite_number(row.get('QUANTITY'), 'Receipt quantity')
            val = _finite_number(row.get('PRICE'), 'Receipt total value')
            if qty <= QTY_TOLERANCE:
                raise ValueError(f"Receipt quantity must be positive; found {qty!r}.")
            if val < -VALUE_TOLERANCE:
                raise ValueError(f"Receipt total value cannot be negative; found {val!r}.")

            unit_cost = val / qty if qty > 0 else 0.0
            date_str = row['DATE DELIVERED'].strftime('%Y-%m-%d') if pd.notnull(row['DATE DELIVERED']) else 'Unknown'
            po = _excel_safe_text(row.get('PO #', ''))
            invoice = _excel_safe_text(row.get('INVOICE #', ''))
            item = _excel_safe_text(row.get('ITEMS', ''))
            uom = _first_present_text(row, ('UOM', 'UNIT OF MEASURE', 'UNIT'))
            cost_basis = _first_present_text(row, ('COST BASIS', 'COST_BASIS'))
            transaction_type = _first_present_text(row, ('TRANSACTION TYPE', 'TRANSACTION_TYPE', 'TYPE'))

            raw_receipts.append({
                'item': item, 'date': date_str, 'po': po, 'invoice': invoice,
                'unit_cost': unit_cost, 'total_cost': val, 'qty': qty,
                'uom': uom, 'cost_basis': cost_basis,
                'transaction_type': transaction_type, 'source_order': source_order,
            })

            validated_receipts.append({
                'date': date_str, 'qty': qty, 'total_value': val, 'unit_cost': unit_cost,
                'po': po, 'invoice': invoice, 'item': item,
                'source_period': layer_period_label,
                'uom': uom, 'cost_basis': cost_basis,
                'transaction_type': transaction_type,
                '_source_receipt': {
                    'source_order': source_order, 'date': date_str, 'qty': qty,
                    'total_value': val, 'unit_cost': unit_cost, 'po': po,
                    'invoice': invoice, 'item': item, 'uom': uom,
                    'cost_basis': cost_basis, 'transaction_type': transaction_type,
                },
            })

        receipt_layers = _consolidate_adjacent_receipt_layers(validated_receipts)

    norm_beginning = []
    oh_counter = 1
    for raw_layer in beginning_layers:
        l = _canonicalize_layer(raw_layer)
        norm_beginning.append({
            'date': l.get('date', 'Unknown'),
            'date_end': l.get('date_end', l.get('date', 'Unknown')),
            'date_range': l.get('date_range', l.get('date', 'Unknown')),
            'qty': l['qty'],
            'unit_cost': l['unit_cost'],
            'total_value': l['total_value'],
            'po': l.get('po', ''), 'invoice': l.get('invoice', ''), 'item': l.get('item', ''),
            'uom': l.get('uom', ''), 'cost_basis': l.get('cost_basis', ''),
            'transaction_type': l.get('transaction_type', ''),
            'receipt_count': l.get('receipt_count', 0),
            'source_references': l.get('source_references', ''),
            'source_receipts': l.get('source_receipts', ''),
            'source_period': l.get('source_period', ''),
            'layer_id': l.get('layer_id') or f"OPEN-{oh_counter:03d}"
        })
        if not l.get('layer_id'):
            oh_counter += 1

    all_layers = norm_beginning + receipt_layers
    for layer in all_layers:
        layer['original_qty'] = layer['qty']
        layer['original_value'] = layer['total_value']

    if not _check_chronology(all_layers):
        raise ValueError("FIFO layers are not in non-decreasing chronological order.")

    beg_qty = sum(l['qty'] for l in norm_beginning)
    beg_val = sum(l['total_value'] for l in norm_beginning)
    purch_qty = sum(l['qty'] for l in receipt_layers)
    purch_val = sum(l['total_value'] for l in receipt_layers)

    usage_qty = beg_qty + purch_qty - ending_qty
    remaining_usage = usage_qty
    variance_qty = 0
    if remaining_usage < 0:
        variance_qty = abs(remaining_usage)
        remaining_usage = 0

    on_hand = []
    depleted = []
    oh_seq = 0
    dep_seq = 0

    for layer in all_layers:
        original_qty = layer['original_qty']
        original_value = layer['original_value']
        unit_cost = original_value / original_qty if original_qty > QTY_TOLERANCE else 0.0
        parsed_date = _parse_layer_date(layer['date'])
        age_days = (as_of_date - parsed_date).days if parsed_date else None

        consumed = min(original_qty, remaining_usage) if remaining_usage > 0 else 0
        remaining_usage -= consumed
        remaining_qty = original_qty - consumed
        if consumed <= QTY_TOLERANCE:
            consumed_value = 0.0
            remaining_value = original_value
        elif remaining_qty <= QTY_TOLERANCE:
            consumed_value = original_value
            remaining_value = 0.0
        else:
            consumed_value = original_value * (consumed / original_qty)
            remaining_value = original_value - consumed_value

        if consumed > 0.0001:
            dep_seq += 1
            depleted.append({
                'seq': dep_seq, 'layer_id': layer['layer_id'], 'date': layer['date'],
                'date_end': layer.get('date_end', layer['date']),
                'date_range': layer.get('date_range', layer['date']),
                'qty_available': original_qty, 'qty_consumed': consumed, 'qty_remaining': remaining_qty,
                'unit_cost': unit_cost, 'usage_value': consumed_value,
                'period': period_label,
                'source_period': layer.get('source_period', ''),
                'receipt_count': layer.get('receipt_count', 0),
                'source_references': layer.get('source_references', ''),
                'source_receipts': layer.get('source_receipts', ''),
                'po': layer.get('po', ''), 'invoice': layer.get('invoice', ''),
                'item': layer.get('item', ''), 'uom': layer.get('uom', ''),
                'status': 'Fully Depleted' if remaining_qty <= QTY_TOLERANCE else 'Partially Depleted'
            })

        if remaining_qty > QTY_TOLERANCE:
            oh_seq += 1
            remaining_unit_cost = remaining_value / remaining_qty
            on_hand.append({
                'seq': oh_seq, 'layer_id': layer['layer_id'], 'date': layer['date'],
                'date_end': layer.get('date_end', layer['date']),
                'date_range': layer.get('date_range', layer['date']),
                'original_qty': original_qty, 'qty_depleted': consumed,
                'qty': remaining_qty,
                'unit_cost': remaining_unit_cost,
                'total_value': remaining_value,
                'source_period': layer.get('source_period', ''),
                'age_days': age_days, 'po': layer.get('po', ''), 'invoice': layer.get('invoice', ''),
                'item': layer.get('item', ''), 'uom': layer.get('uom', ''),
                'cost_basis': layer.get('cost_basis', ''),
                'transaction_type': layer.get('transaction_type', ''),
                'receipt_count': layer.get('receipt_count', 0),
                'source_references': layer.get('source_references', ''),
                'source_receipts': layer.get('source_receipts', ''),
            })

    if remaining_usage > 0:
        variance_qty = -remaining_usage

    end_val = sum(l['total_value'] for l in on_hand)
    usage_val = sum(d['usage_value'] for d in depleted)

    value_variance = beg_val + purch_val - usage_val - end_val
    metrics = {
        'beg_qty': beg_qty, 'beg_val': beg_val, 'purch_qty': purch_qty, 'purch_val': purch_val,
        'usage_qty': usage_qty, 'usage_val': usage_val, 'end_qty': ending_qty, 'end_val': end_val,
        'variance_qty': variance_qty, 'value_variance': value_variance
    }

    return on_hand, depleted, raw_receipts, metrics

def _failed_product_result(alias, name, beginning_layers, expected_beg_qty, ending_qty,
                           period, fiscal_year, as_of_date, error_message):
    safe_layers = []
    for seq, raw in enumerate(beginning_layers, 1):
        layer = _canonicalize_layer(raw)
        parsed_date = _parse_layer_date(layer.get('date', 'Unknown'))
        safe_layers.append({
            'seq': seq,
            'layer_id': layer.get('layer_id') or f"OPEN-{seq:03d}",
            'date': layer.get('date', 'Unknown'),
            'source_period': layer.get('source_period', ''),
            'original_qty': layer['qty'], 'qty_depleted': 0.0, 'qty': layer['qty'],
            'unit_cost': layer['unit_cost'], 'total_value': layer['total_value'],
            'age_days': (as_of_date - parsed_date).days if parsed_date else None,
            'po': layer.get('po', ''), 'invoice': layer.get('invoice', ''),
        })
    actual_beg_qty = sum(l['qty'] for l in safe_layers)
    actual_beg_val = sum(l['total_value'] for l in safe_layers)
    metrics = {
        'beg_qty': actual_beg_qty, 'beg_val': actual_beg_val,
        'purch_qty': 0.0, 'purch_val': 0.0, 'usage_qty': 0.0, 'usage_val': 0.0,
        'end_qty': ending_qty, 'end_val': actual_beg_val,
        'variance_qty': actual_beg_qty - ending_qty, 'value_variance': 0.0,
    }
    avg_cost = actual_beg_val / actual_beg_qty if actual_beg_qty else 0.0
    return {
        'alias': alias, 'prod_name': name, 'metrics': metrics, 'receivings': [],
        'depleted': [], 'on_hand': safe_layers,
        'beg_variance_qty': actual_beg_qty - expected_beg_qty,
        'beg_variance_val': (actual_beg_qty - expected_beg_qty) * avg_cost,
        'alias_found': True, 'alias_ambiguous': False,
        'processing_error': str(error_message),
    }

def stage_batch_calculation(products, master_grid, receipts_df, layer_store, fiscal_year,
                           current_period, as_of_date):
    all_results = []
    staged_layers = layer_store.all_layers()
    processing_errors = []
    beginning_col = "13" if int(current_period) == 1 else f"{int(current_period) - 1:02d}"
    ending_col = f"{int(current_period):02d}"

    for alias, name in products.items():
        prod_rows = master_grid[pd.to_numeric(master_grid['PRODUCT ALIAS'], errors='coerce') == alias]
        alias_found = not prod_rows.empty
        alias_ambiguous = len(prod_rows) > 1
        expected_beg_qty = 0.0
        ending_qty = 0.0
        beginning_layers = layer_store.sync_beginning(alias, 0.0)
        beg_variance_qty = 0.0
        beg_variance_val = 0.0

        try:
            if alias_ambiguous:
                raise ValueError("Product alias appears more than once in the Master Grid.")
            if not alias_found:
                raise ValueError("Product alias was not found in the Master Grid.")

            expected_beg_qty = _finite_number(prod_rows.iloc[0][beginning_col], f"Alias {alias} beginning quantity")
            ending_qty = _finite_number(prod_rows.iloc[0][ending_col], f"Alias {alias} ending quantity")

            beginning_layers = layer_store.sync_beginning(alias, expected_beg_qty)
            actual_beg_qty = sum(_canonicalize_layer(l)['qty'] for l in beginning_layers)
            actual_beg_val = sum(_canonicalize_layer(l)['total_value'] for l in beginning_layers)
            beg_variance_qty = actual_beg_qty - expected_beg_qty
            avg_cost = actual_beg_val / actual_beg_qty if actual_beg_qty else 0.0
            beg_variance_val = beg_variance_qty * avg_cost

            product_receipts = (
                receipts_df[receipts_df['PRODUCT ALIAS'] == alias] if not receipts_df.empty else pd.DataFrame()
            )
            on_hand, depleted, raw_receipts, metrics = calculate_fifo(
                beginning_layers, product_receipts, ending_qty,
                period=current_period, fiscal_year=fiscal_year, as_of_date=as_of_date,
            )
            result = {
                'alias': alias, 'prod_name': name, 'metrics': metrics,
                'receivings': raw_receipts, 'depleted': depleted, 'on_hand': on_hand,
                'beg_variance_qty': beg_variance_qty, 'beg_variance_val': beg_variance_val,
                'alias_found': alias_found, 'alias_ambiguous': alias_ambiguous,
                'processing_error': None,
            }
            staged_layers[alias] = copy.deepcopy(on_hand)
        except Exception as exc:
            processing_errors.append({'Alias': alias, 'Product': name, 'Error': str(exc)})
            result = _failed_product_result(
                alias, name, beginning_layers, expected_beg_qty, ending_qty,
                current_period, fiscal_year, as_of_date, str(exc),
            )
            result['alias_found'] = alias_found
            result['alias_ambiguous'] = alias_ambiguous

        all_results.append(result)

    return all_results, staged_layers, processing_errors

def summarize_control_counts(all_results):
    counts = {'PASS': 0, 'REVIEW': 0, 'FAIL': 0}
    for result in all_results:
        status = _compute_summary_control_status(
            result, alias_found=result.get('alias_found', True),
            alias_ambiguous=result.get('alias_ambiguous', False),
        )['overall_status']
        if status.startswith('FAIL'):
            counts['FAIL'] += 1
        elif status.startswith('REVIEW'):
            counts['REVIEW'] += 1
        else:
            counts['PASS'] += 1
    return counts
