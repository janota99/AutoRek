"""Transaction Cleanup logic: validation, GL account building, vendor/GL matching, and new-vendor checks.

A Streamlit port of the original VBA macro. No Streamlit calls, so everything here can be
unit tested with plain DataFrames. Also the neutral home for helpers shared with
vendor_reconciliation.py, such as clean_key_series."""

from __future__ import annotations

import hashlib
import re

import numpy as np
import pandas as pd


# =====================================================================
# CONSTANTS
# =====================================================================

DEFAULT_EXCLUDED_IDS = ["101806", "103173", "102825", "102898", "116000"]


MAIN_SHEET_NAME = "All Transactions"


NEW_VENDORS_LABEL = "New Vendors"


# Positional assumptions for the 11-column source file (0-based), matching
# the original macro's fixed layout: A=Vendor, C=Transaction ID,
# E=Account Number, F=Cost Center, G=Amount, J=Code.

VENDOR_COL_IDX = 0


TXN_ID_COL_IDX = 2


ACCOUNT_NUMBER_COL_IDX = 4


COST_CENTER_COL_IDX = 5


AMOUNT_COL_IDX = 6


CODE_COL_IDX = 9


LEFT_ALIGN_COL_IDX = (2, 3)


# =====================================================================
# CORE LOGIC - pure pandas/openpyxl, no Streamlit calls in this section.
# Kept isolated from the UI so it can be unit tested independently.
# =====================================================================
# Collapses Excel-numeric-cell artifacts like "101806.0" -> "101806". Only
# matches a pure digit string followed by ".0"/".00"/etc, so it never touches
# genuinely non-numeric IDs or text-typed values with meaningful leading
# zeros (those never had a decimal point appended in the first place, since
# pandas only produces the ".0" suffix for float64-typed Excel cells).

_TRAILING_DECIMAL_ZERO_RE = re.compile(r"^(\d+)\.0+$")


_TRAILING_DECIMAL_ZERO_PATTERN = r"^(\d+)\.0+$"


def clean_key_series(s: pd.Series) -> pd.Series:
    """Vectorized equivalent of the VBA CleanKey function: trim, strip
    non-breaking spaces/regular spaces/surrounding quotes, collapse
    whole-number float artifacts, uppercase. Ensures "101806", 101806.0,
    '"101806"', and " 101806 " all normalize to the same key."""
    cleaned = (
        s.fillna("")
        .astype(str)
        .str.strip()
        .str.replace("\xa0", "", regex=False)
        .str.replace(" ", "", regex=False)
        .str.strip('"')
        .str.strip("'")
    )
    cleaned = cleaned.str.replace(_TRAILING_DECIMAL_ZERO_PATTERN, r"\1", regex=True)
    return cleaned.str.upper()


def clean_key(value) -> str:
    """Scalar version of clean_key_series."""
    if pd.isna(value):
        return ""
    s = str(value).strip().replace("\xa0", "").replace(" ", "")
    s = s.strip('"').strip("'")
    m = _TRAILING_DECIMAL_ZERO_RE.match(s)
    if m:
        s = m.group(1)
    return s.upper()


