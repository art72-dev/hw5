"""Tests for every campus-customs MCP tool, its guardrails and the shop rules.

Runs against a throwaway copy of the database in a temp folder, so the real
working copy is never touched. No model calls.

From hw5/:  .venv/bin/python backend/tests/test_mcp_tools.py
"""

import asyncio
import shutil
import sqlite3
import sys
import tempfile
import warnings
from datetime import date
from pathlib import Path

warnings.filterwarnings("ignore")
HW = Path(__file__).resolve().parents[2]  # hw5/ (this file is in backend/tests/)
sys.path.insert(0, str(HW / "mcp_server"))

import server  # noqa: E402
from fastmcp import Client  # noqa: E402

results: list[tuple[str, bool]] = []


def check(name: str, ok: bool) -> None:
    results.append((name, ok))
    print(("PASS  " if ok else "FAIL  ") + name)


async def call(c: Client, tool: str, args: dict | None = None):
    return (await c.call_tool(tool, args or {})).structured_content


async def error(c: Client, tool: str, args: dict) -> str:
    r = await c.call_tool(tool, args, raise_on_error=False)
    return r.content[0].text if r.is_error else ""


def db(sql: str, args=()):
    with sqlite3.connect(server.DB_PATH) as con:
        con.row_factory = sqlite3.Row
        return [dict(r) for r in con.execute(sql, args)]


