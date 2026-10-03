"""
Vendor Reconciliation
======================
Compares two vendor listings (by Vendor ID) and classifies each vendor as
Added / Removed / Renamed / Unchanged, then lets you classify new vendors
and assemble an updated mapping file.

The mapping file this module builds uses a 6-column schema (Vendor Name,
Vendor ID, Notes, Taxability, Grouping, Status) chosen to match cleanup.py's
positional expectations exactly: cleanup.py matches source transactions
against mapping column 0 using the transaction's own Vendor column, which
in practice contains vendor NAME text (e.g. "PLAINS DAIRY - WATER-H20"),
not a separate numeric ID - so Vendor Name, not Vendor ID, has to be the
column cleanup.py actually matches on. Taxability sits at position 3 and
Grouping at position 4 (see cleanup.py's process_transactions, which reads
mapping.columns[0]/[3]/[4] by position, and validate_inputs, which
requires at least 5 mapping columns). Vendor ID and Status are informational
columns cleanup.py ignores; Status marks historical (removed) vendors as
Inactive rather than deleting them outright.

ID normalization is shared with Transaction Cleanup through cleanup.py, which
has no Streamlit or UI imports, so the import direction stays one-way with no
risk of a circular import. Name normalization is deliberately different (see
clean_name_key_series) and stays here.
"""

import hashlib
from io import BytesIO

import pandas as pd
import streamlit as st
from openpyxl import Workbook
from openpyxl.styles import Border, Font, Side

from shared.sample_data import sample_downloads, sample_uploader

# ID normalization is the same rule Transaction Cleanup uses: strip ALL internal
# whitespace, surrounding quotes, and Excel .0 float artifacts, then uppercase.
from .cleanup import clean_key_series as clean_id_key_series

_HEADER_FONT = Font(bold=True)
_HEADER_BORDER = Border(bottom=Side(style="thin"))

# Matches cleanup.py's positional mapping-file expectations - see module
# docstring. Vendor Name (not Vendor ID) is at position 0 because that's
# the field cleanup.py actually matches transactions against. Column ORDER
# matters here, not just presence.
MAPPING_SCHEMA_COLS = ["Vendor Name", "Vendor ID", "Notes", "Taxability", "Grouping", "Status"]
_MAPPING_BASE_COLS = ["Vendor Name", "Vendor ID", "Notes", "Taxability", "Grouping"]


# =====================================================================
# NORMALIZATION - IDs and names are deliberately handled differently.
# =====================================================================
def clean_name_key_series(s: pd.Series) -> pd.Series:
    """Name normalization: trim ends, collapse repeated internal whitespace
    to a single space, case-insensitive. Deliberately does NOT strip all
    internal spaces the way ID normalization does - "ACME CO" and "ACMECO"
    are different names, and treating them as identical for comparison
    purposes could mask a genuine rename as a non-event."""
    cleaned = s.fillna("").astype(str).str.strip()
    cleaned = cleaned.str.replace(r"\s+", " ", regex=True)
    return cleaned.str.upper()


# =====================================================================
# VALIDATION
# =====================================================================
def validate_reconciliation_inputs(df_old: pd.DataFrame, df_new: pd.DataFrame,
                                    old_id_col: str, old_name_col: str,
                                    new_id_col: str, new_name_col: str,
                                    old_tax_col: str, old_group_col: str) -> list:
    """Schema/selection validation before running the comparison. Returns a
    list of human-readable messages (empty list = valid)."""
    errors = []

    if df_old.empty:
        errors.append("Previous Vendor Listing: the file is completely blank.")
    if df_new.empty:
        errors.append("Updated Vendor Listing: the file is completely blank.")

    if old_id_col == old_name_col:
        errors.append("Previous Vendor Listing: Vendor ID and Vendor Name columns must be different.")
    if new_id_col == new_name_col:
        errors.append("Updated Vendor Listing: Vendor ID and Vendor Name columns must be different.")
    if old_tax_col and old_group_col and old_tax_col == old_group_col:
        errors.append("Previous Vendor Listing: Taxability and Grouping columns must be different.")

    return errors


