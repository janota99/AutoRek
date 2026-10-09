"""Custom mapping mode of the Data Reconciliation Studio: reconcile any two datasets with a mapping the user builds.

Shown on the Recon page when the user switches Mode to "Custom mapping" (`render()`). A four-step wizard:

    1. Template        pick a starting mapping from the template library (or start blank)
    2. Upload data     two dataset cards, each with a preview and status badges
    3. Configure rules a mapping grid (A field <-> rule <-> B field), IDs, and the matching volume
    4. Review & run    pre-flight checks, then run; results appear below

A live summary of the rules sits beside steps 2-3 and the Back/Next/Run bar stays at the bottom. The matching rules
live in `engine.py`; this module is page flow and presentation only. Streamlit forgets the widgets of a step that is
not on screen, so every mapping widget key is registered and re-stored on each run (`_keep_alive`), and loaded
datasets and the draft mapping live in plain session-state keys.
"""

from __future__ import annotations

import hashlib
import html
from dataclasses import asdict
from typing import Optional

import pandas as pd
import streamlit as st

from ..assets.ui_assets import INFOR_LOGO_URI, QUICKBOOKS_LOGO_URI
from ..ingestion import list_source_sheets, read_raw_source, read_source_file
from ..matching.core import parse_amount_cents
from ..ui_components import render_section_heading
from shared.sample_data import sample_uploader
from shared.status import status_badge, status_styler
from db.connection import DatabaseConfigError, load_config, tenant_connection
from .engine import ReconResult, normalize_value, reconcile
from . import store
from .export import build_workbook
from .spec import (
    ID_POSITIONS, MAX_GROUP_SIZE, NORMALIZERS, ColumnPair, ReconSpec, find_column, validate_spec,
)

PRESETS_KEY = "cr_presets"
RESULT_KEY = "cr_result"
STEP_KEY = "cr_step"
SPEC0_KEY = "cr_spec0"  # the template the user picked (a ReconSpec as a dict)
DRAFT_KEY = "cr_draft"  # the mapping built in step 3 (a ReconSpec as a dict)
DATA_KEY = "cr_data"  # {"a": (filename, frame, is_sample), "b": ...}
KEYS_KEY = "cr_keys"  # registry of mapping-widget keys to keep alive across steps
NONE_LABEL = "None (use row numbers)"
STEPS = ("Template", "Upload data", "Configure rules", "Review & run")
PATTERNS = ("1 : 1", "Many : 1", "1 : Many", "Many : Many")
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

# The placeholder starting mapping for the QuickBooks and Infinium sample layouts.
PLACEHOLDER = ReconSpec(
    name="QuickBooks and Infinium (placeholder)",
    label_a="QuickBooks", label_b="Infinium",
    amount=ColumnPair("AMOUNT", "OHTOTA"),
    align=[ColumnPair("P.O. NUMBER", "OHDESC", "digits")],
    id_a="Num", id_b="OHOBNO",
)
BLANK = ReconSpec(name="My mapping")
# The template library. Add a real mapping here (with its column names) to offer it as a card.
BUILT_IN = (
    (PLACEHOLDER, "Accounting / ERP", "Example"),
    (BLANK, "Custom", "Blank"),
)


# ---------------------------------------------------------------------------
# Small shared pieces
# ---------------------------------------------------------------------------

def _k(ns: str, name: str) -> str:
    """A mapping-widget key, registered so its value survives a visit to another step."""
    key = f"{ns}_{name}"
    st.session_state.setdefault(KEYS_KEY, set()).add(key)
    return key


def _keep_alive() -> None:
    for key in st.session_state.get(KEYS_KEY, ()):
        if key in st.session_state:
            st.session_state[key] = st.session_state[key]


def _chip(label: str) -> str:
    """A logo chip for a system: the real logo where this app has one, else a colored initial."""
    text = label.strip().lower()
    for prefix, uri in (("quickbooks", QUICKBOOKS_LOGO_URI), ("infinium", INFOR_LOGO_URI)):
        if text.startswith(prefix):
            return f'<span class="cr-chip"><img src="{uri}" alt="{html.escape(label)} logo"></span>'
    hue = int(hashlib.sha1(text.encode()).hexdigest()[:4], 16) % 360
    initial = html.escape((label.strip() or "?")[0].upper())
    return f'<span class="cr-chip cr-chip--initial" style="--cr-hue:{hue}">{initial}</span>'


def _badge(text: str, tone: str = "neutral") -> str:
    return f'<span class="cr-badge cr-badge--{tone}">{html.escape(text)}</span>'


def _pattern_of(spec: ReconSpec) -> str:
    a, b = spec.max_a_per_b, spec.max_b_per_a
    return PATTERNS[0] if a == 1 and b == 1 else PATTERNS[1] if b == 1 else PATTERNS[2] if a == 1 else PATTERNS[3]


def _spec_from(data: Optional[dict]) -> Optional[ReconSpec]:
    return ReconSpec.from_dict(data) if data else None


