# ingestion.py
import io
import math
import re
import pandas as pd

from .user_inputs import PRODUCTS, QTY_TOLERANCE, VALUE_TOLERANCE

def _finite_number(value, field_name):
    """Parse an accounting number and reject blanks, NaN, and infinities."""
    if isinstance(value, str):
        text = value.strip().replace(',', '').replace('$', '')
        if text.startswith('(') and text.endswith(')'):
            text = '-' + text[1:-1]
        value = text
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} is not numeric: {value!r}") from exc
    if not math.isfinite(parsed):
        raise ValueError(f"{field_name} must be a finite number.")
    return parsed

def extract_period(period_str):
    if pd.isna(period_str):
        return None
    text = str(period_str).upper().strip()
    patterns = [
        r'\bP(?:ERIOD)?\.?\s*D?\.?\s*0?(1[0-3]|[1-9])\b',
        r'\bPD\s*0?(1[0-3]|[1-9])\b',
        r'\b(1[0-3]|[1-9])\b',
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return int(match.group(1))
    return None

def _excel_sheet_names(uploaded_file):
    suffix = uploaded_file.name.rsplit('.', 1)[-1].lower() if '.' in uploaded_file.name else ''
    if suffix not in {'xlsx', 'xlsm'}:
        return None
    try:
        with pd.ExcelFile(io.BytesIO(uploaded_file.getvalue())) as xls:
            return xls.sheet_names
    except Exception as exc:
        raise ValueError(f"Could not read workbook sheets from {uploaded_file.name}: {exc}") from exc

def _read_uploaded_table(uploaded_file, sheet_name=None):
    suffix = uploaded_file.name.rsplit('.', 1)[-1].lower() if '.' in uploaded_file.name else ''
    try:
        if suffix == 'csv':
            return pd.read_csv(io.BytesIO(uploaded_file.getvalue()))
        if suffix in {'xlsx', 'xlsm'}:
            kwargs = {'sheet_name': sheet_name} if sheet_name is not None else {}
            result = pd.read_excel(io.BytesIO(uploaded_file.getvalue()), **kwargs)
            if isinstance(result, dict):
                raise ValueError(
                    f"{uploaded_file.name} has multiple sheets ({', '.join(result)}); "
                    f"a specific sheet must be selected before this file can be read."
                )
            return result
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f"Could not read {uploaded_file.name}: {exc}") from exc
    raise ValueError(f"Unsupported file type for {uploaded_file.name}. Upload CSV or XLSX.")

def _coerce_accounting_series(series):
    text = series.astype('string').str.strip()
    negative = text.str.match(r'^\(.*\)$', na=False)
    text = text.str.replace(r'[,$]', '', regex=True).str.replace(r'^\((.*)\)$', r'\1', regex=True)
    values = pd.to_numeric(text, errors='coerce')
    values.loc[negative & values.notna()] *= -1
    return values

def _excel_safe_text(value):
    text = '' if value is None or pd.isna(value) else str(value)
    return "'" + text if text.startswith(('=', '+', '-', '@')) else text

def find_column(df, candidates):
    norm_lookup = {re.sub(r'[^A-Z0-9]', '', str(c).upper()): c for c in df.columns}
    for cand in candidates:
        key = re.sub(r'[^A-Z0-9]', '', cand.upper())
        if key in norm_lookup:
            return norm_lookup[key]
    return None

