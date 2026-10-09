"""Content for the front landing page (apps/landing.py): the synthetic example, the walkthrough, the FAQ.

Every number the page shows is computed here, in integer cents, from the small row lists below, so the
reconciliation counts, the FIFO cost, and the sample downloads always agree with each other. The vendors,
customers, and amounts are made up. Nothing here reads the real accounting engines.
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass

# --- Data Reconciliation Studio: QuickBooks sales vs Infinium sales -------------------------------------------

# (row id, customer, PO, amount in cents)
QB_ROWS = (
    ("QB-101", "Harbor Beverage", "48211", 481250),
    ("QB-102", "Lakeside Foods", "48219", 126000),
    ("QB-103", "Summit Spirits", "48230", 907525),
    ("QB-104", "Northgate Grocers", "48244", 234080),
    ("QB-105", "Riverbend Co-op", "48251", 68840),
    ("QB-106", "Harbor Beverage", "48263", 315000),
    ("QB-107", "Pioneer Wholesale", "48270", 542510),
    ("QB-108", "Cedar Ridge Market", "48288", 91275),
)
INF_ROWS = (
    ("INF-201", "Harbor Beverage", "48211", 481250),
    ("INF-202", "Lakeside Foods", "48219", 126000),
    ("INF-203", "Summit Spirits", "48230", 907525),
    ("INF-204", "Northgate Grocers", "48244", 234080),
    ("INF-205", "Riverbend Co-op", "48251", 68840),
    ("INF-206", "Harbor Beverage", "48263", 315000),
    ("INF-207", "Pioneer Wholesale", "48270", 545210),
    ("INF-209", "Orchard Fresh", "48301", 110000),
)


def money(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    cents = abs(cents)
    return f"{sign}${cents // 100:,}.{cents % 100:02d}"


def reconcile() -> dict:
    """Exact-cent matching on PO: equal amounts match; anything else stays open for a person (no 'closest match')."""
    inf_by_po = {po: (row_id, amount) for row_id, _, po, amount in INF_ROWS}
    matched, exceptions, used = [], [], set()
    for row_id, customer, po, amount in QB_ROWS:
        other = inf_by_po.get(po)
        if other and other[1] == amount:
            matched.append((row_id, other[0], customer, po, amount))
            used.add(po)
        elif other:
            used.add(po)
            diff = amount - other[1]
            exceptions.append((row_id, other[0], customer, po, amount,
                               f"Amount differs by {money(abs(diff))}: Infinium shows {money(other[1])}"))
        else:
            exceptions.append((row_id, "", customer, po, amount, "No Infinium record for this PO"))
    for row_id, customer, po, amount in INF_ROWS:
        if po not in used:
            exceptions.append(("", row_id, customer, po, amount, "No QuickBooks record for this PO"))
    return {
        "matched": matched,
        "exceptions": exceptions,
        "matched_cents": sum(m[4] for m in matched),
        "qb_total": sum(r[3] for r in QB_ROWS),
        "inf_total": sum(r[3] for r in INF_ROWS),
    }


# --- Inventory Costing & Analytics: strict FIFO -----------------------------------------------------------------

# (layer, source, units, total extended value in cents). Receipt PRICE is the total value, as in the FIFO app.
LAYERS = (
    ("Opening (P11 ending)", 4000, 72000),
    ("Receipt R-1", 6000, 114000),
    ("Receipt R-2", 5000, 100000),
)
UNITS_USED = 7500


def fifo() -> dict:
    """Consume oldest layers first. Returns the usage detail, the ending layers, and the cents that tie out."""
    remaining = UNITS_USED
    usage, ending = [], []
    for name, units, value in LAYERS:
        take = min(units, remaining)
        remaining -= take
        # value * take / units, rounded to the cent (half up) in integer arithmetic
        cost = (value * take * 2 + units) // (units * 2) if take else 0
        if take:
            usage.append((name, take, cost))
        left = units - take
        if left:
            ending.append((name, left, value - cost))
    assert remaining == 0, "sample usage exceeds the available layers"
    return {
        "usage": usage, "ending": ending,
        "cost_of_usage": sum(u[2] for u in usage),
        "ending_value": sum(e[2] for e in ending),
        "available_value": sum(layer[2] for layer in LAYERS),
        "ending_units": sum(e[1] for e in ending),
    }


# --- Transaction Preparation & Review ---------------------------------------------------------------------------

# (raw vendor text, amount in cents, standardized vendor or "", review queue)
TXN_ROWS = (
    ("AMAZON MKTPLACE PMTS", 18420, "Amazon", "Ready"),
    ("AMZN Mktp US*2K4TT81", 6395, "Amazon", "Ready"),
    ("SYSCO FOODS #4412", 142875, "Sysco Corporation", "Ready"),
    ("Sysco Corp", 98010, "Sysco Corporation", "Ready"),
    ("STAPLES 0291", 21480, "Staples", "Ready"),
    ("WM SUPERCENTER 1127", 7342, "Walmart", "Ready"),
    ("RIVERBEND PACKAGING LLC", 310000, "", "Needs vendor mapping"),
    ("GLOBAL LABEL & TAPE", 45500, "", "Needs vendor mapping"),
    ("CITY WATER UTILITY", 38860, "City Water Utility", "Review sales tax"),
)


def review() -> dict:
    queues: dict[str, list] = {}
    for row in TXN_ROWS:
        queues.setdefault(row[3], []).append(row)
    total = sum(r[1] for r in TXN_ROWS)
    return {
        "queues": queues, "total": total, "rows": len(TXN_ROWS),
        "vendors": len({r[2] for r in TXN_ROWS if r[2]}),
        # The Sales Tax app's own control: cleaned dollars must equal source dollars.
        "control_diff": total - sum(r[1] for q in queues.values() for r in q),
    }


# --- Sample downloads ---------------------------------------------------------------------------------------------

def _csv(header: list[str], rows: list[list]) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8")


def _dollars(cents: int) -> str:
    return f"{cents // 100}.{cents % 100:02d}" if cents >= 0 else f"-{abs(cents) // 100}.{abs(cents) % 100:02d}"


def exception_report_rows() -> tuple[list[str], list[list]]:
    header = ["QuickBooks Row", "Infinium Row", "Customer", "PO", "Amount", "Why it is open"]
    rows = [[q, i, c, po, _dollars(a), why] for q, i, c, po, a, why in reconcile()["exceptions"]]
    return header, rows


def costing_report_rows() -> tuple[list[str], list[list]]:
    result = fifo()
    header = ["Section", "Layer", "Units", "Value"]
    rows = [["Cost of usage", n, u, _dollars(c)] for n, u, c in result["usage"]]
    rows.append(["Cost of usage", "Total", UNITS_USED, _dollars(result["cost_of_usage"])])
    rows += [["Ending inventory", n, u, _dollars(v)] for n, u, v in result["ending"]]
    rows.append(["Ending inventory", "Total", result["ending_units"], _dollars(result["ending_value"])])
    return header, rows


def transactions_rows() -> tuple[list[str], list[list]]:
    header = ["Original Vendor Text", "Amount", "Standard Vendor", "Review Queue"]
    return header, [[raw, _dollars(a), vendor, queue] for raw, a, vendor, queue in TXN_ROWS]


REPORTS = (
    ("exceptions", "Sample exception report", "Reconciliation", "sample_exception_report.csv", exception_report_rows,
     "Every open item with the reason it was not matched."),
    ("costing", "Sample inventory costing report", "Inventory costing", "sample_inventory_costing_report.csv",
     costing_report_rows, "Cost of usage and ending layers, oldest first."),
    ("transactions", "Sample cleaned transaction export", "Transaction review", "sample_cleaned_transactions.csv",
     transactions_rows, "Raw vendor text beside its standardized record and review queue."),
)


def report_csv(key: str) -> bytes:
    for k, _, _, _, builder, _ in REPORTS:
        if k == key:
            return _csv(*builder())
    raise KeyError(key)


# --- Walkthrough: how the platform adapts to a visitor's data -----------------------------------------------------

@dataclass(frozen=True)
class Step:
    title: str
    blurb: str
    available: tuple[str, ...]
    planned: tuple[str, ...]


WALKTHROUGH = (
    Step("Choose sources", "Start from the files your accounting systems already export.",
         ("Excel (.xlsx) and CSV uploads", "QuickBooks and Infinium exports as the example reconciliation template"),
         ("Direct QuickBooks Online connection", "Outlook invoice mail intake beyond the prototype")),
    Step("Map fields", "Tell the workflow which column is the PO, the amount, the period.",
         ("Each tool shows a sample template of the layout it expects", "Column mapping happens inside each tool"),
         ("One reusable mapping saved per company", "Mapping suggestions from column names")),
    Step("Configure rules", "Decide how records are matched, costed, and cleaned.",
         ("Exact-cent matching: close is never accepted", "Strict oldest-first FIFO costing",
          "Vendor mapping file for transaction cleanup"),
         ("A rule editor for your own match and cleanup rules", "Per-customer tolerance policies")),
    Step("Review results", "Spend your time on the exceptions, not the matches.",
         ("Exceptions with the reason each is open", "PASS / REVIEW / FAIL controls", "Excel workpapers to download"),
         ("Saved projects and history", "Review queues shared across a team")),
)


# --- Solutions ------------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Solution:
    id: str
    name: str
    tab: str
    app_path: str       # the registered page this workflow opens
    icon: str           # Material Symbols name; the registry (shared/layout.py) uses the same icon for the page
    tagline: str
    benefits: tuple[str, ...]
    example_note: str
    demo_hint: str      # shown on the demo banner inside the tool: what is loaded and where to look


SOLUTIONS = (
    Solution("recon", "Data Reconciliation Studio", "Reconciliation", "recon", "compare_arrows",
             "Bring two sources together, match them to the cent, and review only what is left.",
             ("Matching is automatic; open items arrive with the reason attached.",
              "Ambiguity is never guessed away: a near miss stays open for a person to decide.",
              "QuickBooks and Infinium are the example template; the same workflow fits other pairs of exports."),
             "Example template: QuickBooks sales against Infinium sales.",
             "The QuickBooks and Infinium example template is loaded with sample files. Choose Run Reconciliation to see the matches and the open items."),
    Solution("fifo", "Inventory Costing & Analytics", "Inventory Costing", "fifo-inventory", "inventory_2",
             "Turn receipts and balances into FIFO layers, cost summaries, and period reports.",
             ("Oldest layers are consumed first, and a layer never goes negative.",
              "Preview a period before you close it; only closing changes the official layers.",
              "Controls report PASS, REVIEW, or FAIL, with an Excel report for the file."),
             "Example workflow: strict FIFO on one raw material, one period.",
             "The sample Master Grid and receipts are loaded. Under Period Processing, run the preview to see the cost summary and controls; closing a period is disabled in a demo."),
    Solution("review", "Transaction Preparation & Review", "Transaction Review", "sales-tax", "receipt_long",
             "Turn raw transactions into standardized vendor records and organized review queues.",
             ("Messy vendor text is matched to one standard record.",
              "Unknown vendors and tax questions land in their own queues instead of hiding in the list.",
              "A dollar control proves the cleaned file equals the source before you download."),
             "Example: a week of card and bank transactions.",
             "The sample source transactions and vendor mapping are loaded. Run the cleanup to see standardized vendors and review queues; the download stays disabled until the dollar control is $0.00."),
)
SOLUTION_BY_ID = {s.id: s for s in SOLUTIONS}
SOLUTION_BY_APP = {s.app_path: s for s in SOLUTIONS}  # url_path of the registered page -> its solution


# --- FAQ and product background -------------------------------------------------------------------------------------

FAQS = (
    ("Which systems does it work with?",
     "Today it reads Excel (.xlsx) and CSV exports. The reconciliation example is built for QuickBooks and Infinium "
     "sales exports, inventory costing for a receipts-and-balances grid, and transaction review for a transaction "
     "list with a vendor mapping. Direct connections to accounting systems are planned, not available."),
    ("How long does setup take?",
     "There is nothing to install. Open a demo to see a populated result, then upload your own exports. Choosing a "
     "template and checking how your columns line up is the whole setup for a first workflow."),
    ("What happens to the files I upload?",
     "Uploads are held in memory for your browser session and cleared when you leave the tool. Excel outputs are "
     "built on request for you to download and are not kept. A few small files are saved on the server: closed "
     "inventory periods, a shared sales-tax trial balance cache, and confirmed vendor aliases."),
    ("Is the checkout real?",
     "No. The purchase flow is a clearly labeled simulation: no payment processor is connected, nothing is charged, "
     "and card details are checked and discarded. Prices are placeholders."),
    ("Is the sign-in secure?",
     "The sign-in in this prototype is simulated. The demo account needs a login, a password (kept only as a salted "
     "hash for your session) and a code from an authenticator app that works offline. It personalizes the page and "
     "does not control access to any tool."),
    ("What support is included?",
     "Starter and Professional include email support. Enterprise adds dedicated support. Response-time commitments "
     "have not been set yet."),
)

BACKGROUND = (
    "Janota FIN started at the month-end desk of a bottling and packaging operation. The work was the same every "
    "period: cost raw materials oldest-first, match QuickBooks sales to the Infinium ledger, and clean up a pile of "
    "transactions before sales tax. Each job began as spreadsheets and an Excel macro.",
    "Each one was rebuilt as a tool with the controls an accountant expects: amounts agree to the cent, nothing is "
    "guessed, and every number can be traced to a source row. The platform widens that approach to other data, "
    "while the original workflows stay as working examples.",
)
