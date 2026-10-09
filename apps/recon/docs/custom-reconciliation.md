# Custom mapping mode (Data Reconciliation Studio)

An optional mode of the Recon page (`/recon`): the **Mode** control under the title switches between
"QuickBooks and Infinium" (the standard workflow) and "Custom mapping", which reconciles any two datasets with a
mapping the user builds. It is separate from
the QuickBooks/Infinium engine (`matching/`): nothing in `apps/recon/matching` changes, and that engine's
rules (PO/invoice evidence, fuzzy passes, vendor aliases) do not apply here. QuickBooks and Infinium appear
only as the placeholder starting mapping (`PLACEHOLDER` in `custom/ui.py`).

| Module (`apps/recon/custom/`) | Owns |
|---|---|
| `spec.py` | `ReconSpec` (the mapping), JSON save/load, `find_column`, `missing_columns` (does a saved mapping fit these files), `validate_spec`. Column names only, never data. |
| `engine.py` | `reconcile(frame_a, frame_b, spec)`: every matching rule, plus the controls. |
| `export.py` | The Excel workbook (Summary with controls, bridge and mapping used; Matches; Match Detail; Exceptions; both datasets with the Match ID column). No Streamlit. |
| `ui.py` | `render()`: a four-step wizard (Template, Upload data, Configure rules, Review & run) with a template library, dataset cards with previews and status badges, a mapping grid, a live rule summary, pre-flight checks and a sticky Back/Next/Run bar. Called from `apps/recon/app.py` when Mode is Custom mapping; it is not a registered page, so navigation, the Dashboard and `mongodb/tools` are unchanged. |

## What the user sets

1. **Columns to reconcile on:** one amount column per dataset, equal to the signed cent.
2. **Supporting columns that must align:** pairs of columns (up to 8). Rows only match when every pair agrees and
   none is blank. Each pair is compared as text, letters and digits, digits only, amount, or date. These are all exact
   comparisons after the stated cleanup; there is no "close enough" mode.
3. **Unique identifier:** an optional existing ID column per dataset, used to label rows in results and checked for
   duplicates. Every match also gets a generated Match ID (`M-0001`), which can be written back into each dataset as
   the first column, the last column, or after a chosen column.
4. **Matching volume:** how many rows of A may add up to one row of B (and the reverse), from 1 to 1 up to 6 to 1.

## Matching rules (`engine.py` docstring has the full list)

One to one first, then k = 2, 3, ... smallest first. Only rows inside the same alignment group are searched.
Ambiguity is never resolved: two rows with the same amount in a group, a target with more than one fitting
combination, or a row claimed by two targets are all flagged **Ambiguous** and left for review. Zero-amount rows
match one to one only. Rows with a blank or non-numeric amount, or a blank alignment value, are **Excluded**,
never dropped. The search has a step budget per group (`SEARCH_BUDGET`); when it runs out the group is left
unmatched with a REVIEW control, not partly searched.

## Saved mappings

Two places, chosen automatically. With a database configured (see `db/connection.py` and `.streamlit/secrets.toml.example`)
mappings are saved to the organization's shared library (`store.py`, tables `recon_mapping_templates` and
`recon_mapping_versions`): every save of a changed mapping adds an immutable version, saving an identical one writes
nothing, and the template step shows them with a "Shared" tag. With no database, or if it cannot be reached, mappings
live in the session (`st.session_state["cr_presets"]`) and the page says so. A mapping can also be downloaded as JSON and
loaded again; loading one saves it the same way. Loading a mapping onto files that lack one of its columns blanks that
field and names the missing column. A preset never runs on columns the file does not have.

The app connects as `app_login` and refuses an owner or administrator connection. Its settings use
`AUTOREK_APP_DATABASE_URL` and `AUTOREK_ORG_ID`, never `DATABASE_URL` (the owner string `db.migrate` uses).

## Tests

`apps/recon/custom/tests/test_engine.py`.

## Wizard notes

- Streamlit forgets the widgets of a step that is off screen. Mapping widget keys are registered with `_k()` and
  re-stored each run by `_keep_alive()`; loaded datasets (`cr_data`), the chosen template (`cr_spec0`) and the draft
  mapping (`cr_draft`) are plain session keys. Buttons and uploaders must never be registered.
- The template library is `BUILT_IN` in `ui.py` plus mappings saved this session. Only QuickBooks and Infinium exists
  because it is the only mapping with real column names; add a card by adding a `ReconSpec` there.
- Not built, on purpose: ratings or popularity badges, credit balance and Pro gating (no billing exists), an
  organization switcher, dark mode, and any fuzzy or AI matching (it would break the no-closest-match rule).