def parse_period_column_header(col):
    """Recognize a Master Grid period column header and classify it.

    Returns a (period, kind) tuple where `period` is an int 1-13 and `kind`
    is 'qty' or 'value', or (None, None) if the header isn't a period column.

    Quantity columns are named the same way they always have been: a plain
    number ("1", "01") or a "P"/"PD"-style token ("PD01", "P.1").
    Value columns are the same token with a trailing "V" ("01V", "PD01V").
    """
    s = str(col).strip()
    upper = s.upper()

    kind = 'qty'
    candidate = upper
    if upper.endswith('V') and len(upper) > 1:
        stripped = upper[:-1]
        # Only treat the trailing V as a value marker if what's left still
        # parses as a period token below -- guards against a column literally
        # named "V" or a period token that legitimately ends in another letter.
        candidate = stripped
        kind = 'value'

    def _try_parse(text):
        try:
            v = int(float(text))
            if 1 <= v <= 13:
                return v
        except ValueError:
            pass
        m = re.fullmatch(r'[PD]{1,2}\.?0?(\d{1,2})', text)
        if m:
            v = int(m.group(1))
            if 1 <= v <= 13:
                return v
        return None

    period = _try_parse(candidate)
    if period is not None:
        return period, kind

    if kind == 'value':
        # The "strip trailing V" reading didn't work out -- fall back to
        # treating the whole header as a plain (non-value) token, in case a
        # quantity-only header happens to end in "V" for some other reason.
        period = _try_parse(upper)
        if period is not None:
            return period, 'qty'

    return None, None

def parse_master_grid_upload(df_upload):
    df = df_upload.copy()
    df.columns = [str(c).strip() for c in df.columns]

    alias_col = find_column(df, ['ALIAS', 'PRODUCT ALIAS']) or df.columns[0]

    qty_period_cols = {}
    value_period_cols = {}
    for col in df.columns:
        if col == alias_col:
            continue
        p, kind = parse_period_column_header(col)
        if p is None:
            continue
        if kind == 'value':
            value_period_cols[p] = col
        else:
            qty_period_cols[p] = col

    # Backward-compatible combined view: every period that has a quantity
    # column and/or a value column, keyed the same way calculations expect.
    period_cols = dict(qty_period_cols)

    df[alias_col] = pd.to_numeric(df[alias_col], errors='coerce')
    df = df.dropna(subset=[alias_col])
    df[alias_col] = df[alias_col].astype(int)

    updates = {}
    unmatched_aliases = []
    seen_aliases = set()
    duplicate_aliases = []
    invalid_cells = []
    for _, row in df.iterrows():
        alias = int(row[alias_col])
        if alias in seen_aliases:
            duplicate_aliases.append(alias)
        seen_aliases.add(alias)

        vals = {}
        for p, col in qty_period_cols.items():
            raw = row[col]
            if pd.isna(raw):
                continue
            try:
                val = _finite_number(raw, f"Alias {alias}, period {p:02d} quantity")
                if val < -QTY_TOLERANCE:
                    raise ValueError("quantity cannot be negative")
            except ValueError:
                invalid_cells.append({'alias': alias, 'period': p, 'field': 'qty', 'value': raw})
                continue
            vals.setdefault(p, {})['qty'] = val

        for p, col in value_period_cols.items():
            raw = row[col]
            if pd.isna(raw):
                continue
            try:
                val = _finite_number(raw, f"Alias {alias}, period {p:02d} value")
            except ValueError:
                invalid_cells.append({'alias': alias, 'period': p, 'field': 'value', 'value': raw})
                continue
            vals.setdefault(p, {})['value'] = val

        updates[alias] = vals
        if alias not in PRODUCTS and alias not in unmatched_aliases:
            unmatched_aliases.append(alias)

    return updates, period_cols, alias_col, unmatched_aliases, duplicate_aliases, invalid_cells, value_period_cols

def apply_updates_to_master_grid(grid_df, updates, period_cols, value_period_cols=None):
    """Write parsed Master Grid updates back into the working grid.

    `updates` is `{alias: {period: {'qty': ..., 'value': ...}}}` (either key
    may be absent for a given period if that column wasn't present/valid in
    the upload). Quantity columns are named "01".."13"; value columns are
    named "01V".."13V", matching parse_period_column_header's convention.
    """
    grid_df = grid_df.copy()
    applied_count = 0
    for alias, period_vals in updates.items():
        if alias not in PRODUCTS:
            continue
        mask = grid_df['PRODUCT ALIAS'] == alias
        if not mask.any():
            continue
        touched = False
        for p, fields in period_vals.items():
            if 'qty' in fields:
                col_name = f"{p:02d}"
                if col_name in grid_df.columns:
                    grid_df.loc[mask, col_name] = fields['qty']
                    touched = True
            if 'value' in fields:
                col_name = f"{p:02d}V"
                if col_name in grid_df.columns:
                    grid_df.loc[mask, col_name] = fields['value']
                    touched = True
        if touched:
            applied_count += 1
    return grid_df, applied_count

