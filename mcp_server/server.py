"""Campus Customs MCP server.

The shared toolbox for the ops agent team. Every tool works on the shop's
working database, data/campus_customs_new.db. Every number a tool returns
comes straight from the database, and "today" is always desk.date_today,
never the computer clock.

Three kinds of tools:
  read     look things up; open the database read-only
  board    post drafts and approval requests to the `board` table; never move money
  human    approve/reject requests, pay approved requests, refresh the working copy;
           tagged "human_only" and never given to any agent

Run (stdio):  python mcp_server/server.py
"""

import calendar
import hashlib
import json
import os
import re
import shutil
import sqlite3
from contextlib import closing
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Annotated, Literal

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from pydantic import Field

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
ORIGINAL_DB_PATH = DATA_DIR / "campus_customs.db"  # only ever read, by refresh_working_copy
# Tests and demos set CAMPUS_CUSTOMS_WORK_DIR to a temp folder so they never touch the real working copy.
WORK_DIR = Path(os.environ["CAMPUS_CUSTOMS_WORK_DIR"]) if os.environ.get("CAMPUS_CUSTOMS_WORK_DIR") else DATA_DIR
DB_PATH = WORK_DIR / "campus_customs_new.db"

READ_ONLY = {"readOnlyHint": True, "idempotentHint": True, "openWorldHint": False}
BOARD_WRITE = {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}
HUMAN_WRITE = {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": False}

AGENT_NAMES = {"boss", "inventory", "accounting", "facilities", "customer service"}
TICKET_STATUSES = ("open", "in_progress", "waiting_on_human", "blocked", "resolved")
AWAITING_HUMAN = ("pending_approval", "approved")  # payment/price requests not finished yet
BOARD_LIST_LIMIT = 25  # keeps get_board replies small (token budget)
MAX_SUBJECT, MAX_BODY = 120, 1200
MAX_VENDOR_QTY = 200

mcp = FastMCP(
    name="campus-customs",
    instructions=(
        "Tools for the Campus Customs back office. Shop rules: "
        "(1) 'today' is desk.date_today from the database, not the real date; overdue means due before today. "
        "(2) Vendor lead times come only from vendors.lead_days. "
        "(3) A vendor with any open invoice does not ship. "
        "(4) Every payment needs human approval. "
        "(5) A payment updates payments, the invoice or lease, and cash_accounts together. "
        "(6) A payment the cash balance can't cover is refused. "
        "(7) Cash only goes out; sales never add money. "
        "(8) The working database is refreshed from the original before each run. "
        "(9) Never email customers or call vendors; drafts stay on the board. "
        "Agents can read and post to the board. Only a human can approve, pay or refresh."
    ),
)

BOARD_SCHEMA = """
CREATE TABLE IF NOT EXISTS board (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,            -- payment_request | price_override | customer_draft | vendor_draft | boss_decision
    ticket_id INTEGER,
    status TEXT NOT NULL,          -- pending_approval | approved | rejected | paid | draft | on_hold | superseded | logged
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,      -- real clock, for ordering the board
    desk_date TEXT NOT NULL,       -- desk.date_today when posted (rule 1)
    title TEXT NOT NULL,
    details TEXT NOT NULL,         -- JSON
    amount REAL,
    ref_kind TEXT,                 -- invoice | rent (payment requests)
    ref_id INTEGER,
    account TEXT,
    decided_by TEXT,
    decided_at TEXT,
    decision_note TEXT,
    payment_id INTEGER,
    FOREIGN KEY (ticket_id) REFERENCES tickets(id)
)
"""


# ---------------------------------------------------------------- connections + shared helpers


def _require_db() -> None:
    if not DB_PATH.exists():
        raise ToolError(
            f"Working database not found at data/{DB_PATH.name}. "
            "Run refresh_working_copy (or copy data/campus_customs.db to data/campus_customs_new.db) first."
        )


def _connect() -> sqlite3.Connection:
    """Open the working copy read-only, so read tools can never change it."""
    _require_db()
    con = sqlite3.connect(f"{DB_PATH.as_uri()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def _connect_rw() -> sqlite3.Connection:
    """Open the working copy for writing. Callers wrap changes in `with con:` so they commit or roll back together."""
    _require_db()
    con = sqlite3.connect(f"{DB_PATH.as_uri()}?mode=rw", uri=True)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute(BOARD_SCHEMA)
    return con


def _today(con: sqlite3.Connection) -> date:
    """The shop's 'today' (rule 1): desk.date_today, not the computer clock."""
    row = con.execute("SELECT date_today FROM desk").fetchone()
    if row is None:
        raise ToolError("desk.date_today is missing, so 'today' is unknown. Refresh the working database.")
    return date.fromisoformat(row["date_today"])


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _cash_check(con: sqlite3.Connection, amount: float) -> list[dict]:
    """Whether each cash account could pay `amount` (rule 6). Read-only: nothing is paid here."""
    return [
        {
            "account": r["name"],
            "balance": r["balance"],
            "covers": r["balance"] > 0 and r["balance"] >= amount,
            "balance_after": round(r["balance"] - amount, 2),
        }
        for r in con.execute("SELECT name, balance FROM cash_accounts ORDER BY name")
    ]


def _has_board(con: sqlite3.Connection) -> bool:
    return con.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'board'").fetchone() is not None


def _board_item(row: sqlite3.Row) -> dict:
    item = dict(row)
    item["details"] = json.loads(item["details"])
    return {k: v for k, v in item.items() if v is not None}


def _ticket(con: sqlite3.Connection, ticket_id: int) -> sqlite3.Row:
    row = con.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
    if row is None:
        ids = ", ".join(str(r["id"]) for r in con.execute("SELECT id FROM tickets ORDER BY id"))
        raise ToolError(f"No ticket {ticket_id}. Tickets on file: {ids}.")
    return row


def _pending_payment_total(con: sqlite3.Connection, account: str) -> float:
    if not _has_board(con):
        return 0.0
    row = con.execute(
        "SELECT COALESCE(SUM(amount), 0) AS total FROM board "
        "WHERE kind = 'payment_request' AND account = ? AND status IN ('pending_approval', 'approved')",
        (account,),
    ).fetchone()
    return round(row["total"], 2)


def _pick_account(con: sqlite3.Connection, account: str | None) -> sqlite3.Row:
    rows = con.execute("SELECT * FROM cash_accounts ORDER BY name").fetchall()
    if account is None:
        if len(rows) != 1:
            raise ToolError(f"Say which account to use: {', '.join(r['name'] for r in rows)}.")
        return rows[0]
    match = next((r for r in rows if r["name"] == account), None)
    if match is None:
        raise ToolError(f"No cash account {account!r}. Accounts: {', '.join(r['name'] for r in rows)}.")
    return match


def _post(con: sqlite3.Connection, *, kind: str, ticket_id: int | None, status: str, created_by: str,
          title: str, details: dict, amount: float | None = None, ref_kind: str | None = None,
          ref_id: int | None = None, account: str | None = None) -> int:
    cur = con.execute(
        "INSERT INTO board (kind, ticket_id, status, created_by, created_at, desk_date, title, details, "
        "amount, ref_kind, ref_id, account) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (kind, ticket_id, status, created_by, _now(), _today(con).isoformat(), title,
         json.dumps(details), amount, ref_kind, ref_id, account),
    )
    return cur.lastrowid


def _get_board_row(con: sqlite3.Connection, board_id: int) -> sqlite3.Row:
    row = con.execute("SELECT * FROM board WHERE id = ?", (board_id,)).fetchone() if _has_board(con) else None
    if row is None:
        raise ToolError(f"No board item {board_id}.")
    return row


def _is_human(name: str) -> bool:
    cleaned = name.strip().lower()
    return len(cleaned) >= 2 and cleaned not in AGENT_NAMES and "agent" not in cleaned


def _add_month(d: date) -> date:
    year, month = (d.year + 1, 1) if d.month == 12 else (d.year, d.month + 1)
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------------------------------------------------------------- read tools


@mcp.tool(annotations=READ_ONLY, tags={"read"})
def list_open_tickets() -> dict:
    """List every ticket that isn't resolved, oldest first, with the ids it links to.

    Reads tickets and desk. The Boss uses this to see all the work before
    deciding who handles what.
    """
    with closing(_connect()) as con:
        today = _today(con)
        rows = con.execute(
            "SELECT * FROM tickets WHERE status != 'resolved' ORDER BY created_at, id"
        ).fetchall()
    return {"today": today.isoformat(), "count": len(rows), "tickets": [dict(r) for r in rows]}


@mcp.tool(annotations=READ_ONLY, tags={"read"})
def list_tickets() -> dict:
    """List every ticket, open or resolved, with a simple open/resolved state (for the dashboard).

    Reads tickets and desk. `status` is the raw ticket status; `state` is
    'resolved' only when status is 'resolved', otherwise 'open'.
    """
    with closing(_connect()) as con:
        today = _today(con)
        rows = con.execute("SELECT * FROM tickets ORDER BY id").fetchall()
    tickets = [{**dict(r), "state": "resolved" if r["status"] == "resolved" else "open"} for r in rows]
    return {"today": today.isoformat(), "count": len(tickets),
            "open": sum(t["state"] == "open" for t in tickets), "tickets": tickets}


@mcp.tool(annotations=READ_ONLY, tags={"read"})
def get_ticket(
    ticket_id: Annotated[int, Field(description="Ticket id, e.g. 101")],
) -> dict:
    """Get one ticket with every record it links to.

    Reads tickets, invoices, vendors, leases, inventory, pricing, desk and
    board. Adds the linked invoice (with its vendor and days overdue), the
    linked lease (with days until due), the stock row and price for the
    ticket's SKU and size, and anything already on the board for the ticket.
    """
    with closing(_connect()) as con:
        today = _today(con)
        t = _ticket(con, ticket_id)
        out: dict = {"today": today.isoformat(), "ticket": dict(t)}
        if t["invoice_id"] is not None:
            inv = con.execute(
                "SELECT i.*, v.name AS vendor_name, v.specialty, v.lead_days "
                "FROM invoices i JOIN vendors v ON v.id = i.vendor_id WHERE i.id = ?",
                (t["invoice_id"],),
            ).fetchone()
            if inv:
                days_late = (today - date.fromisoformat(inv["due_date"])).days
                out["invoice"] = {**dict(inv), "overdue": inv["status"] == "open" and days_late > 0,
                                  "days_overdue": max(0, days_late) if inv["status"] == "open" else 0}
        if t["lease_id"] is not None:
            lease = con.execute("SELECT * FROM leases WHERE id = ?", (t["lease_id"],)).fetchone()
            if lease:
                out["lease"] = {**dict(lease),
                                "days_until_due": (date.fromisoformat(lease["next_due"]) - today).days}
        if t["sku"]:
            stock = con.execute(
                "SELECT name, size, qty, location FROM inventory WHERE sku = ? AND size = ?", (t["sku"], t["size"])
            ).fetchone()
            price = con.execute("SELECT unit_cost, list_price FROM pricing WHERE sku = ?", (t["sku"],)).fetchone()
            out["stock"] = dict(stock) if stock else None
            out["pricing"] = dict(price) if price else None
        out["board"] = (
            [_board_item(r) for r in con.execute("SELECT * FROM board WHERE ticket_id = ? ORDER BY id", (ticket_id,))]
            if _has_board(con) else []
        )
    return out


@mcp.tool(annotations=READ_ONLY, tags={"read"})
def check_stock(
    sku: Annotated[str, Field(description="Product SKU, e.g. CC-TEE-WHITE")],
    size: Annotated[str, Field(description="Size as stored in inventory: S, M, L, XL, or OS for one-size items")],
    qty_needed: Annotated[int, Field(ge=1, description="How many the ticket asks for")] = 1,
) -> dict:
    """Check stock and price for one product size against what a ticket needs.

    Reads inventory and pricing. Returns units on hand, how many short,
    the aisle it sits in, other sizes that are in stock, and the unit cost
    and list price so any discount can be checked against cost. Prices are
    for quoting only: a sale never adds money to cash_accounts (rule 7).
    """
    sku, size = sku.strip().upper(), size.strip().upper()
    with closing(_connect()) as con:
        rows = con.execute(
            "SELECT name, size, qty, location FROM inventory WHERE sku = ? ORDER BY rowid", (sku,)
        ).fetchall()
        if not rows:
            raise ToolError(f"No product with SKU {sku!r} in inventory.")
        match = next((r for r in rows if r["size"] == size), None)
        if match is None:
            sizes = ", ".join(r["size"] for r in rows)
            raise ToolError(f"{sku} has no size {size!r}. Sizes on file: {sizes}.")
        price = con.execute(
            "SELECT unit_cost, list_price FROM pricing WHERE sku = ?", (sku,)
        ).fetchone()

    short_by = max(0, qty_needed - match["qty"])
    result = {
        "sku": sku,
        "name": match["name"],
        "size": size,
        "location": match["location"],
        "on_hand": match["qty"],
        "qty_needed": qty_needed,
        "short_by": short_by,
        "can_fill_from_stock": short_by == 0,
        "other_sizes_in_stock": {r["size"]: r["qty"] for r in rows if r["size"] != size and r["qty"] > 0},
        "pricing": None,  # stays None if the SKU has no pricing row; never guessed
    }
    if price:
        cost, list_price = price["unit_cost"], price["list_price"]
        result["pricing"] = {
            "unit_cost": cost,
            "list_price": list_price,
            "margin_per_unit": round(list_price - cost, 2),
            "quote_at_list": round(list_price * qty_needed, 2),
            "cost_floor": round(cost * qty_needed, 2),
            "note": "Quote only. Cash only goes out; a sale never adds to cash_accounts.",
        }
    return result


@mcp.tool(annotations=READ_ONLY, tags={"read"})
def check_vendor_status(
    vendor_id: Annotated[int | None, Field(description="Vendor id; leave empty to see every vendor")] = None,
) -> dict:
    """Check whether a vendor can ship, what the shop owes them, and their lead time.

    Reads vendors, invoices, desk and cash_accounts. A vendor with ANY open
    invoice cannot ship, even if the invoice isn't due yet (rule 3). An
    invoice is overdue when its due_date is before desk.date_today (rule 1).
    Delivery dates use vendors.lead_days (rule 2). Each open invoice shows
    whether cash could cover it (rule 6), but paying needs human approval
    (rule 4) and this tool pays nothing. The data has no product-to-vendor
    link, so match products to vendors by `specialty`.
    """
    with closing(_connect()) as con:
        today = _today(con)
        if vendor_id is None:
            vendors = con.execute("SELECT * FROM vendors ORDER BY id").fetchall()
        else:
            vendors = con.execute("SELECT * FROM vendors WHERE id = ?", (vendor_id,)).fetchall()
            if not vendors:
                raise ToolError(f"No vendor with id {vendor_id}.")

        report = []
        for v in vendors:
            open_invoices = []
            for inv in con.execute(
                "SELECT id, amount, due_date, description FROM invoices "
                "WHERE vendor_id = ? AND status = 'open' ORDER BY due_date",
                (v["id"],),
            ).fetchall():
                days_late = (today - date.fromisoformat(inv["due_date"])).days
                open_invoices.append({
                    "invoice_id": inv["id"],
                    "amount": inv["amount"],
                    "due_date": inv["due_date"],
                    "overdue": days_late > 0,
                    "days_overdue": max(0, days_late),
                    "description": inv["description"],
                    "cash_check": _cash_check(con, inv["amount"]),
                })
            owed = round(sum(i["amount"] for i in open_invoices), 2)
            can_ship = not open_invoices
            report.append({
                "vendor_id": v["id"],
                "name": v["name"],
                "specialty": v["specialty"],
                "lead_days": v["lead_days"],
                "can_ship": can_ship,
                "blocked_reason": None if can_ship else (
                    f"{len(open_invoices)} open invoice(s) totalling ${owed:,.2f} must be paid "
                    "(with human approval) before this vendor ships"
                ),
                "open_invoices": open_invoices,
                "open_balance": owed,
                # A blocked vendor doesn't ship, so there is no arrival date until its invoices are paid.
                "arrives_by_if_ordered_today": (
                    (today + timedelta(days=v["lead_days"])).isoformat() if can_ship else None
                ),
            })

    return {"today": today.isoformat(), "vendors": report}


@mcp.tool(annotations=READ_ONLY, tags={"read"})
def check_rent_due(
    lease_id: Annotated[int, Field(description="Lease id, from tickets.lease_id")],
) -> dict:
    """Check when rent is due on a lease and whether cash covers it.

    Reads leases, desk and cash_accounts. Days until due are counted from
    desk.date_today (rule 1); a negative number means overdue. Shows whether
    each cash account could cover the rent (rule 6), but paying needs human
    approval (rule 4) and this tool pays nothing.
    """
    with closing(_connect()) as con:
        today = _today(con)
        lease = con.execute("SELECT * FROM leases WHERE id = ?", (lease_id,)).fetchone()
        if lease is None:
            raise ToolError(f"No lease with id {lease_id}.")
        cash_check = _cash_check(con, lease["monthly_rent"])

    days = (date.fromisoformat(lease["next_due"]) - today).days
    if days < 0:
        status = f"overdue by {-days} day(s)"
    elif days == 0:
        status = "due today"
    else:
        status = f"due in {days} day(s)"

    return {
        "today": today.isoformat(),
        "lease_id": lease["id"],
        "space_name": lease["space_name"],
        "landlord": lease["landlord"],
        "monthly_rent": lease["monthly_rent"],
        "next_due": lease["next_due"],
        "days_until_due": days,
        "overdue": days < 0,
        "status": status,
        "notes": lease["notes"],
        "cash_check": cash_check,
    }


@mcp.tool(annotations=READ_ONLY, tags={"read"})
def get_cash_position() -> dict:
    """The whole money picture: cash, every bill owed, requests waiting on a human, and payments made.

    Reads cash_accounts, invoices, vendors, leases, payments, board and desk.
    `cash_after_pending` is what's left if every payment request already on
    the board gets approved and paid. `cash_after_all_bills` is what's left
    if every open invoice and each lease's next rent were paid. Nothing is
    paid here, and no money ever comes in (rule 7).
    """
    with closing(_connect()) as con:
        today = _today(con)
        accounts = [dict(r) for r in con.execute("SELECT name, balance, date AS as_of FROM cash_accounts ORDER BY name")]
        invoices = []
        for r in con.execute(
            "SELECT i.id, i.amount, i.due_date, i.description, v.name AS vendor "
            "FROM invoices i JOIN vendors v ON v.id = i.vendor_id WHERE i.status = 'open' ORDER BY i.due_date"
        ):
            days_late = (today - date.fromisoformat(r["due_date"])).days
            invoices.append({"invoice_id": r["id"], "vendor": r["vendor"], "amount": r["amount"],
                             "due_date": r["due_date"], "overdue": days_late > 0,
                             "days_overdue": max(0, days_late), "description": r["description"]})
        rent = []
        for r in con.execute("SELECT * FROM leases ORDER BY next_due"):
            days = (date.fromisoformat(r["next_due"]) - today).days
            rent.append({"lease_id": r["id"], "space_name": r["space_name"], "landlord": r["landlord"],
                         "monthly_rent": r["monthly_rent"], "next_due": r["next_due"],
                         "days_until_due": days, "overdue": days < 0})
        pending = (
            [_board_item(r) for r in con.execute(
                "SELECT id, ticket_id, status, amount, ref_kind, ref_id, account, title, details FROM board "
                "WHERE kind = 'payment_request' AND status IN ('pending_approval', 'approved') ORDER BY id")]
            if _has_board(con) else []
        )
        payments = [dict(r) for r in con.execute("SELECT * FROM payments ORDER BY id")]

    cash = round(sum(a["balance"] for a in accounts), 2)
    owed_invoices = round(sum(i["amount"] for i in invoices), 2)
    owed_rent = round(sum(r["monthly_rent"] for r in rent), 2)
    pending_total = round(sum(p["amount"] for p in pending), 2)
    return {
        "today": today.isoformat(),
        "accounts": accounts,
        "open_invoices": invoices,
        "rent": rent,
        "pending_payment_requests": pending,
        "payments_made": payments,
        "totals": {
            "cash": cash,
            "open_invoices": owed_invoices,
            "next_rent": owed_rent,
            "pending_requests": pending_total,
            "cash_after_pending": round(cash - pending_total, 2),
            "cash_after_all_bills": round(cash - owed_invoices - owed_rent, 2),
        },
        "note": "Cash only goes out. Sales never add to these balances.",
    }


@mcp.tool(annotations=READ_ONLY, tags={"read"})
def check_margin(
    sku: Annotated[str, Field(description="Product SKU, e.g. CC-HOOD-NAVY")],
    qty: Annotated[int, Field(ge=1, description="How many units the quote is for")],
    unit_price: Annotated[float, Field(gt=0, description="Proposed price per unit, in dollars")],
) -> dict:
    """Check the margin on a proposed price before anyone suggests a discount.

    Reads pricing. Returns the discount off list, margin per unit and in
    total, and whether the price is below cost. A quote never adds cash
    (rule 7); a discount still needs human approval through
    request_price_override.
    """
    sku = sku.strip().upper()
    with closing(_connect()) as con:
        price = con.execute("SELECT unit_cost, list_price FROM pricing WHERE sku = ?", (sku,)).fetchone()
    if price is None:
        raise ToolError(f"No pricing row for SKU {sku!r}.")
    cost, list_price = price["unit_cost"], price["list_price"]
    return {
        "sku": sku,
        "qty": qty,
        "list_price": list_price,
        "unit_cost": cost,
        "proposed_unit_price": unit_price,
        "discount_pct": round((list_price - unit_price) / list_price * 100, 1),
        "margin_per_unit": round(unit_price - cost, 2),
        "margin_pct_of_price": round((unit_price - cost) / unit_price * 100, 1),
        "quote_total": round(unit_price * qty, 2),
        "quote_at_list": round(list_price * qty, 2),
        "cost_total": round(cost * qty, 2),
        "margin_total": round((unit_price - cost) * qty, 2),
        "below_cost": unit_price < cost,
        "note": "Quote only. A price below list needs human approval; cash only goes out.",
    }


@mcp.tool(annotations=READ_ONLY, tags={"read"})
def get_board(
    ticket_id: Annotated[int | None, Field(description="Only items for this ticket")] = None,
    kind: Annotated[
        Literal["payment_request", "price_override", "customer_draft", "vendor_draft", "boss_decision"] | None,
        Field(description="Only this kind of item"),
    ] = None,
    status: Annotated[str | None, Field(description="Only items with this status, e.g. pending_approval")] = None,
) -> dict:
    """Read the board: drafts, approval requests and Boss decisions the team has posted.

    Reads board. Returns at most the 25 newest matching items. Use it to avoid
    posting duplicates and to see whether a human approved something.
    """
    with closing(_connect()) as con:
        if not _has_board(con):
            return {"count": 0, "items": []}
        where, args = [], []
        for col, val in (("ticket_id", ticket_id), ("kind", kind), ("status", status)):
            if val is not None:
                where.append(f"{col} = ?")
                args.append(val)
        sql = "SELECT * FROM board" + (" WHERE " + " AND ".join(where) if where else "")
        rows = con.execute(sql + " ORDER BY id DESC LIMIT ?", (*args, BOARD_LIST_LIMIT)).fetchall()
    return {"count": len(rows), "items": [_board_item(r) for r in reversed(rows)]}


# ---------------------------------------------------------------- board tools (agents post; nothing is sent or paid)


@mcp.tool(annotations=BOARD_WRITE, tags={"board"})
def request_payment(
    ticket_id: Annotated[int, Field(description="The ticket this payment helps resolve")],
    kind: Annotated[Literal["invoice", "rent"], Field(description="'invoice' for a vendor bill, 'rent' for a lease")],
    ref_id: Annotated[int, Field(description="Invoice id (kind=invoice) or lease id (kind=rent)")],
    reason: Annotated[str, Field(max_length=400, description="Why this should be paid now, in one or two sentences")],
    requested_by: Annotated[Literal["Accounting"], Field(description="Only Accounting raises payment requests")] = "Accounting",
    account: Annotated[str | None, Field(description="Cash account; leave empty if the shop has only one")] = None,
) -> dict:
    """Ask a human to approve a payment. Posts a request to the board; pays nothing (rule 4).

    Reads invoices/vendors or leases, cash_accounts, desk and board; writes
    one board row. The amount always comes from the database (the invoice
    amount or the lease's monthly rent), never from the caller. Refuses a
    request that is already paid, already waiting on the board, or that cash
    can't cover after the requests already waiting (rule 6).
    """
    con = _connect_rw()
    with closing(con), con:
        today = _today(con)
        _ticket(con, ticket_id)
        if kind == "invoice":
            inv = con.execute(
                "SELECT i.*, v.name AS vendor FROM invoices i JOIN vendors v ON v.id = i.vendor_id WHERE i.id = ?",
                (ref_id,),
            ).fetchone()
            if inv is None:
                raise ToolError(f"No invoice {ref_id}.")
            if inv["status"] != "open":
                return {"status": "refused", "reason": f"Invoice #{ref_id} is already {inv['status']}; nothing to pay."}
            amount = inv["amount"]
            days_late = (today - date.fromisoformat(inv["due_date"])).days
            title = f"Pay invoice #{ref_id} to {inv['vendor']}: ${amount:,.2f}"
            details = {"vendor_id": inv["vendor_id"], "vendor": inv["vendor"], "due_date": inv["due_date"],
                       "days_overdue": max(0, days_late), "description": inv["description"]}
        else:
            lease = con.execute("SELECT * FROM leases WHERE id = ?", (ref_id,)).fetchone()
            if lease is None:
                raise ToolError(f"No lease {ref_id}.")
            amount = lease["monthly_rent"]
            title = f"Pay rent for {lease['space_name']} to {lease['landlord']}: ${amount:,.2f}"
            details = {"space_name": lease["space_name"], "landlord": lease["landlord"],
                       "next_due": lease["next_due"],
                       "days_until_due": (date.fromisoformat(lease["next_due"]) - today).days}

        acct = _pick_account(con, account)
        if _has_board(con):
            dup = con.execute(
                "SELECT id, status FROM board WHERE kind = 'payment_request' AND ref_kind = ? AND ref_id = ? "
                "AND status IN ('pending_approval', 'approved')",
                (kind, ref_id),
            ).fetchone()
            if dup:
                return {"status": "already_on_board", "board_id": dup["id"], "board_status": dup["status"],
                        "amount": amount, "note": "A request for this is already waiting on a human. Not posted again."}

        pending = _pending_payment_total(con, acct["name"])
        available = round(acct["balance"] - pending, 2)
        if acct["balance"] <= 0 or amount > available:
            return {"status": "refused",
                    "reason": (f"Cash can't cover ${amount:,.2f}: {acct['name']} has ${acct['balance']:,.2f} and "
                               f"${pending:,.2f} is already waiting on the board, leaving ${available:,.2f} (rule 6)."),
                    "amount": amount, "balance": acct["balance"], "pending": pending}

        details["reason"] = reason.strip()
        board_id = _post(con, kind="payment_request", ticket_id=ticket_id, status="pending_approval",
                         created_by=requested_by, title=title, details=details, amount=amount,
                         ref_kind=kind, ref_id=ref_id, account=acct["name"])
    return {"status": "posted", "board_id": board_id, "board_status": "pending_approval", "title": title,
            "amount": amount, "account": acct["name"], "balance": acct["balance"],
            "pending_before": pending, "available_after_this": round(available - amount, 2),
            "note": "Nothing has been paid. A human must approve, then the payment is made."}


@mcp.tool(annotations=BOARD_WRITE, tags={"board"})
def request_price_override(
    ticket_id: Annotated[int, Field(description="The price_override (or order) ticket")],
    unit_price: Annotated[float, Field(gt=0, description="Proposed discounted price per unit, in dollars")],
    reason: Annotated[str, Field(max_length=400, description="Why this price, in one or two sentences")],
    requested_by: Annotated[Literal["Accounting"], Field(description="Only Accounting proposes prices")] = "Accounting",
) -> dict:
    """Ask a human to approve a discounted price for a ticket. Posts to the board; changes no price and no cash.

    Reads tickets, pricing, inventory, desk and board; writes one board row.
    The SKU, size and quantity come from the ticket, not the caller. Refuses
    a price below unit cost, and rejects a price at or above list (that's not
    a discount). A sale never adds cash (rule 7).
    """
    con = _connect_rw()
    with closing(con), con:
        t = _ticket(con, ticket_id)
        if not t["sku"] or not t["qty"]:
            raise ToolError(f"Ticket {ticket_id} has no SKU and quantity to price.")
        price = con.execute("SELECT unit_cost, list_price FROM pricing WHERE sku = ?", (t["sku"],)).fetchone()
        if price is None:
            raise ToolError(f"No pricing row for {t['sku']}.")
        cost, list_price = price["unit_cost"], price["list_price"]
        if unit_price >= list_price:
            raise ToolError(f"${unit_price:,.2f} is not below the ${list_price:,.2f} list price, so it isn't a discount.")
        if unit_price < cost:
            return {"status": "refused",
                    "reason": f"${unit_price:,.2f} is below the ${cost:,.2f} unit cost. The shop would lose money on every unit."}
        dup = con.execute(
            "SELECT id, status, details FROM board WHERE kind = 'price_override' AND ticket_id = ? "
            "AND status IN ('pending_approval', 'approved')", (ticket_id,),
        ).fetchone()
        if dup:
            return {"status": "already_on_board", "board_id": dup["id"], "board_status": dup["status"],
                    "details": json.loads(dup["details"])}
        stock = con.execute("SELECT qty FROM inventory WHERE sku = ? AND size = ?", (t["sku"], t["size"])).fetchone()
        qty = t["qty"]
        details = {
            "sku": t["sku"], "size": t["size"], "qty": qty, "requester": t["requester"],
            "list_price": list_price, "unit_cost": cost, "unit_price": unit_price,
            "discount_pct": round((list_price - unit_price) / list_price * 100, 1),
            "margin_per_unit": round(unit_price - cost, 2),
            "quote_total": round(unit_price * qty, 2), "quote_at_list": round(list_price * qty, 2),
            "on_hand": stock["qty"] if stock else None,
            "short_by": max(0, qty - stock["qty"]) if stock else None,
            "reason": reason.strip(),
        }
        title = f"Discount for {t['requester']}: {qty} x {t['sku']} {t['size']} at ${unit_price:,.2f} (list ${list_price:,.2f})"
        board_id = _post(con, kind="price_override", ticket_id=ticket_id, status="pending_approval",
                         created_by=requested_by, title=title, details=details, amount=details["quote_total"])
    return {"status": "posted", "board_id": board_id, "board_status": "pending_approval", "title": title,
            "details": details, "note": "No price has changed. A human must approve this discount."}


INTERNAL_TERMS = re.compile(
    r"\b(invoices?|unpaid|overdue|margins?|unit cost|our cost|at cost|cash|bank|checking account|"
    r"cash flow|supplier'?s? bill|vendor'?s? bill|owe|owed|debt)\b", re.IGNORECASE)
CONTACT_INFO = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+|https?://|www\.|\b\d{3}[-.\s]\d{3}[-.\s]\d{4}\b")
CARD_LIKE = re.compile(r"\b(?:\d[ -]?){13,19}\b")
MONEY = re.compile(r"\$\s?(\d[\d,]*(?:\.\d{1,2})?)")
PERCENT = re.compile(r"(\d+(?:\.\d+)?)\s?%")
DISCOUNT_PROMISE = re.compile(
    r"discount[^.!?]{0,60}\b(approved|confirmed|locked in|guaranteed|applied)\b|"
    r"\b(approved|confirmed|locked in|guaranteed|applied)\b[^.!?]{0,60}discount", re.IGNORECASE)


def _customer_text_problems(con: sqlite3.Connection, t: sqlite3.Row, subject: str, body: str) -> list[str]:
    """Guardrails for anything a customer might read. Each problem is a reason to rewrite the draft."""
    text = f"{subject}\n{body}"
    problems = []
    if len(subject) > MAX_SUBJECT or len(body) > MAX_BODY:
        problems.append(f"Too long: subject max {MAX_SUBJECT} characters, body max {MAX_BODY}.")
    if m := INTERNAL_TERMS.search(text):
        problems.append(f"Mentions internal business details ({m.group(0)!r}). Customers never hear about bills, costs, margins or cash.")
    if CONTACT_INFO.search(text) or CARD_LIKE.search(text):
        problems.append("Contains an email address, link, phone number or card-like number. Leave contact details out.")

    approved = (
        [json.loads(r["details"]) for r in con.execute(
            "SELECT details FROM board WHERE kind = 'price_override' AND ticket_id = ? AND status = 'approved'",
            (t["id"],))]
        if _has_board(con) else []
    )
    allowed_money: set[float] = set()
    if t["sku"]:
        price = con.execute("SELECT list_price FROM pricing WHERE sku = ?", (t["sku"],)).fetchone()
        if price:
            allowed_money.add(round(price["list_price"], 2))
            if t["qty"]:
                allowed_money.add(round(price["list_price"] * t["qty"], 2))
    for d in approved:
        allowed_money.update({round(d["unit_price"], 2), round(d["quote_total"], 2)})
    for raw in MONEY.findall(text):
        value = round(float(raw.replace(",", "")), 2)
        if not any(abs(value - ok) < 0.005 for ok in allowed_money):
            problems.append(f"${raw} isn't a price the customer can be quoted. Use the list price or a human-approved price only.")
    allowed_pct = {round(d["discount_pct"]) for d in approved}
    for raw in PERCENT.findall(text):
        if round(float(raw)) not in allowed_pct:
            problems.append(f"{raw}% isn't an approved discount. Don't promise a discount a human hasn't approved.")
    if DISCOUNT_PROMISE.search(text) and not approved:
        problems.append("Says a discount is approved or confirmed, but no human has approved one.")
    return problems


@mcp.tool(annotations=BOARD_WRITE, tags={"board"})
def draft_customer_message(
    ticket_id: Annotated[int, Field(description="The customer's ticket")],
    subject: Annotated[str, Field(description=f"Subject line, max {MAX_SUBJECT} characters")],
    body: Annotated[str, Field(description=f"Message body, max {MAX_BODY} characters, signed 'Campus Customs'")],
    written_by: Annotated[Literal["Customer Service"], Field(description="Only Customer Service drafts customer messages")] = "Customer Service",
) -> dict:
    """Save a draft reply to a ticket's requester on the board. It is NEVER sent (rule 9); a human reviews it.

    Reads tickets, pricing and board; writes one board row and marks any older
    draft for the ticket as superseded. The recipient is always the ticket's
    requester. Rejects drafts that mention internal details (bills, costs,
    margins, cash), contact details, prices other than list or a
    human-approved price, or a discount no human has approved.
    """
    subject, body = subject.strip(), body.strip()
    if not subject or not body:
        raise ToolError("Subject and body are both required.")
    con = _connect_rw()
    with closing(con), con:
        t = _ticket(con, ticket_id)
        problems = _customer_text_problems(con, t, subject, body)
        if problems:
            raise ToolError("Draft not saved. Fix these and try again: " + " ".join(problems))
        con.execute("UPDATE board SET status = 'superseded' WHERE kind = 'customer_draft' AND ticket_id = ? "
                    "AND status = 'draft'", (ticket_id,))
        details = {"to": t["requester"], "subject": subject, "body": body}
        board_id = _post(con, kind="customer_draft", ticket_id=ticket_id, status="draft", created_by=written_by,
                         title=f"Draft to {t['requester']}: {subject}", details=details)
    return {"status": "posted", "board_id": board_id, "board_status": "draft", "to": t["requester"],
            "subject": subject, "body": body, "note": "Saved as a draft only. Nothing was sent."}


@mcp.tool(annotations=BOARD_WRITE, tags={"board"})
def draft_vendor_order(
    ticket_id: Annotated[int, Field(description="The ticket that needs the restock")],
    vendor_id: Annotated[int, Field(description="Vendor chosen by specialty, from check_vendor_status")],
    sku: Annotated[str, Field(description="SKU to restock")],
    size: Annotated[str, Field(description="Size to restock")],
    qty: Annotated[int, Field(ge=1, le=MAX_VENDOR_QTY, description="Units to order, normally the shortfall")],
    reason: Annotated[str, Field(max_length=400, description="Why this order, in one or two sentences")],
    written_by: Annotated[Literal["Inventory"], Field(description="Only Inventory drafts vendor orders")] = "Inventory",
) -> dict:
    """Save a draft restock order on the board. It is NEVER sent to the vendor (rule 9) and pays nothing.

    Reads vendors, invoices, inventory, pricing, tickets, desk and board;
    writes one board row. If the vendor has an open invoice the draft is put
    `on_hold`, because they won't ship until it's paid (rule 3); otherwise
    the arrival date is desk.date_today + lead_days (rule 2). Refuses to
    re-order an item an open invoice already covers.
    """
    sku, size = sku.strip().upper(), size.strip().upper()
    con = _connect_rw()
    with closing(con), con:
        today = _today(con)
        _ticket(con, ticket_id)
        vendor = con.execute("SELECT * FROM vendors WHERE id = ?", (vendor_id,)).fetchone()
        if vendor is None:
            raise ToolError(f"No vendor with id {vendor_id}.")
        if con.execute("SELECT 1 FROM inventory WHERE sku = ? AND size = ?", (sku, size)).fetchone() is None:
            raise ToolError(f"No inventory row for {sku} size {size}.")
        for inv in con.execute("SELECT id, vendor_id, description FROM invoices WHERE status = 'open'"):
            words = (inv["description"] or "").upper().split()
            if sku in words and size in words:
                return {"status": "refused",
                        "reason": (f"Open invoice #{inv['id']} ({inv['description']}) already covers {sku} {size}. "
                                   "Don't order it twice. Paying that invoice releases the shipment.")}
        dup = con.execute(
            "SELECT id, status FROM board WHERE kind = 'vendor_draft' AND ticket_id = ? AND ref_id = ? "
            "AND status IN ('draft', 'on_hold') AND json_extract(details, '$.sku') = ? AND json_extract(details, '$.size') = ?",
            (ticket_id, vendor_id, sku, size),
        ).fetchone()
        if dup:
            return {"status": "already_on_board", "board_id": dup["id"], "board_status": dup["status"]}
        blockers = [dict(r) for r in con.execute(
            "SELECT id AS invoice_id, amount, due_date FROM invoices WHERE vendor_id = ? AND status = 'open'", (vendor_id,))]
        price = con.execute("SELECT unit_cost FROM pricing WHERE sku = ?", (sku,)).fetchone()
        on_hold = bool(blockers)
        details = {
            "vendor_id": vendor_id, "vendor": vendor["name"], "specialty": vendor["specialty"],
            "lead_days": vendor["lead_days"], "sku": sku, "size": size, "qty": qty,
            "est_cost": round(price["unit_cost"] * qty, 2) if price else None,
            "est_cost_basis": "pricing.unit_cost x qty (estimate; the vendor's bill will need human approval)",
            "blocked_by_invoices": blockers,
            "arrives_by_if_sent_today": None if on_hold else (today + timedelta(days=vendor["lead_days"])).isoformat(),
            "reason": reason.strip(),
        }
        title = f"Restock {qty} x {sku} {size} from {vendor['name']}" + (" (ON HOLD: open invoice)" if on_hold else "")
        board_id = _post(con, kind="vendor_draft", ticket_id=ticket_id, status="on_hold" if on_hold else "draft",
                         created_by=written_by, title=title, details=details, ref_kind="vendor", ref_id=vendor_id)
    return {"status": "posted", "board_id": board_id, "board_status": "on_hold" if on_hold else "draft",
            "title": title, "details": details, "note": "Draft only. Nothing was sent to the vendor."}


@mcp.tool(annotations=BOARD_WRITE, tags={"board"})
def update_ticket(
    ticket_id: Annotated[int, Field(description="Ticket to update")],
    status: Annotated[Literal["open", "in_progress", "waiting_on_human", "blocked", "resolved"],
                      Field(description="New status")],
    note: Annotated[str, Field(max_length=600, description="The Boss's final call, in plain words")],
    updated_by: Annotated[Literal["Boss"], Field(description="Only the Boss sets ticket status")] = "Boss",
) -> dict:
    """Record the Boss's final call: set the ticket's status and log the decision on the board.

    Reads and writes tickets; reads board and writes one board row. A ticket
    can be 'resolved' while approvals wait: they stay on the board, and the
    decision log lists them so nothing is lost.
    """
    con = _connect_rw()
    with closing(con), con:
        t = _ticket(con, ticket_id)
        waiting = [dict(r) for r in con.execute(
            "SELECT id, kind, status FROM board WHERE ticket_id = ? AND kind IN ('payment_request', 'price_override') "
            "AND status IN ('pending_approval', 'approved')", (ticket_id,))]
        con.execute("UPDATE tickets SET status = ? WHERE id = ?", (status, ticket_id))
        board_id = _post(con, kind="boss_decision", ticket_id=ticket_id, status="logged", created_by=updated_by,
                         title=f"Ticket {ticket_id}: {t['status']} -> {status}",
                         details={"from": t["status"], "to": status, "note": note.strip(), "waiting": waiting})
    return {"status": "updated", "ticket_id": ticket_id, "from": t["status"], "to": status, "board_id": board_id,
            "still_waiting_on_a_person": waiting}


# ---------------------------------------------------------------- human-only tools (never given to an agent)


@mcp.tool(annotations=HUMAN_WRITE, tags={"human_only"})
def decide_request(
    board_id: Annotated[int, Field(description="Board id of a payment_request, price_override or vendor_draft")],
    decision: Annotated[Literal["approve", "reject"], Field(description="approve or reject")],
    decided_by: Annotated[str, Field(description="Name of the human deciding; never an agent")],
    note: Annotated[str | None, Field(max_length=400, description="Optional reason")] = None,
) -> dict:
    """HUMAN ONLY. Approve or reject a payment request, price override or purchase order (vendor draft) (rule 4).

    Reads board, vendors, invoices and cash_accounts; writes board. Approving a
    payment or purchase doesn't pay it; call pay_approved_request next. A
    purchase can only be approved once the vendor has no open invoice (rule 3)
    and its estimated cost fits in cash after requests already waiting
    (rule 6). Agents never get this tool.
    """
    if not _is_human(decided_by):
        raise ToolError("decided_by must be a person's name, not an agent.")
    con = _connect_rw()
    with closing(con), con:
        row = _get_board_row(con, board_id)
        if row["kind"] not in ("payment_request", "price_override", "vendor_draft"):
            raise ToolError(f"Board item {board_id} is a {row['kind']}, which doesn't need approval.")
        waiting = ("draft", "on_hold") if row["kind"] == "vendor_draft" else ("pending_approval",)
        if row["status"] not in waiting:
            return {"status": "refused", "reason": f"Board item {board_id} is already {row['status']}.",
                    "board_id": board_id, "kind": row["kind"], "current_status": row["status"]}
        if row["kind"] == "vendor_draft" and decision == "approve":
            blockers = [r["id"] for r in con.execute(
                "SELECT id FROM invoices WHERE vendor_id = ? AND status = 'open'", (row["ref_id"],))]
            if blockers:
                return {"status": "refused", "board_id": board_id, "kind": row["kind"], "current_status": row["status"],
                        "reason": f"The vendor still has open invoice(s) {blockers}, so it won't ship (rule 3). Pay those first."}
            est = json.loads(row["details"]).get("est_cost")
            acct = _pick_account(con, None)
            available = round(acct["balance"] - _pending_payment_total(con, acct["name"]), 2)
            if est is None or acct["balance"] <= 0 or est > available:
                return {"status": "refused", "board_id": board_id, "kind": row["kind"], "current_status": row["status"],
                        "reason": f"Cash can't cover this purchase: est. ${est or 0:,.2f}, ${available:,.2f} left after "
                                  "payment requests already waiting (rule 6)."}
        new_status = "approved" if decision == "approve" else "rejected"
        con.execute("UPDATE board SET status = ?, decided_by = ?, decided_at = ?, decision_note = ? WHERE id = ?",
                    (new_status, decided_by.strip(), _now(), note, board_id))
    return {"status": new_status, "board_id": board_id, "kind": row["kind"], "decided_by": decided_by.strip(),
            "title": row["title"]}


@mcp.tool(annotations=HUMAN_WRITE, tags={"human_only"})
def pay_approved_request(
    board_id: Annotated[int, Field(description="Board id of an approved payment_request or vendor_draft (purchase)")],
) -> dict:
    """HUMAN ONLY. Pay an approved payment request or purchase, and update every related table (rules 4-6).

    Reads desk, board, invoices or leases, vendors, pricing and cash_accounts.
    In ONE transaction it writes: a new payments row (paid_at =
    desk.date_today, approved_by = the human), then for an invoice -> 'paid',
    for rent -> the lease's next_due + 1 month, for a purchase -> nothing else
    (the goods arrive after lead_days); cash_accounts.balance minus the amount
    (date = desk.date_today); and the board item -> 'paid'. Refuses if it isn't
    approved, is already paid, the bill or price changed, the vendor is
    blocked, or cash can't cover it. Agents never get this tool.
    """
    con = _connect_rw()
    with closing(con), con:
        today = _today(con)
        req = _get_board_row(con, board_id)
        if req["kind"] not in ("payment_request", "vendor_draft"):
            raise ToolError(f"Board item {board_id} is a {req['kind']}, which isn't paid.")
        purchase = req["kind"] == "vendor_draft"
        if req["status"] == "paid":
            return {"status": "refused", "reason": f"Already paid (payment #{req['payment_id']})."}
        if req["status"] != "approved":
            return {"status": "refused", "reason": f"Status is {req['status']}. A human must approve it first (rule 4)."}
        if not req["decided_by"] or not _is_human(req["decided_by"]):
            return {"status": "refused", "reason": "No human approval on record (rule 4)."}

        details = json.loads(req["details"])
        if purchase:
            if con.execute("SELECT 1 FROM invoices WHERE vendor_id = ? AND status = 'open'", (req["ref_id"],)).fetchone():
                return {"status": "refused", "reason": "The vendor has an open invoice again, so it won't ship (rule 3)."}
            price = con.execute("SELECT unit_cost FROM pricing WHERE sku = ?", (details["sku"],)).fetchone()
            amount = round(price["unit_cost"] * details["qty"], 2) if price else None
            if amount is None or details.get("est_cost") is None or abs(amount - details["est_cost"]) > 0.005:
                return {"status": "refused", "reason": "The unit cost changed since approval. Ask for a new approval."}
            account = _pick_account(con, None)["name"]
        else:
            amount, account = req["amount"], req["account"]
        if amount is None or amount <= 0:
            return {"status": "refused", "reason": "Payment amount must be positive. Cash only goes out (rule 7)."}
        if not purchase and req["ref_kind"] == "invoice":
            inv = con.execute("SELECT * FROM invoices WHERE id = ?", (req["ref_id"],)).fetchone()
            if inv is None or inv["status"] != "open":
                return {"status": "refused", "reason": f"Invoice #{req['ref_id']} is no longer open. Nothing to pay."}
            if abs(inv["amount"] - amount) > 0.005:
                return {"status": "refused", "reason": "The invoice amount changed since approval. Ask for a new approval."}
        elif not purchase:
            lease = con.execute("SELECT * FROM leases WHERE id = ?", (req["ref_id"],)).fetchone()
            if lease is None:
                return {"status": "refused", "reason": f"Lease {req['ref_id']} not found."}
            if abs(lease["monthly_rent"] - amount) > 0.005:
                return {"status": "refused", "reason": "The rent changed since approval. Ask for a new approval."}

        acct = con.execute("SELECT * FROM cash_accounts WHERE name = ?", (account,)).fetchone()
        if acct is None:
            return {"status": "refused", "reason": f"Cash account {account!r} not found."}
        if acct["balance"] <= 0 or amount > acct["balance"]:
            return {"status": "refused",
                    "reason": f"Cash can't cover it: {acct['name']} has ${acct['balance']:,.2f}, payment is ${amount:,.2f} (rule 6)."}

        kind, ref_id = ("purchase", board_id) if purchase else (req["ref_kind"], req["ref_id"])
        cur = con.execute(
            "INSERT INTO payments (kind, ref_id, amount, account, paid_at, approved_by) VALUES (?, ?, ?, ?, ?, ?)",
            (kind, ref_id, amount, acct["name"], today.isoformat(), req["decided_by"]),
        )
        payment_id = cur.lastrowid
        if purchase:
            lead = con.execute("SELECT lead_days FROM vendors WHERE id = ?", (req["ref_id"],)).fetchone()["lead_days"]
            ref_after = {"purchase_board_id": board_id, "vendor_id": req["ref_id"], "sku": details["sku"],
                         "size": details["size"], "qty": details["qty"],
                         "arrives_by": (today + timedelta(days=lead)).isoformat(),
                         "note": "Inventory isn't changed until the goods arrive."}
        elif req["ref_kind"] == "invoice":
            con.execute("UPDATE invoices SET status = 'paid' WHERE id = ?", (req["ref_id"],))
            ref_after = {"invoice_id": req["ref_id"], "status": "paid"}
        else:
            next_due = _add_month(date.fromisoformat(lease["next_due"])).isoformat()
            con.execute("UPDATE leases SET next_due = ? WHERE id = ?", (next_due, req["ref_id"]))
            ref_after = {"lease_id": req["ref_id"], "next_due_before": lease["next_due"], "next_due": next_due}
        new_balance = round(acct["balance"] - amount, 2)
        con.execute("UPDATE cash_accounts SET balance = ?, date = ? WHERE name = ?",
                    (new_balance, today.isoformat(), acct["name"]))
        con.execute("UPDATE board SET status = 'paid', payment_id = ?, amount = ? WHERE id = ?",
                    (payment_id, amount, board_id))

    ref_table = "purchase" if purchase else ("invoices" if req["ref_kind"] == "invoice" else "leases")
    return {
        "status": "paid",
        "payment": {"id": payment_id, "kind": kind, "ref_id": ref_id, "amount": amount,
                    "account": acct["name"], "paid_at": today.isoformat(), "approved_by": req["decided_by"]},
        "updated": {"payments": "new row", ref_table: ref_after,
                    "cash_accounts": {"account": acct["name"], "before": acct["balance"], "after": new_balance,
                                      "date": today.isoformat()},
                    "board": {"id": board_id, "status": "paid"}},
    }


@mcp.tool(annotations=HUMAN_WRITE, tags={"human_only"})
def refresh_working_copy() -> dict:
    """HUMAN / RUNNER ONLY. Replace the working copy with the original database before a run (rule 8).

    Reads data/campus_customs.db (never writes it) and overwrites
    data/campus_customs_new.db with an exact copy, which also clears the
    board, payments and every change from earlier runs. Agents never get this tool.
    """
    if not ORIGINAL_DB_PATH.exists():
        raise ToolError("Original database data/campus_customs.db is missing; can't refresh.")
    for suffix in ("-wal", "-shm", "-journal"):
        Path(f"{DB_PATH}{suffix}").unlink(missing_ok=True)
    shutil.copyfile(ORIGINAL_DB_PATH, DB_PATH)
    original, working = _sha256(ORIGINAL_DB_PATH), _sha256(DB_PATH)
    with closing(_connect()) as con:
        today = _today(con)
        open_tickets = con.execute("SELECT COUNT(*) FROM tickets WHERE status != 'resolved'").fetchone()[0]
    return {"status": "refreshed", "identical_to_original": original == working, "sha256": working,
            "desk_date": today.isoformat(), "open_tickets": open_tickets}


if __name__ == "__main__":
    mcp.run(show_banner=False)