def detect_duplicate_id_conflicts(df: pd.DataFrame, id_col: str, name_col: str,
                                   tax_col: str = None, group_col: str = None,
                                   label: str = "Vendor Listing") -> list:
    """Flags Vendor IDs that appear more than once in a listing with
    DIFFERENT names, and/or two or more DIFFERENT NON-BLANK
    classifications, across the duplicate rows. Identical duplicates are
    safe to consolidate and are not flagged. A blank Taxability/Grouping
    value on one duplicate row is NOT treated as conflicting with a real
    value on another - that's an incomplete entry, not a disagreement (name
    conflicts are still checked strictly, since a blank vendor name is more
    likely a genuine data problem worth surfacing than an unclassified row
    is)."""
    d = df.copy()
    d["_id"] = clean_id_key_series(d[id_col])
    d = d[d["_id"] != ""]
    d["_name_norm"] = clean_name_key_series(d[name_col])
    if tax_col:
        d["_tax_norm"] = clean_id_key_series(d[tax_col])
    if group_col:
        d["_grp_norm"] = clean_id_key_series(d[group_col])

    errors = []
    for key, group in d.groupby("_id"):
        if len(group) <= 1:
            continue

        name_conflict = group["_name_norm"].nunique() > 1

        classification_conflict = False
        if tax_col or group_col:
            class_cols = [c for c in ("_tax_norm", "_grp_norm") if c in group.columns]
            blank_mask = pd.Series(True, index=group.index)
            for c in class_cols:
                blank_mask &= group[c] == ""
            substantive = group[~blank_mask]
            classification_conflict = substantive[class_cols].drop_duplicates().shape[0] > 1

        if name_conflict or classification_conflict:
            display_name = group.iloc[0][name_col]
            errors.append(
                f"{label}: conflicting duplicate entries for Vendor ID '{key}' "
                f"(e.g. '{display_name}') - values differ across rows sharing this ID."
            )
    return errors


def get_controlled_options(df: pd.DataFrame, col: str) -> list:
    """Distinct non-blank values already used for the given column - the
    source of truth for dropdown options, so a business's existing
    Taxability/Grouping vocabulary isn't reinvented here."""
    if col is None or col not in df.columns:
        return []
    values = df[col].dropna().astype(str).str.strip()
    values = values[values != ""]
    return sorted(values.unique().tolist())


def validate_mapping_completeness(combined_active: pd.DataFrame) -> list:
    """Guardrail before building the mapping file: every ACTIVE vendor
    needs Taxability + Grouping. Required here even though these fields
    are optional earlier during pure comparison, since a mapping file
    with gaps can't actually support transaction classification.
    Inactive (archived) vendors are exempt - they're kept for historical
    reference, not used to classify new transactions going forward."""
    errors = []

    blank_tax = combined_active[combined_active["Taxability"].fillna("").astype(str).str.strip() == ""]
    if len(blank_tax) > 0:
        names = ", ".join(blank_tax["Vendor ID"].astype(str).tolist()[:10])
        errors.append(f"Missing Taxability for Vendor ID(s): {names}")

    blank_group = combined_active[combined_active["Grouping"].fillna("").astype(str).str.strip() == ""]
    if len(blank_group) > 0:
        names = ", ".join(blank_group["Vendor ID"].astype(str).tolist()[:10])
        errors.append(f"Missing Grouping for Vendor ID(s): {names}")

    return errors


def compute_reconciliation_fingerprint(old_bytes, new_bytes, old_id_col, old_name_col,
                                        old_tax_col, old_group_col, new_id_col, new_name_col) -> str:
    """Hashes both files' raw bytes plus every column selection. A mismatch
    against the fingerprint stored when the comparison ran means at least
    one input changed since, so the old results should be hidden rather
    than trusted."""
    hasher = hashlib.sha256()
    hasher.update(old_bytes or b"")
    hasher.update(b"|")
    hasher.update(new_bytes or b"")
    hasher.update(b"|")
    selections = "|".join(str(x) for x in [
        old_id_col, old_name_col, old_tax_col, old_group_col, new_id_col, new_name_col
    ])
    hasher.update(selections.encode())
    return hasher.hexdigest()