def validate_master_grid(grid_df, current_period):
    errors = []
    if 'PRODUCT ALIAS' not in grid_df.columns:
        return ["Master Grid is missing PRODUCT ALIAS."]

    end_col = f"{int(current_period):02d}"
    beginning_col = "13" if int(current_period) == 1 else f"{int(current_period) - 1:02d}"
    for col in (beginning_col, end_col):
        if col not in grid_df.columns:
            errors.append(f"Master Grid is missing required period column {col}.")
    aliases = pd.to_numeric(grid_df['PRODUCT ALIAS'], errors='coerce')
    duplicate_aliases = aliases[aliases.duplicated(keep=False) & aliases.notna()].astype(int).unique().tolist()
    if duplicate_aliases:
        errors.append(f"Master Grid contains duplicate aliases: {sorted(duplicate_aliases)}")

    missing_aliases = [alias for alias in PRODUCTS if not (aliases == alias).any()]
    if missing_aliases:
        errors.append(f"Master Grid is missing product aliases: {missing_aliases}")

    for col in (beginning_col, end_col):
        if col not in grid_df.columns:
            continue
        values = _coerce_accounting_series(grid_df[col])
        invalid_mask = values.isna() | ~values.map(lambda x: math.isfinite(float(x)) if pd.notna(x) else False)
        negative_mask = values < -QTY_TOLERANCE
        if invalid_mask.any():
            bad_aliases = aliases[invalid_mask].dropna().astype(int).tolist()[:10]
            errors.append(f"Period {col} contains nonnumeric or blank quantities for alias(es): {bad_aliases}")
        if negative_mask.any():
            bad_aliases = aliases[negative_mask].dropna().astype(int).tolist()[:10]
            errors.append(f"Period {col} contains negative ending quantities for alias(es): {bad_aliases}")

    # Value columns (01V..13V) are optional and never read by the calculation:
    # beginning value comes from the stored FIFO layers, ending value is derived.
    return errors