def validate_inputs(df_source: pd.DataFrame, df_mapping: pd.DataFrame,
                     df_trial_balance: pd.DataFrame) -> list:
    """Schema/shape validation for all three input files. Collects every
    problem found rather than stopping at the first one, so the user sees
    everything that needs fixing in a single pass. Returns a list of
    human-readable messages (empty list = valid); each message identifies
    the workbook and, where relevant, the specific field or row."""
    errors = []

    if df_source.empty:
        errors.append("Source Transactions: the file is completely blank.")
    else:
        if df_source.shape[1] != 11:
            errors.append(
                f"Source Transactions: found {df_source.shape[1]} column(s); "
                "exactly 11 are required."
            )

        headers = list(df_source.columns)
        blank_positions = [
            i + 1 for i, h in enumerate(headers)
            if str(h).strip() == "" or str(h).lower().startswith("unnamed")
        ]
        if blank_positions:
            errors.append(
                f"Source Transactions: blank column header(s) at position(s) {blank_positions}."
            )

        header_counts = {}
        for h in headers:
            header_counts[h] = header_counts.get(h, 0) + 1
        dupe_headers = sorted(h for h, n in header_counts.items() if n > 1)
        if dupe_headers:
            errors.append(f"Source Transactions: duplicate column header(s): {dupe_headers}.")

        if df_source.shape[1] > AMOUNT_COL_IDX:
            amount_col = headers[AMOUNT_COL_IDX]
            numeric = pd.to_numeric(df_source[amount_col], errors="coerce")

            non_numeric_mask = numeric.isna() & df_source[amount_col].notna()
            non_numeric_rows = df_source.index[non_numeric_mask].tolist()
            if non_numeric_rows:
                # +2 converts a 0-based DataFrame index to a 1-based Excel
                # row number, accounting for the header row.
                excel_rows = [r + 2 for r in non_numeric_rows[:10]]
                more = f" (+{len(non_numeric_rows) - 10} more)" if len(non_numeric_rows) > 10 else ""
                errors.append(
                    f"Source Transactions: non-numeric Amount value(s) at row(s) "
                    f"{excel_rows}{more}."
                )

            # Blank Amount is treated as not permitted - a transaction needs
            # a dollar value for sales-tax review to mean anything.
            blank_mask = df_source[amount_col].isna()
            blank_rows = df_source.index[blank_mask].tolist()
            if blank_rows:
                excel_rows = [r + 2 for r in blank_rows[:10]]
                more = f" (+{len(blank_rows) - 10} more)" if len(blank_rows) > 10 else ""
                errors.append(
                    f"Source Transactions: blank Amount value(s) at row(s) "
                    f"{excel_rows}{more}."
                )

    if df_mapping.empty:
        errors.append("Vendor Mapping: the file is completely blank.")
    elif df_mapping.shape[1] < 5:
        errors.append(
            f"Vendor Mapping: found {df_mapping.shape[1]} column(s); at least 5 are "
            "required (Vendor, 2 unused, Taxability, Grouping)."
        )

    if df_trial_balance.empty:
        errors.append("Trial Balance: the file is completely blank.")
    elif df_trial_balance.shape[1] < 3:
        errors.append(
            f"Trial Balance: found {df_trial_balance.shape[1]} column(s); at least 3 "
            "are required."
        )

    return errors


def pad_numeric_segment(value, length: int, pad_side: str = "right"):
    """Validates value is a whole number of digits and pads it to `length`.
    pad_side='right' matches the original macro's (unusual) trailing-zero
    padding; pad_side='left' is the more conventional leading-zero padding.
    Returns None if the value is blank, non-numeric, non-whole, or too long.
    """
    if pd.isna(value):
        return None
    s = str(value).strip()
    if s == "":
        return None
    try:
        f = float(s)
        if f != int(f):
            return None
        s = str(int(f))
    except ValueError:
        pass
    if not s.isdigit():
        return None
    if len(s) > length:
        return None
    pad = "0" * (length - len(s))
    return (s + pad) if pad_side == "right" else (pad + s)


def build_gl_account(cost_center, account_number, pad_side: str = "right"):
    """Builds the 001-CCCCC-AAAAAA-000 GL account string, or None if either
    segment fails validation (invalid rows are skipped, not fatal - unlike
    the original macro, which aborted the entire run on a bad row)."""
    cc = pad_numeric_segment(cost_center, 5, pad_side)
    acct = pad_numeric_segment(account_number, 6, pad_side)
    if cc is None or acct is None:
        return None
    return f"001-{cc}-{acct}-000"


