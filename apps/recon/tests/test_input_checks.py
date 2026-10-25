"""Pre-match input checks: every row and dollar must be accounted for, and bad inputs must be flagged."""
from apps.recon import run_store
from apps.recon.ingestion import filter_qb_subtotal_rows, read_source_file
from apps.recon.input_checks import NO_FILE, SourceInput, build_input_checks

HEADER = "Period,Customer,PO,Invoice,Qty,Amount"
QB_MAPPING = {"quantity": "Qty", "amount": "Amount"}


def _qb(*lines: str) -> SourceInput:
    data = "\n".join([HEADER, *lines]).encode()
    imported = read_source_file(data, "qb.csv", 0)
    used, _ = filter_qb_subtotal_rows(imported, QB_MAPPING)
    return SourceInput("QuickBooks", imported, used, "Amount", "a" * 64)


def _checks(*sources, saved=None):
    return build_input_checks(list(sources), saved).set_index(["Dataset", "Check"])


def _row(frame, dataset, check):
    return frame.loc[(dataset, check)]


CLEAN = (
    "1,Acme,PO1,INV1,1,100.00",
    "1,Acme,PO2,INV2,2,250.50",
)


def test_a_clean_file_passes_every_check():
    frame = _checks(_qb(*CLEAN))
    assert set(frame["Status"]) == {"PASS"}
    assert "2 row(s) below the header: 2 used" in _row(frame, "QuickBooks", "Rows in the file are all accounted for")["Details"]


def test_every_kind_of_removed_row_is_counted_and_the_file_still_ties():
    src = _qb(
        *CLEAN,
        ",,,,,",                                  # blank row
        HEADER,                                   # header repeated inside the data
        "Total for Acme,,,,,350.50",              # labelled total, carries an amount
    )
    row = _row(_checks(src), "QuickBooks", "Rows in the file are all accounted for")
    assert row["Status"] == "PASS"
    for part in ("5 row(s) below the header", "2 used", "1 blank", "1 repeated header", "1 subtotal or incomplete"):
        assert part in row["Details"], part
    left_out = _row(_checks(src), "QuickBooks", "Amount in rows left out")
    assert left_out["Status"] == "PASS" and "$350.50" in left_out["Details"]


def test_an_incomplete_row_with_an_amount_is_flagged_for_a_human_look():
    src = _qb(*CLEAN, "1,,PO9,INV9,1,75.00")      # blank customer: dropped as incomplete, but it carries money
    row = _row(_checks(src), "QuickBooks", "Amount in rows left out")
    assert row["Status"] == "WARNING" and "$75.00" in row["Details"] and "real transaction" in row["Details"]


def test_rows_the_import_did_not_report_make_the_tie_out_fail():
    src = _qb(*CLEAN)
    src.imported.attrs["ingestion_audit"]["source_rows_below_header"] += 3
    row = _row(_checks(src), "QuickBooks", "Rows in the file are all accounted for")
    assert row["Status"] == "FAIL" and "3 unexplained" in row["Details"]


def test_blank_unreadable_zero_and_negative_amounts_are_each_counted():
    src = _qb(*CLEAN, "1,Acme,PO3,INV3,1,", "1,Acme,PO4,INV4,1,abc", "1,Acme,PO5,INV5,1,0.00", "1,Acme,PO6,INV6,1,-40.25")
    frame = _checks(src)
    quality = _row(frame, "QuickBooks", "Amounts: blank, unreadable, zero")
    assert quality["Status"] == "WARNING"
    for part in ("1 blank", "1 unreadable", "1 zero"):
        assert part in quality["Details"], part
    credits = _row(frame, "QuickBooks", "Credits and negative amounts")
    assert credits["Status"] == "PASS" and "1 negative amount(s) totalling -$40.25" in credits["Details"]


def test_the_same_file_on_both_sides_is_flagged_and_different_files_are_not():
    qb = _qb(*CLEAN)
    inf = SourceInput("Infinium", qb.imported, qb.used, "Amount", qb.sha256)
    same = _checks(qb, inf)
    assert _row(same, "All files", "Different files uploaded")["Status"] == "WARNING"
    other = SourceInput("Infinium", qb.imported, qb.used, "Amount", "b" * 64)
    assert _row(_checks(qb, other), "All files", "Different files uploaded")["Status"] == "PASS"


def test_an_empty_upload_slot_is_not_mistaken_for_a_repeat():
    qb = _qb(*CLEAN)
    empty = [SourceInput(f"Slot {n}", qb.imported, qb.used, "Amount", NO_FILE) for n in (1, 2)]
    frame = build_input_checks([qb, *empty])
    assert frame[frame["Check"] == "Different files uploaded"].empty


def test_a_file_used_in_a_saved_run_is_named():
    qb = _qb(*CLEAN)
    frame = _checks(qb, saved={qb.sha256: "RUN-0042"})
    row = _row(frame, "QuickBooks", "File not used before")
    assert row["Status"] == "WARNING" and "RUN-0042" in row["Details"]


class _FakeConn:
    def __init__(self, rows):
        self.rows, self.calls = rows, []

    def execute(self, sql, params):
        self.calls.append((sql, params))
        return self

    def fetchall(self):
        return self.rows


def test_runs_using_hashes_returns_the_most_recent_run_and_skips_the_query_for_nothing():
    conn = _FakeConn([("h1", "OLD"), ("h1", "NEW"), ("h2", "ONLY")])
    assert run_store.runs_using_hashes(conn, ["h2", "h1", "h1", ""]) == {"h1": "NEW", "h2": "ONLY"}
    assert conn.calls[0][1] == (["h1", "h2"],)
    empty = _FakeConn([])
    assert run_store.runs_using_hashes(empty, ["", None]) == {} and empty.calls == []


def test_the_import_records_how_many_rows_were_below_the_header():
    data = "\n".join([HEADER, *CLEAN, ",,,,,"]).encode()
    audit = read_source_file(data, "qb.csv", 0).attrs["ingestion_audit"]
    assert audit["source_rows_below_header"] == 3