def prepare_receipts_upload(df_upload, current_period):
    df_rec = df_upload.copy()
    df_rec.columns = [str(c).strip().upper() for c in df_rec.columns]

    qty_col = find_column(df_rec, ['QUANTITY', 'QTY', 'QUANTITY RECEIVED', 'QTY RECEIVED'])
    price_col = find_column(df_rec, ['PRICE', 'TOTAL COST', 'TOTAL PRICE', 'AMOUNT', 'COST', 'TOTAL'])
    alias_col = find_column(df_rec, ['PRODUCT ALIAS', 'ALIAS', 'PRODUCT'])
    period_col = find_column(df_rec, ['PERIOD', 'PD', 'FISCAL PERIOD'])
    date_col = find_column(df_rec, ['DATE DELIVERED', 'DELIVERY DATE', 'DATE RECEIVED', 'DATE'])

    warnings = []
    errors = []
    used_position_fallback = []
    if not qty_col and len(df_rec.columns) > 9:
        qty_col = df_rec.columns[9]
        used_position_fallback.append(f"Quantity -> column 10 ('{qty_col}')")
    if not price_col and len(df_rec.columns) > 10:
        price_col = df_rec.columns[10]
        used_position_fallback.append(f"Total value -> column 11 ('{price_col}')")

    df_rec['_SOURCE_ROW'] = range(2, len(df_rec) + 2)

    missing = []
    for label, col in [('Product Alias', alias_col), ('Quantity', qty_col),
                       ('Total Value', price_col), ('Delivery Date', date_col)]:
        if not col:
            missing.append(label)
    if missing:
        errors.append("Receipts upload is missing required field(s): " + ", ".join(missing))
        return pd.DataFrame(), errors, warnings, {
            'qty_col': qty_col, 'price_col': price_col, 'alias_col': alias_col,
            'period_col': period_col, 'date_col': date_col, 'used_position_fallback': used_position_fallback,
            'rejected_rows': pd.DataFrame(), 'duplicate_rows': pd.DataFrame(),
        }

    rename_map = {
        qty_col: 'QUANTITY', price_col: 'PRICE', alias_col: 'PRODUCT ALIAS', date_col: 'DATE DELIVERED'
    }
    if period_col:
        rename_map[period_col] = 'SOURCE PERIOD'
    df_rec = df_rec.rename(columns=rename_map)

    df_rec['PRODUCT ALIAS'] = pd.to_numeric(df_rec['PRODUCT ALIAS'], errors='coerce')
    df_rec['QUANTITY'] = _coerce_accounting_series(df_rec['QUANTITY'])
    df_rec['PRICE'] = _coerce_accounting_series(df_rec['PRICE'])
    df_rec['DATE DELIVERED'] = pd.to_datetime(df_rec['DATE DELIVERED'], errors='coerce')

    if period_col:
        df_rec['PARSED_PERIOD'] = df_rec['SOURCE PERIOD'].apply(extract_period)
        selected = df_rec['PARSED_PERIOD'] == int(current_period)
        invalid_period = df_rec['PARSED_PERIOD'].isna() & df_rec['SOURCE PERIOD'].notna()
        if invalid_period.any():
            rows = df_rec.loc[invalid_period, '_SOURCE_ROW'].tolist()[:20]
            warnings.append(f"Could not interpret the fiscal period on source row(s) {rows}; those rows were not applied.")
        period_df = df_rec[selected].copy()
    else:
        period_df = df_rec.copy()
        warnings.append("No period column was found; all valid receipt rows are being treated as the selected period.")

    invalid_alias = period_df['PRODUCT ALIAS'].isna() | ~period_df['PRODUCT ALIAS'].isin(PRODUCTS)
    invalid_qty = period_df['QUANTITY'].isna() | (period_df['QUANTITY'] <= QTY_TOLERANCE)
    invalid_value = period_df['PRICE'].isna() | (period_df['PRICE'] < -VALUE_TOLERANCE)
    invalid_date = period_df['DATE DELIVERED'].isna()
    rejected_mask = invalid_alias | invalid_qty | invalid_value | invalid_date
    rejected = period_df[rejected_mask].copy()
    if not rejected.empty:
        rows = rejected['_SOURCE_ROW'].tolist()[:25]
        errors.append(
            f"{len(rejected)} selected-period receipt row(s) were rejected for invalid alias, quantity, total value, or date. "
            f"Source rows: {rows}"
        )

    valid = period_df[~rejected_mask].copy()
    valid['PRODUCT ALIAS'] = valid['PRODUCT ALIAS'].astype(int)

    duplicate_subset = ['PRODUCT ALIAS', 'DATE DELIVERED', 'QUANTITY', 'PRICE']
    for optional in ('PO #', 'INVOICE #'):
        if optional in valid.columns:
            duplicate_subset.append(optional)
    duplicate_mask = valid.duplicated(subset=duplicate_subset, keep=False)
    duplicate_rows = valid[duplicate_mask].copy()
    if not duplicate_rows.empty:
        warnings.append(
            f"{len(duplicate_rows)} receipt row(s) share all principal fields and may be duplicates; review before committing."
        )

    zero_value_rows = valid[(valid['PRICE'].abs() < VALUE_TOLERANCE) & (valid['QUANTITY'] > QTY_TOLERANCE)]
    if not zero_value_rows.empty:
        warnings.append(f"{len(zero_value_rows)} valid receipt row(s) carry quantity at zero total value.")

    debug = {
        'qty_col': qty_col, 'price_col': price_col, 'alias_col': alias_col,
        'period_col': period_col, 'date_col': date_col, 'used_position_fallback': used_position_fallback,
        'rejected_rows': rejected, 'duplicate_rows': duplicate_rows,
    }
    if used_position_fallback:
        warnings.append("Column-position fallback used: " + "; ".join(used_position_fallback))
    return valid, errors, warnings, debug