def detect_mapping_conflicts(df_mapping: pd.DataFrame) -> list:
    """Flags vendor IDs that appear more than once in the mapping file with
    two or more DIFFERENT, NON-BLANK Taxability/Grouping combinations.
    Identical duplicates are safe to consolidate and are not flagged. A
    blank row (not yet classified) sharing an ID with a classified row is
    NOT treated as a conflict either - it's an incomplete entry, not a
    disagreement, and the classified row wins (see the matching fix in
    process_transactions's mapping lookup, which prefers non-blank rows
    over blank ones regardless of which appears first in the file)."""
    if df_mapping.shape[1] < 5:
        return []
    m = df_mapping.copy()
    m["_key"] = clean_key_series(m.iloc[:, 0])
    m = m[m["_key"] != ""]
    tax_col = m.columns[3]
    grp_col = m.columns[4]
    m["_tax_norm"] = clean_key_series(m[tax_col])
    m["_grp_norm"] = clean_key_series(m[grp_col])

    def _display(v):
        return "(blank)" if pd.isna(v) or str(v).strip() == "" else str(v)

    errors = []
    for key, group in m.groupby("_key"):
        if len(group) <= 1:
            continue
        substantive = group[(group["_tax_norm"] != "") | (group["_grp_norm"] != "")]
        distinct_combos = substantive[["_tax_norm", "_grp_norm"]].drop_duplicates()
        if len(distinct_combos) > 1:
            vendor_display = group.iloc[0, 0]
            combos = sorted(set(
                f"{_display(row[tax_col])}/{_display(row[grp_col])}"
                for _, row in substantive.iterrows()
            ))
            errors.append(
                f"Vendor Mapping: conflicting entries for vendor '{vendor_display}' "
                f"(ID {key}): {'; '.join(combos)}"
            )
    return errors


def detect_trial_balance_conflicts(df_trial_balance: pd.DataFrame) -> list:
    """Flags GL accounts that appear more than once in the trial balance
    with two or more DIFFERENT, NON-BLANK Account Names. Same blank-aware
    rationale as detect_mapping_conflicts."""
    if df_trial_balance.shape[1] < 3:
        return []
    tb = df_trial_balance.copy()
    tb["_key"] = clean_key_series(tb.iloc[:, 2])
    tb = tb[tb["_key"] != ""]
    name_col = tb.columns[1]
    tb["_name_norm"] = clean_key_series(tb[name_col])

    def _display(v):
        return "(blank)" if pd.isna(v) or str(v).strip() == "" else str(v)

    errors = []
    for key, group in tb.groupby("_key"):
        if len(group) <= 1:
            continue
        substantive = group[group["_name_norm"] != ""]
        distinct_names = substantive["_name_norm"].drop_duplicates()
        if len(distinct_names) > 1:
            names = sorted(set(_display(v) for v in substantive[name_col]))
            errors.append(
                f"Trial Balance: conflicting Account Name(s) for GL account "
                f"'{key}': {'; '.join(names)}"
            )
    return errors