# =====================================================================
# CORE LOGIC - pure pandas/openpyxl, no Streamlit calls in this section.
# =====================================================================
def diff_vendor_lists(df_old: pd.DataFrame, df_new: pd.DataFrame,
                       old_id_col: str, old_name_col: str,
                       new_id_col: str, new_name_col: str,
                       old_taxability_col: str = None, old_grouping_col: str = None):
    """Outer-joins the old and new vendor listings on Vendor ID and
    classifies each vendor as added / removed / renamed / unchanged.
    Name-change detection uses clean_name_key_series (NOT the aggressive
    ID normalizer), so a genuine rename like "Acme Co" -> "Acme LLC" isn't
    masked, while whitespace/case-only differences still don't count as a
    rename. Returns (added_df, removed_df, renamed_df, unchanged_df)."""
    old = df_old.copy()
    old["_id"] = clean_id_key_series(old[old_id_col])
    if old_taxability_col or old_grouping_col:
        # A Vendor ID can legitimately appear twice with one row blank (not
        # yet classified) and one row with real Taxability/Grouping data -
        # detect_duplicate_id_conflicts doesn't treat that as a conflict, so
        # this must independently guarantee the classified row always wins,
        # not just "whichever comes last in the file".
        tax_norm = clean_id_key_series(old[old_taxability_col]) if old_taxability_col else ""
        grp_norm = clean_id_key_series(old[old_grouping_col]) if old_grouping_col else ""
        old["_is_blank"] = (tax_norm == "") & (grp_norm == "")
        old = old.sort_values("_is_blank", ascending=False)
    old = old[old["_id"] != ""].drop_duplicates("_id", keep="last")

    new = df_new.copy()
    new["_id"] = clean_id_key_series(new[new_id_col])
    new = new[new["_id"] != ""].drop_duplicates("_id", keep="last")

    old_cols = {"_id": "_id", old_name_col: "Old Name"}
    if old_taxability_col:
        old_cols[old_taxability_col] = "Taxability"
    if old_grouping_col:
        old_cols[old_grouping_col] = "Grouping"
    old_slim = old[list(old_cols.keys())].rename(columns=old_cols)

    new_slim = new[["_id", new_id_col, new_name_col]].rename(
        columns={new_id_col: "Vendor ID", new_name_col: "New Name"}
    )

    merged = old_slim.merge(new_slim, on="_id", how="outer", indicator=True)

    added = merged[merged["_merge"] == "right_only"].copy()
    added_out = added[["Vendor ID", "New Name"]].rename(columns={"New Name": "Vendor Name"})
    added_out["Taxability"] = ""
    added_out["Grouping"] = ""

    removed = merged[merged["_merge"] == "left_only"].copy()
    removed_out = removed[["_id", "Old Name"]].rename(
        columns={"_id": "Vendor ID", "Old Name": "Vendor Name"}
    )
    if "Taxability" in removed.columns:
        removed_out["Taxability"] = removed["Taxability"]
    if "Grouping" in removed.columns:
        removed_out["Grouping"] = removed["Grouping"]

    both = merged[merged["_merge"] == "both"].copy()
    name_changed = clean_name_key_series(both["Old Name"]) != clean_name_key_series(both["New Name"])

    renamed = both[name_changed].copy()
    renamed_out = renamed[["Vendor ID", "Old Name", "New Name"]].copy()
    if "Taxability" in renamed.columns:
        renamed_out["Taxability"] = renamed["Taxability"]
    if "Grouping" in renamed.columns:
        renamed_out["Grouping"] = renamed["Grouping"]

    unchanged = both[~name_changed].copy()
    unchanged_out = unchanged[["Vendor ID", "New Name"]].rename(columns={"New Name": "Vendor Name"})
    if "Taxability" in unchanged.columns:
        unchanged_out["Taxability"] = unchanged["Taxability"]
    if "Grouping" in unchanged.columns:
        unchanged_out["Grouping"] = unchanged["Grouping"]

    return (
        added_out.reset_index(drop=True),
        removed_out.reset_index(drop=True),
        renamed_out.reset_index(drop=True),
        unchanged_out.reset_index(drop=True),
    )


def build_mapping_workbook(unchanged_df, renamed_df, added_df,
                            removed_df=None, include_inactive=False) -> BytesIO:
    """Assembles a fresh vendor mapping file matching cleanup.py's expected
    positional schema (Vendor Name @ 0, Taxability @ 3, Grouping @ 4), plus
    Vendor ID and Status as informational columns cleanup.py ignores. Every
    input frame is reindex()'d onto the target columns rather than indexed
    directly, so a frame missing Taxability/Grouping entirely (e.g. because
    the user left those column selectors on "(none)") fills in blank
    instead of raising a KeyError.

    If include_inactive is True and removed_df is provided, removed
    vendors are appended with Status="Inactive" instead of being dropped
    outright - preserving their historical Taxability/Grouping for
    vendors that may still appear in historical transactions, late
    invoices, or prior-period reruns, while flagging that they're no
    longer active."""

    def _prep(df: pd.DataFrame, status: str) -> pd.DataFrame:
        d = df.copy()
        if "New Name" in d.columns:
            d = d.rename(columns={"New Name": "Vendor Name"})
        d = d.reindex(columns=_MAPPING_BASE_COLS)
        d["Notes"] = d["Notes"].fillna("")
        d["Taxability"] = d["Taxability"].fillna("")
        d["Grouping"] = d["Grouping"].fillna("")
        d["Status"] = status
        return d

    parts = [_prep(unchanged_df, "Active"), _prep(renamed_df, "Active"), _prep(added_df, "Active")]
    if include_inactive and removed_df is not None and len(removed_df) > 0:
        parts.append(_prep(removed_df, "Inactive"))

    combined = pd.concat(parts, ignore_index=True)[MAPPING_SCHEMA_COLS]

    wb = Workbook()
    ws = wb.active
    ws.title = "Vendor Mapping"
    ws.append(MAPPING_SCHEMA_COLS)
    for cell in ws[1]:
        cell.font = _HEADER_FONT
        cell.border = _HEADER_BORDER
    for row in combined.itertuples(index=False):
        ws.append(list(row))
    last_col_letter = "F"  # 6 columns: A-F
    ws.auto_filter.ref = f"A1:{last_col_letter}{len(combined) + 1}"
    ws.freeze_panes = "A2"
    for c, width in zip("ABCDEF", (30, 16, 20, 16, 16, 12)):
        ws.column_dimensions[c].width = width

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