def _saved_columns(spec: ReconSpec) -> tuple[list[str], list[str]]:
    a = [c for c in ([spec.amount.a] if spec.amount else []) + [p.a for p in spec.align] + [spec.id_a] if c]
    b = [c for c in ([spec.amount.b] if spec.amount else []) + [p.b for p in spec.align] + [spec.id_b] if c]
    return a, b


def _go(step: int) -> None:
    st.session_state[STEP_KEY] = step


# ---------------------------------------------------------------------------
# Stepper
# ---------------------------------------------------------------------------

def _stepper(current: int, done: list[bool]) -> None:
    items = ""
    for number, title in enumerate(STEPS):
        state = "complete" if number < current and done[number] else "active" if number == current else "upcoming"
        status = "Done" if state == "complete" else "In progress" if state == "active" else "Next"
        items += (
            f'<div class="stepper-item {state}"><span class="stepper-node">{"✓" if state == "complete" else number + 1}</span>'
            f'<span class="stepper-copy"><span class="stepper-title">{html.escape(title)}</span>'
            f'<span class="stepper-status">{status}</span></span></div>'
        )
    st.markdown(f'<nav class="rec-stepper" aria-label="Custom reconciliation steps">{items}</nav>', unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Step 1: template library
# ---------------------------------------------------------------------------

def _use_template(spec: dict) -> None:
    st.session_state[SPEC0_KEY] = spec
    st.session_state["cr_ver"] = st.session_state.get("cr_ver", 0) + 1
    st.session_state.pop(DRAFT_KEY, None)
    st.session_state[STEP_KEY] = 1


def _db_config():
    """The shared-library database from secrets or the environment, or None (session-only mode)."""
    try:
        return load_config(secrets=st.secrets)
    except DatabaseConfigError as exc:
        st.session_state["cr_db_problem"] = str(exc)
        return None


def _library() -> tuple[dict, bool]:
    """(saved mappings by name, whether they come from the shared database). Falls back to this session on any problem."""
    session = dict(st.session_state.setdefault(PRESETS_KEY, {}))
    config = _db_config()
    if config is None:
        return session, False
    try:
        with tenant_connection(config) as conn:
            return {m.name: m.spec for m in store.list_latest(conn)}, True
    except Exception as exc:  # noqa: BLE001 - never block the page because the library is unreachable
        st.session_state["cr_db_problem"] = f"The shared mapping library could not be reached ({type(exc).__name__})."
        return session, False


def _store_mapping(spec: ReconSpec, note: str = "") -> str:
    """Save to the shared database when configured, else to this session. Returns the message to show."""
    config = _db_config()
    if config is not None:
        try:
            with tenant_connection(config) as conn:
                out = store.save(conn, config.org_id, spec, created_by="app", note=note or None)
            if out.created:
                return f"Saved '{spec.name}' to your shared library as version {out.version}."
            return f"'{spec.name}' is already saved with these exact settings (version {out.version}); nothing new was written."
        except Exception as exc:  # noqa: BLE001 - keep the work: fall back to the session and say so
            st.session_state["cr_db_problem"] = f"The shared library could not be reached ({type(exc).__name__})."
    st.session_state.setdefault(PRESETS_KEY, {})[spec.name] = asdict(spec)
    return f"Saved '{spec.name}' for this session only. Find it under Saved mappings on the Template step."


def _import_preset() -> None:
    upload = st.session_state.get("cr_import")
    if upload is None:
        return
    try:
        spec = ReconSpec.from_json(upload.getvalue().decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        st.session_state["cr_notice"] = f"That file is not a saved mapping ({exc})."
        return
    st.session_state["cr_notice"] = _store_mapping(spec, note="loaded from a mapping file")


def _template_description(spec: ReconSpec) -> str:
    if not spec.amount:
        return "Choose every column yourself."
    parts = [f"Amount equal to the cent, {len(spec.align)} alignment column{'s' if len(spec.align) != 1 else ''}"]
    parts.append(f"matching volume {_pattern_of(spec).lower()}")
    return "; ".join(parts) + "."


def _step_template() -> None:
    render_section_heading("Choose a template", "Start from a ready mapping, one you saved this session, or a blank one.")
    presets, shared = _library()
    notice = st.session_state.pop("cr_notice", None)
    if notice:
        st.success(notice)
    problem = st.session_state.pop("cr_db_problem", None)
    if problem:
        st.warning(f"{problem} Showing this session's mappings instead.")
    st.caption("Saved mappings are shared with your organization." if shared
               else "Saved mappings last until this browser session ends. Connect a database to share them.")

    c1, c2 = st.columns([2, 3], vertical_alignment="bottom")
    query = c1.text_input("Search templates", key="cr_search", placeholder="Search by name or system").strip().casefold()
    categories = ["All", "Accounting / ERP", "Custom", "Saved mappings"]
    category = c2.segmented_control("Category", categories, default="All", key="cr_category") or "All"

    cards = [(spec, cat, tag) for spec, cat, tag in BUILT_IN]
    cards += [(ReconSpec.from_dict(raw), "Saved mappings", "Shared" if shared else "This session") for raw in presets.values()]
    shown = [(s, c, t) for s, c, t in cards
             if category in ("All", c) and (not query or query in f"{s.name} {s.label_a} {s.label_b} {c}".casefold())]
    if not shown:
        st.info("No template matches that search.")
    for row_start in range(0, len(shown), 3):  # noqa: B007
        columns = st.columns(3, gap="medium")
        for offset, (column, (spec, cat, tag)) in enumerate(zip(columns, shown[row_start: row_start + 3])):
            index = row_start + offset
            with column, st.container(border=True, key=f"cr-tpl-{index}"):
                blank = spec.amount is None
                chips = (f'{_chip(spec.label_a)}<span class="cr-arrow">⟷</span>{_chip(spec.label_b)}'
                         if not blank else '<span class="cr-chip cr-chip--initial" style="--cr-hue:215">+</span>')
                st.markdown(f'<div class="cr-tpl-head">{chips}{_badge(tag, "info" if tag == "Example" else "neutral")}</div>',
                            unsafe_allow_html=True)
                st.markdown(f"**{html.escape(spec.name)}**")
                st.caption(_template_description(spec))
                st.button("Use template" if not blank else "Start blank", key=f"cr_use_{index}", type="primary",
                          width="stretch", on_click=_use_template, args=(asdict(spec),))
    st.markdown('<p class="cr-note">QuickBooks and Infinium is the only built-in example. Mappings you save while '
                "using the tool appear here for the rest of the session.</p>", unsafe_allow_html=True)
    with st.expander("Load a saved mapping file from your computer"):
        st.file_uploader("Saved mapping (.json)", type=["json"], key="cr_import", on_change=_import_preset)


# ---------------------------------------------------------------------------
# Step 2: upload and preview
# ---------------------------------------------------------------------------

def _guess_header_row(raw: pd.DataFrame) -> int:
    """The first of the top 25 rows with the most text (non-numeric) cells."""
    def is_label(value: object) -> bool:
        if value is None or pd.isna(value):
            return False
        try:
            float(str(value).replace(",", ""))
            return False
        except ValueError:
            return True
    scores = [sum(is_label(v) for v in raw.iloc[i]) for i in range(min(25, len(raw)))]
    return max(range(len(scores)), key=lambda i: (scores[i], -i)) if scores else 0


def _read_dataset(file, prefix: str, title: str) -> Optional[pd.DataFrame]:
    """Worksheet and header-row pickers for one upload; returns the cleaned frame or None."""
    data, name = file.getvalue(), file.name
    token = getattr(file, "file_id", name)
    try:
        sheets = list_source_sheets(data, name)
        sheet = None
        if len(sheets) > 1:
            sheet = st.selectbox("Worksheet", sheets, key=_k(prefix, f"sheet_{token}"))
        elif sheets:
            sheet = sheets[0]
        raw = read_raw_source(data, name, sheet)
        if raw.empty:
            st.error(f"{title}: the file has no data.")
            return None
        header = st.number_input(
            "Header row", 1, len(raw), _guess_header_row(raw) + 1, key=_k(prefix, f"hdr_{token}_{sheet}"),
            help="The row holding the column names (1 is the first row of the sheet).",
        ) - 1
        frame = read_source_file(data, name, int(header), sheet)
    except Exception as exc:  # noqa: BLE001 - shown to the user, who can change the sheet or header row
        st.error(f"{title}: this file could not be read ({exc}).")
        return None
    if frame.empty:
        st.error(f"{title}: no data rows were found below that header row.")
        return None
    return frame


def _looks_like(series: pd.Series) -> str:
    values = [v for v in series.head(200) if not pd.isna(v) and str(v).strip()]
    if not values:
        return "Empty"
    share = lambda test: sum(test(v) for v in values) / len(values)  # noqa: E731
    if share(lambda v: parse_amount_cents(v) is not None) >= 0.9:
        return "Number / amount"
    if share(lambda v: normalize_value(v, "date") is not None and any(ch in str(v) for ch in "/-")) >= 0.9:
        return "Date"
    return "Text"


def _preview(frame: pd.DataFrame) -> None:
    with st.expander(f"Preview: first {min(len(frame), 10)} of {len(frame):,} rows", expanded=False):
        st.dataframe(frame.head(10), hide_index=True, width="stretch")
        profile = pd.DataFrame({
            "Column": list(frame.columns)[:60],
            "Looks like": [_looks_like(frame[c]) for c in list(frame.columns)[:60]],
            "Filled": [f"{int(frame[c].notna().sum()):,} of {len(frame):,}" for c in list(frame.columns)[:60]],
            "Example": [next((str(v) for v in frame[c] if not pd.isna(v)), "") for c in list(frame.columns)[:60]],
        })
        st.caption("What each column looks like")
        st.dataframe(profile, hide_index=True, width="stretch")


def _dataset_card(side: str, spec0: ReconSpec) -> None:
    label = spec0.label_a if side == "a" else spec0.label_b
    role = "Source system" if side == "a" else "Target system"
    sample_name = "recon_quickbooks_sales.xlsx" if side == "a" else "recon_infinium_sales.xlsx"
    with st.container(border=True, key=f"cr-card-{side}"):
        st.markdown(f'<div class="cr-card-head">{_chip(label)}<span><span class="cr-role">{role}</span>'
                    f'<span class="cr-card-title">{html.escape(label)}</span></span></div>', unsafe_allow_html=True)
        file, is_sample = sample_uploader(f"Upload {label}", key=f"cr_file_{side}", types=["xlsx", "csv"],
                                          sample_file=sample_name, show_badge=False)
        data: dict = st.session_state.setdefault(DATA_KEY, {})
        if file is not None:
            frame = _read_dataset(file, f"cr_{side}", label)
            if frame is None:
                data.pop(side, None)
            else:
                data[side] = (file.name, frame, is_sample)
        elif side in data and data[side][2]:
            data.pop(side)  # a cleared sample
        if side not in data:
            st.markdown(_badge("Waiting for a file", "neutral"), unsafe_allow_html=True)
            return
        name, frame, sample = data[side]
        if file is None:
            st.caption(f"Using the file you uploaded earlier: {name}")
        saved = _saved_columns(spec0)[0 if side == "a" else 1]
        missing = [c for c in saved if find_column(frame.columns, c) is None]
        unnamed = sum(str(c).startswith("Unnamed") for c in frame.columns)
        badges = [_badge("Validated", "ok")]
        if sample:
            badges.append(_badge("Demo data", "info"))
        if unnamed:
            badges.append(_badge(f"{unnamed} unnamed column{'s' if unnamed != 1 else ''}: check the header row", "warn"))
        if missing and spec0.amount:
            badges.append(_badge(f"Header mismatch: {len(missing)} template column{'s' if len(missing) != 1 else ''} not found", "bad"))
        st.markdown(" ".join(badges) + f'<div class="cr-meta">{html.escape(name)} &middot; {len(frame):,} rows &middot; '
                    f"{len(frame.columns)} columns</div>", unsafe_allow_html=True)
        _preview(frame)


def _step_upload(spec0: ReconSpec) -> None:
    render_section_heading("Upload the two datasets", "CSV or Excel. Check the preview, and change the worksheet or header row if needed.")
    left, right = st.columns(2, gap="medium")
    with left:
        _dataset_card("a", spec0)
    with right:
        _dataset_card("b", spec0)


# ---------------------------------------------------------------------------
# Step 3: rules
# ---------------------------------------------------------------------------

def _pick(label: str, columns: list[str], wanted: Optional[str], key: str, *, optional: bool = False) -> Optional[str]:
    """A column selectbox that opens on the saved column when the file has it."""
    options = ([NONE_LABEL] if optional else []) + columns
    found = find_column(columns, wanted)
    index = options.index(found) if found else (0 if optional else None)
    chosen = st.selectbox(label, options, index=index, key=key, placeholder="Choose a column", label_visibility="collapsed")
    return None if chosen in (None, NONE_LABEL) else chosen


def _grid_header(label_a: str, label_b: str) -> None:
    c = st.columns([4, 3, 4], gap="small")
    c[0].markdown(f'<div class="cr-grid-head">{_chip(label_a)} {html.escape(label_a)} field</div>', unsafe_allow_html=True)
    c[1].markdown('<div class="cr-grid-head cr-grid-head--mid">⟵ Match rule ⟶</div>', unsafe_allow_html=True)
    c[2].markdown(f'<div class="cr-grid-head">{_chip(label_b)} {html.escape(label_b)} field</div>', unsafe_allow_html=True)


def _rule_chip(text: str) -> None:
    st.markdown(f'<div class="cr-rule">{html.escape(text)}</div>', unsafe_allow_html=True)


def _rules_form(spec0: ReconSpec, cols_a: list[str], cols_b: list[str], ns: str) -> ReconSpec:
    """Draw the mapping grid and options, seeded from `spec0`, and return the mapping they describe."""
    c1, c2 = st.columns(2)
    label_a = c1.text_input("Name of the first dataset", spec0.label_a, key=_k(ns, "la")).strip() or "Dataset A"
    label_b = c2.text_input("Name of the second dataset", spec0.label_b, key=_k(ns, "lb")).strip() or "Dataset B"

    st.markdown("##### Field mapping")
    _grid_header(label_a, label_b)
    row = st.columns([4, 3, 4], gap="small", vertical_alignment="center")
    with row[0]:
        amount_a = _pick(f"{label_a} amount", cols_a, spec0.amount.a if spec0.amount else None, _k(ns, "amt_a"))
    with row[1]:
        _rule_chip("Reconcile on: equal to the cent")
    with row[2]:
        amount_b = _pick(f"{label_b} amount", cols_b, spec0.amount.b if spec0.amount else None, _k(ns, "amt_b"))

    count_key = _k(ns, "n")
    st.session_state.setdefault(count_key, len(spec0.align))
    align: list[ColumnPair] = []
    for i in range(st.session_state[count_key]):
        saved = spec0.align[i] if i < len(spec0.align) else None
        row = st.columns([4, 3, 4], gap="small", vertical_alignment="center")
        with row[0]:
            pa = _pick(f"{label_a} column {i + 1}", cols_a, saved.a if saved else None, _k(ns, f"al_a{i}"))
        with row[1]:
            modes = list(NORMALIZERS)
            mode = st.selectbox(f"Rule {i + 1}", modes, index=modes.index(saved.normalize) if saved else 0,
                                format_func=NORMALIZERS.get, key=_k(ns, f"al_m{i}"), label_visibility="collapsed",
                                help="Rows only match when this pair agrees after the cleanup shown. Always an exact comparison.")
        with row[2]:
            pb = _pick(f"{label_b} column {i + 1}", cols_b, saved.b if saved else None, _k(ns, f"al_b{i}"))
        align.append(ColumnPair(pa or "", pb or "", mode))
    b1, b2, _ = st.columns([2, 2, 5])
    b1.button("Add alignment column", icon=":material/add:", key=f"{ns}_add", width="stretch",
              disabled=st.session_state[count_key] >= 8,
              on_click=st.session_state.__setitem__, args=(count_key, st.session_state[count_key] + 1),
              help="A supporting column that must agree before two rows can match, such as a PO number or date.")
    b2.button("Remove last", icon=":material/remove:", key=f"{ns}_rem", width="stretch",
              disabled=st.session_state[count_key] == 0,
              on_click=st.session_state.__setitem__, args=(count_key, max(0, st.session_state[count_key] - 1)))

    row = st.columns([4, 3, 4], gap="small", vertical_alignment="center")
    with row[0]:
        id_a = _pick(f"{label_a} ID column", cols_a, spec0.id_a, _k(ns, "id_a"), optional=True)
    with row[1]:
        _rule_chip("Unique ID (optional)")
    with row[2]:
        id_b = _pick(f"{label_b} ID column", cols_b, spec0.id_b, _k(ns, "id_b"), optional=True)

    st.markdown("##### Matching volume")
    pattern = st.segmented_control(
        "Matching volume", PATTERNS, default=_pattern_of(spec0), key=_k(ns, "pattern"), label_visibility="collapsed",
        help=f"1 : 1 matches one row to one row. Many : 1 lets several {label_a} rows add up to one {label_b} row; "
             f"1 : Many is the reverse.",
    ) or PATTERNS[0]
    sizes = list(range(2, MAX_GROUP_SIZE + 1))
    max_a = max_b = 1
    s1, s2 = st.columns(2)
    if pattern in (PATTERNS[1], PATTERNS[3]):
        with s1:
            max_a = st.segmented_control(f"Up to how many {label_a} rows make one {label_b} row", sizes,
                                         default=max(2, spec0.max_a_per_b), key=_k(ns, "ma")) or 2
    if pattern in (PATTERNS[2], PATTERNS[3]):
        with s2:
            max_b = st.segmented_control(f"Up to how many {label_b} rows make one {label_a} row", sizes,
                                         default=max(2, spec0.max_b_per_a), key=_k(ns, "mb")) or 2
    st.markdown(f'<span class="cr-pill">Active: {_volume_text(label_a, label_b, max_a, max_b)}</span>', unsafe_allow_html=True)
    st.info("Groups must add up exactly, to the cent, inside the same alignment group. If more than one combination "
            "fits, the rows are flagged Ambiguous for you to decide, never guessed.", icon=":material/info:")

    st.markdown("##### Match ID column")
    add_id = st.toggle("Write a Match ID column back into both datasets", spec0.add_match_id, key=_k(ns, "addid"),
                       help="Every match gets its own ID (M-0001, M-0002, ...). Choose where it goes in each dataset.")
    header, position, after_a, after_b = spec0.match_id_header, spec0.match_id_position, None, None
    if add_id:
        c1, c2 = st.columns(2)
        header = c1.text_input("Match ID column name", spec0.match_id_header, key=_k(ns, "idh")).strip() or "Match ID"
        positions = list(ID_POSITIONS)
        position = c2.selectbox("Position", positions, index=positions.index(spec0.match_id_position),
                                format_func=ID_POSITIONS.get, key=_k(ns, "idp"))
        if position == "after":
            c1, c2 = st.columns(2)
            with c1:
                st.caption(f"After this {label_a} column")
                after_a = _pick(f"After {label_a} column", cols_a, spec0.match_id_after_a, _k(ns, "aft_a"))
            with c2:
                st.caption(f"After this {label_b} column")
                after_b = _pick(f"After {label_b} column", cols_b, spec0.match_id_after_b, _k(ns, "aft_b"))

    return ReconSpec(
        name=spec0.name, label_a=label_a, label_b=label_b,
        amount=ColumnPair(amount_a or "", amount_b or "") if amount_a and amount_b else None,
        align=align, id_a=id_a, id_b=id_b, max_a_per_b=int(max_a), max_b_per_a=int(max_b),
        add_match_id=add_id, match_id_header=header, match_id_position=position,
        match_id_after_a=after_a, match_id_after_b=after_b,
    )


def _volume_text(label_a: str, label_b: str, max_a: int, max_b: int) -> str:
    if max_a == 1 and max_b == 1:
        return f"1 {label_a} row : 1 {label_b} row"
    parts = []
    if max_a > 1:
        parts.append(f"up to {max_a} {label_a} rows : 1 {label_b} row")
    if max_b > 1:
        parts.append(f"1 {label_a} row : up to {max_b} {label_b} rows")
    return " and ".join(parts) + " (plus 1 : 1)"


def _save_preset(name: str, spec: ReconSpec) -> None:
    saved = ReconSpec.from_dict({**asdict(spec), "name": name})
    st.session_state["cr_notice"] = _store_mapping(saved)


def _save_box(spec: ReconSpec, blocking: list[str], default_name: str) -> None:
    with st.container(border=True):
        st.markdown("**Save this mapping**")
        c1, c2, c3 = st.columns([2, 1, 1], vertical_alignment="bottom")
        name = c1.text_input("Mapping name", default_name, key=_k("cr", "savename")).strip()
        c2.button("Save to library" if _db_config() else "Save for this session", icon=":material/save:", disabled=bool(blocking) or not name,
                  on_click=_save_preset, args=(name, spec), width="stretch", key="cr_save_btn")
        c3.download_button("Download as file", ReconSpec.from_dict({**asdict(spec), "name": name or spec.name}).to_json(),
                           file_name=f"{_safe(name or spec.name)}.json", mime="application/json",
                           icon=":material/download:", disabled=bool(blocking), width="stretch", key="cr_dl_btn")
        notice = st.session_state.pop("cr_notice", None)
        if notice:
            st.success(notice)
        problem = st.session_state.pop("cr_db_problem", None)
        if problem:
            st.warning(f"{problem} Your mapping was kept for this session.")


# ---------------------------------------------------------------------------
# The live rule summary
# ---------------------------------------------------------------------------

def _summary_html(spec: ReconSpec, frame_a: pd.DataFrame, frame_b: pd.DataFrame, blocking: list[str]) -> str:
    esc = html.escape
    lines = [
        f'<div class="cr-sum-title">Rule summary</div>',
        f'<div class="cr-sum-label">Inputs</div><div class="cr-sum-row">{_chip(spec.label_a)} {esc(spec.label_a)} '
        f'({len(frame_a.columns)} cols) <span class="cr-arrow">⟷</span> {_chip(spec.label_b)} {esc(spec.label_b)} '
        f'({len(frame_b.columns)} cols)</div>',
        '<div class="cr-sum-label">Match logic</div>',
    ]
    if spec.amount and spec.amount.a and spec.amount.b:
        lines.append(f'<div class="cr-sum-row">Amount: {esc(spec.amount.a)} = {esc(spec.amount.b)}, to the cent</div>')
    else:
        lines.append('<div class="cr-sum-row cr-sum-missing">Amount columns not chosen yet</div>')
    for pair in spec.align:
        if pair.a and pair.b:
            lines.append(f'<div class="cr-sum-row">Must align: {esc(pair.a)} = {esc(pair.b)} '
                         f'<span class="cr-sum-dim">({esc(NORMALIZERS[pair.normalize].split(" (")[0].lower())})</span></div>')
        else:
            lines.append('<div class="cr-sum-row cr-sum-missing">An alignment column is incomplete</div>')
    lines.append(f'<div class="cr-sum-label">Volume</div><div class="cr-sum-row">'
                 f'{esc(_volume_text(spec.label_a, spec.label_b, spec.max_a_per_b, spec.max_b_per_a))}</div>')
    ids = [c for c in (spec.id_a, spec.id_b) if c]
    lines.append('<div class="cr-sum-label">Identifiers</div><div class="cr-sum-row">'
                 + (f'ID columns: {esc(" / ".join(ids))}' if ids else "Row numbers label each row") + "</div>")
    if spec.add_match_id:
        lines.append(f'<div class="cr-sum-row">Match ID "{esc(spec.match_id_header)}": {esc(ID_POSITIONS[spec.match_id_position].lower())}</div>')
    rows = f"{len(frame_a):,} + {len(frame_b):,} rows"
    status = _badge("Ready to run", "ok") if not blocking else _badge(f"{len(blocking)} to fix", "warn")
    lines.append(f'<div class="cr-sum-foot">{rows} {status}</div>')
    return "".join(lines)


# ---------------------------------------------------------------------------
# Step 4: pre-flight and results
# ---------------------------------------------------------------------------

def _preflight(spec: ReconSpec, frame_a: pd.DataFrame, frame_b: pd.DataFrame, blocking: list[str]) -> list[tuple[str, str]]:
    """(tone, text) for each pre-flight line; tone is ok, warn, or bad."""
    checks = [("ok", f"Datasets uploaded: {len(frame_a):,} + {len(frame_b):,} rows")]
    checks.append(("ok", "Rules validated") if not blocking else ("bad", f"Rules incomplete: {blocking[0]}"))
    if spec.amount and spec.amount.a in frame_a.columns and spec.amount.b in frame_b.columns:
        bad = sum(parse_amount_cents(v) is None for v in frame_a[spec.amount.a]) + \
            sum(parse_amount_cents(v) is None for v in frame_b[spec.amount.b])
        checks.append(("ok", "0 unreadable amounts") if not bad else
                      ("warn", f"{bad:,} row(s) have a blank or non-numeric amount and will be listed as Excluded"))
    checks.append(("ok", "0 schema errors") if not blocking else ("bad", f"{len(blocking)} mapping problem(s)"))
    if not spec.align:
        checks.append(("warn", "No alignment columns: every row is compared with every row"))
    return checks


def _money_config(frame: pd.DataFrame) -> dict:
    return {c: st.column_config.NumberColumn(c, format="%.2f") for c in frame.columns
            if pd.api.types.is_numeric_dtype(frame[c]) and any(w in c.lower() for w in ("amount", "total", "difference"))}


def _show(frame: pd.DataFrame, empty: str) -> None:
    if frame.empty:
        st.info(empty)
    else:
        st.dataframe(frame, hide_index=True, width="stretch", column_config=_money_config(frame))


def _render_result(result: ReconResult, xlsx: bytes) -> None:
    spec = result.spec
    render_section_heading("Results", "Every row is accounted for; anything not matched is listed with its reason.")
    st.markdown(f"Overall: {status_badge(result.overall)}", unsafe_allow_html=True)
    for note in result.warnings:
        st.warning(note)
    bridge = result.bridge
    cols = st.columns(4)
    cols[0].metric("Matches", f"{len(result.matches):,}")
    cols[1].metric("Ambiguous rows", f"{int(bridge['Ambiguous Rows'].sum()):,}")
    cols[2].metric("Unmatched rows", f"{int(bridge['Unmatched Rows'].sum()):,}")
    cols[3].metric("Excluded rows", f"{int(bridge['Excluded Rows'].sum()):,}")
    st.download_button(
        "Download reconciliation workbook (.xlsx)", xlsx, file_name=f"Custom_Reconciliation_{_safe(spec.name)}.xlsx",
        mime=XLSX_MIME, type="primary", icon=":material/download:", key="cr_download",
    )
    tabs = st.tabs(["Controls", "Matches", "Exceptions", "Dataset bridge", f"{spec.label_a} with Match ID",
                    f"{spec.label_b} with Match ID"])
    with tabs[0]:
        st.dataframe(status_styler(result.controls, ["Status"]), hide_index=True, width="stretch")
    with tabs[1]:
        _show(result.matches, "Nothing matched under this mapping.")
    with tabs[2]:
        _show(result.exceptions, "Every row matched.")
    with tabs[3]:
        _show(result.bridge, "")
    with tabs[4]:
        _show(result.dataset_a, "")
    with tabs[5]:
        _show(result.dataset_b, "")


def _safe(text: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in text).strip("_") or "mapping"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def render() -> None:
    """Draw the custom-mapping wizard in place of the standard QuickBooks and Infinium workflow."""
    _keep_alive()
    st.session_state.setdefault(STEP_KEY, 0)
    step = st.session_state[STEP_KEY]
    spec0 = _spec_from(st.session_state.get(SPEC0_KEY))
    data: dict = st.session_state.get(DATA_KEY, {})
    have_data = "a" in data and "b" in data
    frame_a = data["a"][1] if "a" in data else None
    frame_b = data["b"][1] if "b" in data else None
    if (step >= 1 and spec0 is None) or (step >= 2 and not have_data):
        step = st.session_state[STEP_KEY] = 0 if spec0 is None else 1
    if step == 3 and st.session_state.get(DRAFT_KEY) is None:
        step = st.session_state[STEP_KEY] = 2

    draft = _spec_from(st.session_state.get(DRAFT_KEY))
    blocking: list[str] = []
    if draft is not None and have_data:
        blocking = validate_spec(draft, list(frame_a.columns), list(frame_b.columns))
    _stepper(step, [spec0 is not None, have_data, draft is not None and not blocking, RESULT_KEY in st.session_state])

    if step == 0:
        _step_template()
    elif step == 1:
        _step_upload(spec0)
    elif step in (2, 3):
        cols_a, cols_b = list(frame_a.columns), list(frame_b.columns)
        main, side = st.columns([3, 1], gap="large") if step == 2 else (st.container(), None)
        with main:
            if step == 2:
                render_section_heading("Configure the matching rules", "Pick the columns to reconcile on and how rows may combine.")
                token = hashlib.sha1(("|".join(cols_a) + "||" + "|".join(cols_b)).encode()).hexdigest()[:8]
                spec = _rules_form(spec0, cols_a, cols_b, f"cr{st.session_state.get('cr_ver', 0)}_{token}")
                blocking = validate_spec(spec, cols_a, cols_b)
                st.session_state[DRAFT_KEY] = asdict(spec)
                _save_box(spec, blocking, spec0.name if spec0.amount else "My mapping")
                for problem in blocking:
                    st.warning(problem)
            else:
                spec = draft
                _step_run(spec, frame_a, frame_b, blocking)
        if side is not None:
            with side, st.container(key="cr-summary"):
                st.markdown(_summary_html(spec, frame_a, frame_b, blocking), unsafe_allow_html=True)

    # The upload step loads data during this run, so read it again for the Next button.
    data = st.session_state.get(DATA_KEY, {})
    have_data = "a" in data and "b" in data
    frame_a = data["a"][1] if "a" in data else None
    frame_b = data["b"][1] if "b" in data else None
    _nav_bar(step, spec0 is not None, have_data, blocking, _spec_from(st.session_state.get(DRAFT_KEY)), frame_a, frame_b)


def _step_run(spec: ReconSpec, frame_a: pd.DataFrame, frame_b: pd.DataFrame, blocking: list[str]) -> None:
    render_section_heading("Review and run", "Confirm the checks below, then run the reconciliation.")
    left, right = st.columns([3, 2], gap="large")
    with left:
        st.markdown("##### Pre-flight checks")
        for tone, text in _preflight(spec, frame_a, frame_b, blocking):
            icon = {"ok": "✓", "warn": "!", "bad": "✕"}[tone]
            st.markdown(f'<div class="cr-check cr-check--{tone}"><span class="cr-check-icon">{icon}</span>'
                        f"{html.escape(text)}</div>", unsafe_allow_html=True)
    with right, st.container(key="cr-summary-inline"):
        st.markdown(_summary_html(spec, frame_a, frame_b, blocking), unsafe_allow_html=True)
    signature = _signature(frame_a, frame_b, spec)
    stored = st.session_state.get(RESULT_KEY)
    if stored and stored[0] == signature:
        _render_result(stored[1], stored[2])
    elif stored:
        st.info("The files or mapping changed since the last run. Run the reconciliation again.")


def _signature(frame_a: pd.DataFrame, frame_b: pd.DataFrame, spec: ReconSpec) -> str:
    return hashlib.sha256((frame_a.to_csv(index=False) + "\x00" + frame_b.to_csv(index=False)
                           + "\x00" + spec.to_json()).encode("utf-8")).hexdigest()


def _run(frame_a: pd.DataFrame, frame_b: pd.DataFrame, spec: ReconSpec) -> None:
    result = reconcile(frame_a, frame_b, spec)
    st.session_state[RESULT_KEY] = (_signature(frame_a, frame_b, spec), result, build_workbook(result))


def _nav_bar(step: int, has_template: bool, have_data: bool, blocking: list[str], draft: Optional[ReconSpec],
             frame_a: Optional[pd.DataFrame], frame_b: Optional[pd.DataFrame]) -> None:
    """The sticky Back / Next / Run bar at the bottom of every step."""
    with st.container(key="cr-nav"):
        back, hint, go = st.columns([1, 3, 2], vertical_alignment="center")
        if step > 0:
            back.button("Back", icon=":material/arrow_back:", key="cr_back", on_click=_go, args=(step - 1,), width="stretch")
        if step == 3 and draft is not None and frame_a is not None:
            rows = len(frame_a) + len(frame_b)
            go.button(f"Run reconciliation ({rows:,} rows)", icon=":material/play_arrow:", type="primary", key="cr_run",
                      disabled=bool(blocking), width="stretch", on_click=_run, args=(frame_a, frame_b, draft))
        elif step == 1:
            hint.caption("Upload both datasets to continue." if not have_data else "Both datasets are loaded.")
            go.button("Next: configure rules", icon=":material/arrow_forward:", type="primary", key="cr_next",
                      disabled=not have_data, on_click=_go, args=(2,), width="stretch")
        elif step == 2:
            hint.caption("Fix the items above to continue." if blocking else "Rules are complete.")
            go.button("Next: review and run", icon=":material/arrow_forward:", type="primary", key="cr_next",
                      disabled=bool(blocking), on_click=_go, args=(3,), width="stretch")
        elif step == 0:
            hint.caption("Choose a template above to continue." if not has_template else "Template chosen.")
            go.button("Next: upload data", icon=":material/arrow_forward:", type="primary", key="cr_next",
                      disabled=not has_template, on_click=_go, args=(1,), width="stretch")