async def main(tmp: Path) -> None:
    shutil.copy(HW / "data" / "campus_customs.db", tmp / "test.db")
    server.DB_PATH = tmp / "test.db"

    async with Client(server.mcp) as c:
        tools = {t.name: t for t in await c.list_tools()}
        read = {n for n, t in tools.items() if "read" in (t.meta or {}).get("fastmcp", {}).get("tags", [])}
        check("17 tools on the server", len(tools) == 17)
        check("every read tool is marked read-only", all(tools[n].annotations.read_only_hint for n in read) and len(read) == 9)

        # ---- read tools
        t = await call(c, "list_open_tickets")
        check("list_open_tickets: 3 open tickets, today = desk date", t["count"] == 3 and t["today"] == "2026-08-31")
        g = await call(c, "get_ticket", {"ticket_id": 101})
        check("get_ticket 101: invoice 501 open, 3 days overdue, vendor Bulldog; tee S stock 0",
              g["invoice"]["days_overdue"] == 3 and g["invoice"]["vendor_name"] == "Bulldog Print Co" and g["stock"]["qty"] == 0)
        g = await call(c, "get_ticket", {"ticket_id": 102})
        check("get_ticket 102: lease 1 due in 2 days", g["lease"]["days_until_due"] == 2)
        check("get_ticket unknown id lists real ids", "Tickets on file: 101, 102, 103" in await error(c, "get_ticket", {"ticket_id": 999}))
        cp = await call(c, "get_cash_position")
        check("get_cash_position: cash 3400, invoices 840, rent 2400, 160 after all bills",
              cp["totals"] == {"cash": 3400.0, "open_invoices": 840.0, "next_rent": 2400.0, "pending_requests": 0,
                               "cash_after_pending": 3400.0, "cash_after_all_bills": 160.0})
        m = await call(c, "check_margin", {"sku": "CC-HOOD-NAVY", "qty": 20, "unit_price": 49.3})
        check("check_margin: $49.30 hoodie = 15% off, $27.30 margin, $986 quote", (m["discount_pct"], m["margin_per_unit"], m["quote_total"]) == (15.0, 27.3, 986.0))
        m = await call(c, "check_margin", {"sku": "CC-HOOD-NAVY", "qty": 20, "unit_price": 20})
        check("check_margin: $20 hoodie flagged below cost", m["below_cost"] is True)
        check("get_board before anything is posted: empty", (await call(c, "get_board"))["count"] == 0)

        # ---- request_payment (rules 4, 6)
        p = await call(c, "request_payment", {"ticket_id": 101, "kind": "invoice", "ref_id": 501, "reason": "Releases the tee reprint."})
        check("request_payment invoice 501: posted, amount 840 from the database, pending approval",
              p["status"] == "posted" and p["amount"] == 840.0 and p["board_status"] == "pending_approval")
        inv_req = p["board_id"]
        p2 = await call(c, "request_payment", {"ticket_id": 103, "kind": "invoice", "ref_id": 501, "reason": "dup"})
        check("request_payment duplicate: already_on_board, same board id", p2["status"] == "already_on_board" and p2["board_id"] == inv_req)
        r = await call(c, "request_payment", {"ticket_id": 102, "kind": "rent", "ref_id": 1, "reason": "Due in 2 days."})
        check("request_payment rent: posted 2400, 160 left after both", r["amount"] == 2400.0 and r["available_after_this"] == 160.0)
        rent_req = r["board_id"]
        check("nothing paid yet: payments empty, invoice open, cash 3400",
              not db("SELECT * FROM payments") and db("SELECT status FROM invoices")[0]["status"] == "open"
              and db("SELECT balance FROM cash_accounts")[0]["balance"] == 3400.0)
        with sqlite3.connect(server.DB_PATH) as con:
            con.execute("INSERT INTO invoices VALUES (900, 2, 500.0, '2026-09-10', 'open', 'test mugs')")
        x = await call(c, "request_payment", {"ticket_id": 101, "kind": "invoice", "ref_id": 900, "reason": "test"})
        check("rule 6 early: $500 request refused, only $160 left after waiting requests", x["status"] == "refused" and "160.00" in x["reason"])

        # ---- request_price_override (rule 7, cost floor)
        o = await call(c, "request_price_override", {"ticket_id": 103, "unit_price": 20, "reason": "test"})
        check("price override below $22 cost: refused", o["status"] == "refused")
        check("price override at/above list: rejected as not a discount",
              "isn't a discount" in await error(c, "request_price_override", {"ticket_id": 103, "unit_price": 58, "reason": "x"}))
        o = await call(c, "request_price_override", {"ticket_id": 103, "unit_price": 49.3, "reason": "Bulk order of 20."})
        check("price override $49.30: posted, qty 20 from the ticket, 15% off, short 12",
              o["status"] == "posted" and o["details"]["qty"] == 20 and o["details"]["discount_pct"] == 15.0 and o["details"]["short_by"] == 12)
        override_req = o["board_id"]

        # ---- draft_vendor_order (rules 2, 3, no double order)
        v = await call(c, "draft_vendor_order", {"ticket_id": 101, "vendor_id": 1, "sku": "CC-TEE-WHITE", "size": "S", "qty": 1, "reason": "x"})
        check("vendor order for tee S refused: invoice 501 already covers it", v["status"] == "refused" and "#501" in v["reason"])
        v = await call(c, "draft_vendor_order", {"ticket_id": 103, "vendor_id": 1, "sku": "CC-HOOD-NAVY", "size": "M", "qty": 12, "reason": "Short 12."})
        check("vendor order 12 hoodies: on_hold (blocked by 501), no arrival date, est cost $264",
              v["board_status"] == "on_hold" and v["details"]["blocked_by_invoices"][0]["invoice_id"] == 501
              and v["details"]["arrives_by_if_sent_today"] is None and v["details"]["est_cost"] == 264.0)
        v2 = await call(c, "draft_vendor_order", {"ticket_id": 103, "vendor_id": 1, "sku": "CC-HOOD-NAVY", "size": "M", "qty": 12, "reason": "dup"})
        check("vendor order duplicate: already_on_board", v2["status"] == "already_on_board")
        v3 = await call(c, "draft_vendor_order", {"ticket_id": 101, "vendor_id": 2, "sku": "CC-MUG-CREST", "size": "OS", "qty": 10, "reason": "x"})
        check("rule 3 even when not overdue: Elm City Gifts' open test invoice #900 (due 9/10) puts the mug order on_hold",
              v3["board_status"] == "on_hold" and v3["details"]["blocked_by_invoices"][0]["invoice_id"] == 900)

        # ---- draft_customer_message guardrails (rule 9 + customer comms)
        bad = {
            "internal term": "Sorry, our invoice with the printer is unpaid.",
            "unapproved price": "We can do $49.30 each for the club.",
            "unapproved percent": "Good news: 15% off for the club!",
            "discount promise": "Your discount is approved and the hoodies are yours.",
            "contact info": "Email us at shop@campuscustoms.com for more.",
        }
        for label, body in bad.items():
            e = await error(c, "draft_customer_message", {"ticket_id": 103, "subject": "Your hoodies", "body": body})
            check(f"customer draft blocked: {label}", e.startswith("Draft not saved") or "Draft not saved" in e)
        d = await call(c, "draft_customer_message", {"ticket_id": 103, "subject": "Your hoodie order",
                                                     "body": "Hi Yale AI Club! We have 8 in size M now at $58 each and are restocking the rest. "
                                                             "Your bulk request is being reviewed and we'll follow up soon. - Campus Customs"})
        check("customer draft ok: list price allowed, recipient = ticket requester, status draft",
              d["status"] == "posted" and d["to"] == "Yale AI Club" and d["board_status"] == "draft")
        d2 = await call(c, "draft_customer_message", {"ticket_id": 103, "subject": "Update", "body": "Hi again! Still reviewing. - Campus Customs"})
        check("newer draft supersedes the older one",
              db("SELECT status FROM board WHERE id = ?", (d["board_id"],))[0]["status"] == "superseded" and d2["board_status"] == "draft")

        # ---- update_ticket
        u = await call(c, "update_ticket", {"ticket_id": 103, "status": "resolved", "note": "Team done; discount waits on a person."})
        check("update_ticket resolved: tickets.status updated, and the approvals still waiting are listed (they stay on the board)",
              u["status"] == "updated" and db("SELECT status FROM tickets WHERE id = 103")[0]["status"] == "resolved"
              and {w["kind"] for w in u["still_waiting_on_a_person"]} == {"price_override"})
        check("resolved ticket drops out of list_open_tickets", 103 not in [x["id"] for x in (await call(c, "list_open_tickets"))["tickets"]])

        # ---- human-only: approval + payment (rules 4, 5, 6, 7)
        check("decide_request refuses an agent as approver",
              "not an agent" in await error(c, "decide_request", {"board_id": inv_req, "decision": "approve", "decided_by": "Accounting"}))
        n = await call(c, "pay_approved_request", {"board_id": inv_req})
        check("rule 4: pay before approval refused", n["status"] == "refused" and "approve" in n["reason"])
        await call(c, "decide_request", {"board_id": inv_req, "decision": "approve", "decided_by": "Anjali (owner)"})
        paid = await call(c, "pay_approved_request", {"board_id": inv_req})
        pay_row = db("SELECT * FROM payments")
        check("rule 5: payment row (invoice 501, $840, checking, paid_at = desk date, approved_by human)",
              paid["status"] == "paid" and pay_row == [{"id": 1, "kind": "invoice", "ref_id": 501, "amount": 840.0, "account": "checking",
                                                       "paid_at": "2026-08-31", "approved_by": "Anjali (owner)"}])
        check("rule 5: invoice 501 -> paid", db("SELECT status FROM invoices WHERE id = 501")[0]["status"] == "paid")
        check("rule 5: cash 3400 -> 2560, dated desk date", db("SELECT balance, date FROM cash_accounts")[0] == {"balance": 2560.0, "date": "2026-08-31"})
        check("rule 5: board request -> paid, linked to payment 1",
              db("SELECT status, payment_id FROM board WHERE id = ?", (inv_req,))[0] == {"status": "paid", "payment_id": 1})
        again = await call(c, "pay_approved_request", {"board_id": inv_req})
        check("no double payment: paying again refused", again["status"] == "refused" and "Already paid" in again["reason"])
        vs = await call(c, "check_vendor_status", {"vendor_id": 1})
        check("rule 3 cleared: Bulldog can ship after payment, arrives desk date + 5 = 2026-09-05",
              vs["vendors"][0]["can_ship"] and vs["vendors"][0]["arrives_by_if_ordered_today"] == "2026-09-05")

        await call(c, "decide_request", {"board_id": rent_req, "decision": "approve", "decided_by": "Anjali (owner)"})
        rp = await call(c, "pay_approved_request", {"board_id": rent_req})
        check("rule 5: rent paid -> lease next_due 2026-09-02 -> 2026-10-02, cash 2560 -> 160",
              rp["status"] == "paid" and db("SELECT next_due FROM leases")[0]["next_due"] == "2026-10-02"
              and db("SELECT balance FROM cash_accounts")[0]["balance"] == 160.0)

        with sqlite3.connect(server.DB_PATH) as con:  # a stale approval for a bill cash can no longer cover
            con.execute("INSERT INTO board (kind, ticket_id, status, created_by, created_at, desk_date, title, details, amount, "
                        "ref_kind, ref_id, account, decided_by) VALUES ('payment_request', 101, 'approved', 'Accounting', 'now', "
                        "'2026-08-31', 'test', '{}', 500.0, 'invoice', 900, 'checking', 'Anjali (owner)')")
            stale = con.execute("SELECT MAX(id) FROM board").fetchone()[0]
        s = await call(c, "pay_approved_request", {"board_id": stale})
        check("rule 6 at pay time: $500 refused with $160 in checking; nothing changed",
              s["status"] == "refused" and "can't cover" in s["reason"] and db("SELECT balance FROM cash_accounts")[0]["balance"] == 160.0
              and len(db("SELECT * FROM payments")) == 2)
        check("rule 7: cash only went down (3400 -> 2560 -> 160)", [p["amount"] for p in db("SELECT amount FROM payments")] == [840.0, 2400.0])

        await call(c, "decide_request", {"board_id": override_req, "decision": "approve", "decided_by": "Anjali (owner)"})
        ok = await call(c, "draft_customer_message", {"ticket_id": 103, "subject": "Your bulk price",
                                                      "body": "Hi Yale AI Club! Your bulk price is $49.30 per hoodie, $986 for 20. - Campus Customs"})
        check("approved price can be quoted to the customer once a human approves it", ok["status"] == "posted")

        # ---- refresh (rule 8)
        fresh = await call(c, "refresh_working_copy")
        check("rule 8: refresh_working_copy -> identical to original; board, payments and changes gone",
              fresh["identical_to_original"] and fresh["open_tickets"] == 3
              and db("SELECT balance FROM cash_accounts")[0]["balance"] == 3400.0 and not db("SELECT * FROM payments")
              and not db("SELECT name FROM sqlite_master WHERE name = 'board'"))

        # ---- purchases: approve + pay a restock (after refresh: cash $3,400, #501 open)
        lt = await call(c, "list_tickets")
        check("list_tickets: all 3 tickets with state open", lt["count"] == 3 and lt["open"] == 3)
        po = await call(c, "draft_vendor_order", {"ticket_id": 103, "vendor_id": 1, "sku": "CC-HOOD-NAVY", "size": "M", "qty": 12, "reason": "Short 12."})
        held = await call(c, "decide_request", {"board_id": po["board_id"], "decision": "approve", "decided_by": "Anjali (owner)"})
        check("purchase approval refused while invoice #501 blocks the vendor (rule 3)", held["status"] == "refused" and "501" in held["reason"])
        req = await call(c, "request_payment", {"ticket_id": 101, "kind": "invoice", "ref_id": 501, "reason": "Unblock Bulldog."})
        await call(c, "decide_request", {"board_id": req["board_id"], "decision": "approve", "decided_by": "Anjali (owner)"})
        await call(c, "pay_approved_request", {"board_id": req["board_id"]})
        ok = await call(c, "decide_request", {"board_id": po["board_id"], "decision": "approve", "decided_by": "Anjali (owner)"})
        bought = await call(c, "pay_approved_request", {"board_id": po["board_id"]})
        check("purchase approved once #501 is paid, then paid: payments kind 'purchase' $264, cash 2560 -> 2296, arrives 2026-09-05",
              ok["status"] == "approved" and bought["status"] == "paid" and bought["payment"]["kind"] == "purchase"
              and bought["payment"]["amount"] == 264.0 and db("SELECT balance FROM cash_accounts")[0]["balance"] == 2296.0
              and bought["updated"]["purchase"]["arrives_by"] == "2026-09-05")
        check("purchase doesn't touch inventory until the goods arrive (still 8 hoodies in M)",
              db("SELECT qty FROM inventory WHERE sku = 'CC-HOOD-NAVY' AND size = 'M'")[0]["qty"] == 8)
        await call(c, "refresh_working_copy")

    check("month math: Jan 31 + 1 month = Feb 28; Dec 15 -> Jan 15",
          server._add_month(date(2026, 1, 31)) == date(2026, 2, 28) and server._add_month(date(2026, 12, 15)) == date(2027, 1, 15))
    passed = sum(ok for _, ok in results)
    print(f"\n{passed}/{len(results)} passed")
    sys.exit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as d:
        asyncio.run(main(Path(d)))