def process_transactions(df_source, df_mapping, df_trial_balance, excluded_ids, pad_side="right"):
    """Runs the full cleanup pipeline. Returns (result_df, new_vendor_flags,
    stats, removed_df)."""
    if df_source.shape[1] != 11:
        raise ValueError(f"Source file has {df_source.shape[1]} columns; exactly 11 are required.")

    headers = list(df_source.columns)
    df = df_source.copy().reset_index(drop=True)
    # +2 converts the 0-based DataFrame index to the 1-based Excel row
    # number, accounting for the header row - kept on every row (not just
    # removed ones) so it's available for the retained-row cross-reference
    # below.
    df["Source Excel Row"] = df.index + 2

    # Coerce Amount to real numeric dtype up front. validate_inputs already
    # confirmed every non-blank value converts cleanly, so errors="coerce"
    # here is a safety net, not the primary guard - but this assignment is
    # what actually matters: without writing the converted series BACK onto
    # the column, a text value like "125.50" passes validation yet still
    # flows through (and into the exported workbook) as text, which a SUM
    # formula silently ignores.
    amount_col = headers[AMOUNT_COL_IDX]
    df[amount_col] = pd.to_numeric(df[amount_col], errors="coerce")
    original_total = float(df[amount_col].sum(skipna=True))

    df["_vendor_key"] = clean_key_series(df.iloc[:, VENDOR_COL_IDX])
    df["_txn_key"] = clean_key_series(df.iloc[:, TXN_ID_COL_IDX])
    col_code = df.iloc[:, CODE_COL_IDX].fillna("").astype(str).str.strip()
    first_char = col_code.str[0].str.upper()
    df["_begins_with_letter"] = first_char.str.match(r"[A-Z]", na=False)

    excluded_set = set(clean_key(x) for x in excluded_ids)
    df["_is_excluded"] = df["_vendor_key"].isin(excluded_set) & (df["_vendor_key"] != "")

    # Duplicate detection only competes among rows that are otherwise
    # eligible to be kept (not already dropped for letter-prefix or
    # excluded-vendor reasons). Previously this was computed across ALL
    # rows regardless of other removal reasons: a row that was going to be
    # dropped anyway could still "claim" a transaction ID, causing a later,
    # genuinely valid row sharing that ID to be wrongly dropped too - in the
    # worst case, every row for a given transaction ID could vanish with no
    # trace. This intentionally departs from the original VBA macro's
    # behavior, which had the same flaw.
    eligible_mask = (~df["_begins_with_letter"]) & (~df["_is_excluded"])
    df["_is_duplicate"] = False
    eligible_keys = df.loc[eligible_mask, "_txn_key"]
    df.loc[eligible_mask, "_is_duplicate"] = (
        eligible_keys.duplicated(keep="first") & (eligible_keys != "")
    )

    df["_drop_reason"] = np.select(
        [df["_begins_with_letter"], df["_is_excluded"], df["_is_duplicate"]],
        ["letter", "excluded", "duplicate"],
        default="keep",
    )

    # For rows dropped as duplicates, record the Source Excel Row of the
    # surviving first-occurrence row sharing the same transaction ID - makes
    # it possible to go find "why was this specific row dropped" without
    # re-deriving it by hand.
    first_occurrence_row = (
        df.loc[eligible_mask & (df["_txn_key"] != "")]
        .drop_duplicates("_txn_key", keep="first")
        .set_index("_txn_key")["Source Excel Row"]
    )
    df["_retained_row_if_duplicate"] = ""
    dup_mask = df["_drop_reason"] == "duplicate"
    df.loc[dup_mask, "_retained_row_if_duplicate"] = (
        df.loc[dup_mask, "_txn_key"].map(first_occurrence_row).astype(int).astype(str)
    )

    stats = {
        "letter_rows_deleted": int((df["_drop_reason"] == "letter").sum()),
        "excluded_rows_deleted": int((df["_drop_reason"] == "excluded").sum()),
        "duplicate_rows_deleted": int((df["_drop_reason"] == "duplicate").sum()),
    }

    # Removed-row audit detail: every dropped row, with its original data,
    # its Source Excel Row, why it was removed, and (for duplicates) which
    # row was retained instead - so the cleanup can actually be audited
    # instead of trusting a bare count.
    removed_mask = df["_drop_reason"] != "keep"
    removed_cols = headers + ["Source Excel Row"]
    removed_df = df.loc[removed_mask, removed_cols].copy()
    removed_df["Removal Reason"] = df.loc[removed_mask, "_drop_reason"].map({
        "letter": "Code begins with a letter",
        "excluded": "Excluded vendor ID",
        "duplicate": "Duplicate transaction ID",
    })
    removed_df["Retained Row (Duplicate)"] = df.loc[removed_mask, "_retained_row_if_duplicate"]
    removed_total = float(df.loc[removed_mask, amount_col].sum(skipna=True))

    kept = df[df["_drop_reason"] == "keep"].copy()

    # --- Vendor mapping lookup (columns: 0=key, 3=Taxability, 4=Grouping) ---
    mapping = df_mapping.copy()
    mapping["_vendor_key"] = clean_key_series(mapping.iloc[:, 0])
    mapping["_tax_norm"] = clean_key_series(mapping[mapping.columns[3]])
    mapping["_grp_norm"] = clean_key_series(mapping[mapping.columns[4]])
    # A vendor ID can legitimately appear twice with one row blank (not yet
    # classified) and one row containing real data - detect_mapping_conflicts
    # doesn't treat that as a conflict, so this must independently guarantee
    # the classified row always wins, not just "whichever comes last in the
    # file" (which is what plain keep="last" would do).
    mapping["_is_blank"] = (mapping["_tax_norm"] == "") & (mapping["_grp_norm"] == "")
    mapping = mapping.sort_values("_is_blank", ascending=False)
    mapping = mapping[mapping["_vendor_key"] != ""].drop_duplicates("_vendor_key", keep="last")
    mapping_lookup = mapping.set_index("_vendor_key")[[mapping.columns[3], mapping.columns[4]]]
    mapping_lookup.columns = ["Taxability", "Grouping"]

    kept["_vendor_matched"] = kept["_vendor_key"].isin(mapping_lookup.index)
    kept = kept.merge(mapping_lookup, how="left", left_on="_vendor_key", right_index=True)
    kept["Taxability"] = kept["Taxability"].fillna("")
    kept["Grouping"] = kept["Grouping"].fillna("")

    stats["matched_vendor_rows"] = int(kept["_vendor_matched"].sum())
    stats["unmatched_vendor_rows"] = int((~kept["_vendor_matched"]).sum())

    # --- GL account build + trial balance lookup ---
    cost_center_col = headers[COST_CENTER_COL_IDX]
    account_number_col = headers[ACCOUNT_NUMBER_COL_IDX]
    kept["GL Account"] = kept.apply(
        lambda r: build_gl_account(r[cost_center_col], r[account_number_col], pad_side) or "",
        axis=1,
    )

    tb = df_trial_balance.copy()
    tb["_gl_key"] = clean_key_series(tb.iloc[:, 2])
    tb["_name_norm"] = clean_key_series(tb[tb.columns[1]])
    # Same rationale as the vendor mapping lookup above: a GL account with a
    # blank Account Name duplicate shouldn't be able to win over one with a
    # real name just because of file order.
    tb["_is_blank"] = tb["_name_norm"] == ""
    tb = tb.sort_values("_is_blank", ascending=False)
    tb = tb[tb["_gl_key"] != ""].drop_duplicates("_gl_key", keep="last")
    tb_lookup = tb.set_index("_gl_key")[tb.columns[1]]

    kept["_gl_key"] = clean_key_series(kept["GL Account"])
    kept["_gl_matched"] = kept["_gl_key"].isin(tb_lookup.index) & (kept["_gl_key"] != "")
    kept["Account Name"] = kept["_gl_key"].map(tb_lookup).fillna("")

    stats["matched_gl_rows"] = int(kept["_gl_matched"].sum())
    stats["unmatched_gl_rows"] = int((~kept["_gl_matched"]).sum())

    # --- Sort by the Code column ascending, case-insensitive (stable) ---
    sort_col = headers[CODE_COL_IDX]
    kept["_sort_key"] = kept[sort_col].fillna("").astype(str).str.upper()
    kept = kept.sort_values("_sort_key", kind="mergesort").reset_index(drop=True)

    # --- New Vendors: kept rows with a non-blank vendor not found in the mapping ---
    new_vendor_mask = (~kept["_vendor_matched"]) & (kept["_vendor_key"] != "")
    stats["new_vendor_rows"] = int(new_vendor_mask.sum())

    # --- Dollar control totals: Original = Retained + Removed, to the penny ---
    retained_total = float(kept[amount_col].sum(skipna=True))
    stats["original_amount_total"] = original_total
    stats["retained_amount_total"] = retained_total
    stats["removed_amount_total"] = removed_total
    stats["control_difference"] = round(original_total - (retained_total + removed_total), 2)

    output_cols = headers + ["Taxability", "Grouping", "GL Account", "Account Name"]
    kept["_is_new_vendor"] = new_vendor_mask

    result_df = kept[output_cols].reset_index(drop=True)
    new_vendor_flags = kept["_is_new_vendor"].reset_index(drop=True)

    return result_df, new_vendor_flags, stats, removed_df