# =====================================================================
# STREAMLIT UI
# =====================================================================
def render_vendor_reconciliation():
    st.header("Vendor Reconciliation")
    st.caption(
        "Compare a previous vendor listing against an updated one, by Vendor ID, "
        "to find new, removed, and renamed vendors."
    )

    col1, col2 = st.columns(2)
    with col1:
        st.markdown("**Previous Vendor Listing**")
        old_file, _ = sample_uploader(
            "Previous Vendor Listing", key="old_vendor_file",
            sample_file="vendor_listing_previous.xlsx", types=["xlsx"],
        )
    with col2:
        st.markdown("**Updated Vendor Listing**")
        new_file, _ = sample_uploader(
            "Updated Vendor Listing", key="new_vendor_file",
            sample_file="vendor_listing_updated.xlsx", types=["xlsx"],
        )
    sample_downloads([
        ("Previous Vendor Listing (.xlsx)", "vendor_listing_previous.xlsx"),
        ("Updated Vendor Listing (.xlsx)", "vendor_listing_updated.xlsx"),
    ], key="vendor_rec")

    if old_file and new_file:
        try:
            df_old = pd.read_excel(old_file, header=0)
            df_new = pd.read_excel(new_file, header=0)
        except Exception as e:
            st.error(f"Could not read one of the uploaded files: {e}")
            return

        st.subheader("Map columns")
        oc = st.columns(4)
        old_id_col = oc[0].selectbox("Previous file: Vendor ID", df_old.columns, key="old_id")
        old_name_col = oc[1].selectbox("Previous file: Vendor Name", df_old.columns, key="old_name")
        old_tax_col = oc[2].selectbox(
            "Previous file: Taxability (optional for comparison)",
            ["(none)"] + list(df_old.columns), key="old_tax",
            help="Optional to compare listings, but required to build an updated mapping file.",
        )
        old_group_col = oc[3].selectbox(
            "Previous file: Grouping (optional for comparison)",
            ["(none)"] + list(df_old.columns), key="old_group",
            help="Optional to compare listings, but required to build an updated mapping file.",
        )

        nc = st.columns(2)
        new_id_col = nc[0].selectbox("Updated file: Vendor ID", df_new.columns, key="new_id")
        new_name_col = nc[1].selectbox("Updated file: Vendor Name", df_new.columns, key="new_name")

        old_tax_col_resolved = None if old_tax_col == "(none)" else old_tax_col
        old_group_col_resolved = None if old_group_col == "(none)" else old_group_col

        if st.button("Compare Listings", type="primary"):
            validation_errors = validate_reconciliation_inputs(
                df_old, df_new, old_id_col, old_name_col, new_id_col, new_name_col,
                old_tax_col_resolved, old_group_col_resolved,
            )
            validation_errors += detect_duplicate_id_conflicts(
                df_old, old_id_col, old_name_col, old_tax_col_resolved, old_group_col_resolved,
                label="Previous Vendor Listing",
            )
            validation_errors += detect_duplicate_id_conflicts(
                df_new, new_id_col, new_name_col, label="Updated Vendor Listing",
            )

            if validation_errors:
                st.error(
                    "Please fix the following before comparing:\n\n"
                    + "\n".join(f"- {e}" for e in validation_errors)
                )
            else:
                added, removed, renamed, unchanged = diff_vendor_lists(
                    df_old, df_new, old_id_col, old_name_col, new_id_col, new_name_col,
                    old_tax_col_resolved, old_group_col_resolved,
                )
                st.session_state["vr_added"] = added
                st.session_state["vr_removed"] = removed
                st.session_state["vr_renamed"] = renamed
                st.session_state["vr_unchanged"] = unchanged
                st.session_state["vr_fingerprint"] = compute_reconciliation_fingerprint(
                    old_file.getvalue(), new_file.getvalue(),
                    old_id_col, old_name_col, old_tax_col_resolved, old_group_col_resolved,
                    new_id_col, new_name_col,
                )
                # Clear any mapping-file download left over from a previous
                # comparison - it belonged to a different result set.
                st.session_state.pop("vr_mapping_buf", None)
                st.session_state.pop("vr_mapping_count", None)

    if "vr_added" in st.session_state:
        # Stale-result protection: if either file or any column selection
        # has changed since this comparison ran, hide the old results
        # rather than leave them on screen looking current.
        if old_file and new_file:
            current_fingerprint = compute_reconciliation_fingerprint(
                old_file.getvalue(), new_file.getvalue(),
                old_id_col, old_name_col, old_tax_col_resolved, old_group_col_resolved,
                new_id_col, new_name_col,
            )
        else:
            current_fingerprint = None
        if current_fingerprint != st.session_state.get("vr_fingerprint"):
            st.warning("Inputs have changed. Run Compare Listings again before downloading.")
            return

        added = st.session_state["vr_added"]
        removed = st.session_state["vr_removed"]
        renamed = st.session_state["vr_renamed"]
        unchanged = st.session_state["vr_unchanged"]

        m = st.columns(4)
        m[0].metric("Added", len(added))
        m[1].metric("Removed", len(removed))
        m[2].metric("Renamed", len(renamed))
        m[3].metric("Unchanged", len(unchanged))

        tax_options = get_controlled_options(df_old, old_tax_col_resolved) if old_file and new_file else []
        group_options = get_controlled_options(df_old, old_group_col_resolved) if old_file and new_file else []
        tax_col_config = (
            st.column_config.SelectboxColumn("Taxability", options=tax_options, required=True)
            if tax_options else st.column_config.TextColumn("Taxability")
        )
        group_col_config = (
            st.column_config.SelectboxColumn("Grouping", options=group_options, required=True)
            if group_options else st.column_config.TextColumn("Grouping")
        )

        st.subheader(f"Added Vendors ({len(added)})")
        st.caption("Assign Taxability / Grouping for each new vendor before building the updated mapping file.")
        edited_added = st.data_editor(
            added, num_rows="fixed", width="stretch", key="edit_added",
            column_config={"Taxability": tax_col_config, "Grouping": group_col_config},
        )

        st.subheader(f"Removed Vendors ({len(removed)})")
        st.caption("These vendor IDs no longer appear in the updated listing.")
        st.dataframe(removed, width="stretch")
        include_inactive = st.checkbox(
            "Include removed vendors in the updated mapping file, marked Inactive",
            value=False,
            help=(
                "A vendor can be inactive yet still appear in historical transactions, "
                "late invoices, credit memos, or prior-period reruns. Checking this "
                "preserves their existing Taxability/Grouping instead of deleting them."
            ),
        )

        st.subheader(f"Possible Renames ({len(renamed)})")
        st.caption("Same Vendor ID, different name. Taxability / Grouping carries forward automatically.")
        st.dataframe(renamed, width="stretch")

        if st.button("Build Updated Mapping File", type="primary"):
            active_combined = pd.concat([
                unchanged.reindex(columns=["Vendor ID", "Taxability", "Grouping"]),
                renamed.reindex(columns=["Vendor ID", "Taxability", "Grouping"]),
                edited_added.reindex(columns=["Vendor ID", "Taxability", "Grouping"]),
            ], ignore_index=True)
            guardrail_errors = validate_mapping_completeness(active_combined)
            if guardrail_errors:
                st.error(
                    "Please fix the following before building the mapping file:\n\n"
                    + "\n".join(f"- {e}" for e in guardrail_errors)
                )
            else:
                buf = build_mapping_workbook(unchanged, renamed, edited_added, removed, include_inactive)
                # Stored as raw bytes so the download stays available across
                # any later rerun, not just the run where Build was clicked.
                st.session_state["vr_mapping_buf"] = buf.getvalue()
                st.session_state["vr_mapping_count"] = len(edited_added) + (len(removed) if include_inactive else 0)

        if "vr_mapping_buf" in st.session_state:
            st.caption(f"{st.session_state['vr_mapping_count']} vendor(s) added to the mapping file.")
            st.download_button(
                "Download Updated Mapping File",
                data=st.session_state["vr_mapping_buf"],
                file_name="Vendor_Mapping_Updated.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key="dl_updated_mapping",
            )