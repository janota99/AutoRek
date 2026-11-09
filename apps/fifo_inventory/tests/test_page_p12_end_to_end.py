"""FY2026 Period 12 end to end through the real page: the July 31 CSI Caps receipt, a workbook period-end date that
disagrees with the verified Workspace calendar, and a genuinely future receipt. Preview is reached, the Excel download
is enabled, and P12 purchases are the validated $751,939.21."""
import io
from datetime import datetime

from openpyxl import load_workbook
from streamlit.testing.v1 import AppTest

from apps.fifo_inventory import materials

from .test_workbook_import import CALENDAR, COUNTS, _log_row, _workbook

# The P12 receipts of operations' log (material, delivered, quantity, total cost, PO, invoice, vendor), the July 31 CSI
# Caps delivery included; it is dated in P11 but labelled P.12.
P12_LOG = [
    ("Preforms", (2026, 8, 4), 1_695_168, 43_125.07, "3653", "Inv#900346624", "Plastipak"),
    ("Preforms", (2026, 8, 7), 1_695_168, 43_125.07, "3659", "Inv#900350412", "Plastipak"),
    ("Preforms", (2026, 8, 11), 1_695_168, 43_125.07, "3654", "Inv#900351483", "Plastipak"),
    ("Preforms", (2026, 8, 13), 1_695_168, 43_125.07, "3656", "Inv#900354563", "Plastipak"),
    ("Preforms", (2026, 8, 15), 1_695_168, 42_023.21, "3671", "Inv#900355350", "Plastipak"),
    ("Preforms", (2026, 8, 15), 1_695_168, 42_023.21, "3657", "Inv#900356133", "Plastipak"),
    ("Preforms", (2026, 8, 20), 1_695_168, 42_023.21, "3658", "Inv#900359105", "Plastipak"),
    ("Preforms", (2026, 8, 21), 1_695_168, 42_023.21, "3655", "Inv#900359805", "Plastipak"),
    ("Preforms", (2026, 8, 26), 1_695_168, 42_023.21, "3660", "Inv#900362823", "Plastipak"),
    ("CLOSURES", (2026, 7, 31), 9_000_000, 65_520.00, "3807", "Inv#1115187", "CSI"),
    ("CLOSURES", (2026, 8, 5), 9_000_000, 65_520.00, "3809", "Inv#1116202", "CSI"),
    ("CLOSURES", (2026, 8, 12), 9_000_000, 65_520.00, "3810", "Inv#1116981", "CSI"),
    ("CLOSURES", (2026, 8, 26), 9_000_000, 67_488.64, "3853", "Inv#1118944 rec. 8/27/2026", "CSI"),
    ("Labels = Panhandle", (2026, 8, 5), 21_600_000, 53_136.00, "3676", "Inv#3001278", "Amcor"),
    ("Labels = Panhandle", (2026, 8, 13), 21_108_600, 52_138.24, "3684", "Inv#3001774", "Amcor"),
]
EXPECTED_P12_PURCHASES = "$751,939.21"
# Delivered in P13 but labelled P.12: genuinely future, so it stays pending and is never in P12.
FUTURE = ("Preforms", (2026, 9, 10), 600, 30.00, "9999", "Inv#FUT", "Plastipak")


def _data(wrong_p12_end=True):
    rows = [_log_row(item, datetime(*day), qty, total, invoice=invoice, po=po, vendor=vendor)
            for item, day, qty, total, po, invoice, vendor in P12_LOG + [FUTURE]]
    wb = load_workbook(io.BytesIO(_workbook(rows, COUNTS)))
    sheet = wb["PERIOD END 2026"]
    for column, end in enumerate(CALENDAR.period_ends, start=5):
        sheet.cell(row=1, column=column, value=datetime(end.year, end.month, end.day))
    if wrong_p12_end:
        sheet.cell(row=1, column=16, value=datetime(2026, 9, 5))      # operations' P12 end date: wrong
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def _run(monkeypatch, tmp_path, snapshot_dir, data, verified=True):
    monkeypatch.setenv("FISCAL_SETTINGS_PATH", str(tmp_path / "fiscal.json"))
    monkeypatch.setattr(materials, "ALIASES_PATH", tmp_path / "aliases.json")

    class Upload(io.BytesIO):
        name = "Raw Goods & Receivings.xlsx"

        def getvalue(self):
            return data

    import streamlit as st
    monkeypatch.setattr(st, "file_uploader", lambda label, *a, key=None, **k: Upload(data) if key == "ops_workbook_upload" else None)
    at = AppTest.from_file("apps/fifo_inventory/app.py", default_timeout=180)
    at.session_state["suite_fiscal_loaded"] = True
    at.session_state["suite_fiscal_calendar"] = CALENDAR
    at.session_state["suite_fiscal_calendars"] = {CALENDAR.year: CALENDAR}
    at.session_state["suite_fiscal_verified"] = {CALENDAR.year} if verified else set()
    at.session_state["suite_fiscal_end"] = None
    at.session_state["fifo_fiscal_year"] = 2026
    at.session_state["fifo_fiscal_period"] = 12
    return at.run()


def _metric(at, label):
    return next(m.value for m in at.metric if m.label == label)


def test_fy2026_p12_reaches_preview_and_enables_the_excel_download(monkeypatch, tmp_path, snapshot_dir):
    at = _run(monkeypatch, tmp_path, snapshot_dir, _data())
    assert not at.exception
    assert _metric(at, "Purchases") == EXPECTED_P12_PURCHASES                  # includes the July 31 CSI Caps receipt
    assert any(d.label == "Download FIFO workpaper (Excel)" for d in at.get("download_button"))
    assert not at.error                                                       # nothing blocks on a date
    warnings = " ".join(w.value for w in at.warning)
    assert "Sep 05, 2026" in warnings and "Aug 29, 2026" in warnings          # the discrepancy warns, naming both dates
    late = next(e for e in at.expander if e.label.startswith("Late receipts picked up"))
    assert late.label == "Late receipts picked up (1)"                        # the July 31 receipt, informational
    assert any("labelled for period 12 but dated outside" in w.value for w in at.warning)   # the future one stays pending


def test_without_a_saved_calendar_the_same_workbook_stops_before_preview(monkeypatch, tmp_path, snapshot_dir):
    at = _run(monkeypatch, tmp_path, snapshot_dir, _data(), verified=False)
    assert not at.exception and not any(m.label == "Purchases" for m in at.metric)
    assert not any(d.label == "Download FIFO workpaper (Excel)" for d in at.get("download_button"))