def get_controlled_options(df_mapping: pd.DataFrame, col_idx: int) -> list:
    """Distinct non-blank values already used in the mapping file for the
    given column - the source of truth for dropdown options in the New
    Vendor classification editor, since we shouldn't invent a business's
    tax categories ourselves. Returns [] if the file has too few columns
    or no non-blank values yet, signaling the caller to fall back to free
    text (e.g. a brand-new, still-empty mapping file)."""
    if df_mapping.shape[1] <= col_idx:
        return []
    values = df_mapping.iloc[:, col_idx].dropna().astype(str).str.strip()
    values = values[values != ""]
    return sorted(values.unique().tolist())


def validate_new_vendor_classifications(edited_df: pd.DataFrame, df_mapping: pd.DataFrame) -> list:
    """Guardrails before an updated mapping file can be built: every vendor
    needs Taxability + Grouping, no duplicate normalized vendor keys within
    the table, and no collision with a vendor already in the mapping file.
    Returns a list of human-readable messages (empty list = valid)."""
    errors = []

    # fillna("") BEFORE astype(str) matters: without it, a None value
    # becomes the literal string "None" and NaN becomes "nan" once
    # converted, neither of which equals "" - so a genuinely blank
    # classification (e.g. left unselected in a SelectboxColumn) could
    # silently pass this check as if it had been filled in.
    blank_tax = edited_df[edited_df["Taxability"].fillna("").astype(str).str.strip() == ""]
    if len(blank_tax) > 0:
        names = ", ".join(blank_tax["Vendor"].astype(str).tolist()[:10])
        errors.append(f"Missing Taxability for: {names}")

    blank_group = edited_df[edited_df["Grouping"].fillna("").astype(str).str.strip() == ""]
    if len(blank_group) > 0:
        names = ", ".join(blank_group["Vendor"].astype(str).tolist()[:10])
        errors.append(f"Missing Grouping for: {names}")

    keys = clean_key_series(edited_df["Vendor"])
    dupe_mask = keys.duplicated(keep=False) & (keys != "")
    if dupe_mask.any():
        names = ", ".join(sorted(set(edited_df.loc[dupe_mask, "Vendor"].astype(str))))
        errors.append(f"Duplicate vendor(s) within the classification table: {names}")

    existing_keys = set(clean_key_series(df_mapping.iloc[:, 0]))
    collision_mask = keys.isin(existing_keys) & (keys != "")
    if collision_mask.any():
        names = ", ".join(sorted(set(edited_df.loc[collision_mask, "Vendor"].astype(str))))
        errors.append(f"Already present in the mapping file: {names}")

    return errors


def compute_input_fingerprint(source_bytes, mapping_bytes, cached_tb_fingerprint, excluded_ids, pad_side) -> str:
    """Hashes everything that affects the result: the two uploaded files'
    raw bytes, the trial balance cache's content fingerprint (not its
    modification time - a restored backup or a file copy can carry a new
    mtime with identical content, or vice versa), the excluded-ID list,
    and the padding option. Comparing this against the fingerprint stored
    at run time detects whether any input changed since the results on
    screen were produced, so stale results can be hidden rather than
    silently downloaded."""
    hasher = hashlib.sha256()
    hasher.update(source_bytes or b"")
    hasher.update(b"|")
    hasher.update(mapping_bytes or b"")
    hasher.update(b"|")
    hasher.update((cached_tb_fingerprint or "").encode())
    hasher.update(b"|")
    hasher.update(",".join(sorted(excluded_ids)).encode())
    hasher.update(b"|")
    hasher.update(pad_side.encode())
    return hasher.hexdigest()
