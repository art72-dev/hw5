"""Offline end-to-end test of the agent team: real runner, real MCP server (stdio), real audit trail, no API calls.

A scripted FunctionModel plays all five agents, so the whole loop runs:
refresh -> Boss triage -> per ticket Boss -> ask_* -> specialists -> MCP tools
-> output validators -> update_ticket -> BossDecision. It also forces three
mistakes to prove the guardrails catch them:
  * Boss tries to finish ticket 102 without update_ticket  -> output validator retry
  * Customer Service quotes an unapproved price on 103      -> MCP draft guardrail retry
  * Customer Service reports a made-up board id on 101       -> output validator retry

The audit goes to a temp file (not output/audit_trail.json). The working
database is refreshed again at the end so it matches the original.

From hw5/:  .venv/bin/python backend/tests/test_team_offline.py
"""

import asyncio
import os
import json
import sqlite3
import sys
import tempfile
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
HW = Path(__file__).resolve().parents[2]  # hw5/ (this file is in backend/tests/)
WORK_DIR = Path(tempfile.mkdtemp(prefix="campus_customs_"))  # never the real working copy
os.environ["CAMPUS_CUSTOMS_WORK_DIR"] = str(WORK_DIR)
sys.path.insert(0, str(HW / "backend"))

from pydantic_ai.messages import (  # noqa: E402
    ModelMessage,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel  # noqa: E402

from agents.base import mcp_client  # noqa: E402
from audit import AuditTrail  # noqa: E402
from team import run_team  # noqa: E402

results: list[tuple[str, bool]] = []
seen_retries: list[str] = []


def check(name: str, ok: bool) -> None:
    results.append((name, ok))
    print(("PASS  " if ok else "FAIL  ") + name)


# ---------------------------------------------------------------- helpers for the scripted model


def returns(messages: list[ModelMessage]) -> dict[str, list]:
    out: dict[str, list] = {}
    for m in messages:
        if isinstance(m, ModelRequest):
            for p in m.parts:
                if isinstance(p, ToolReturnPart):
                    c = p.content
                    if hasattr(c, "model_dump"):  # specialist reports come back as Pydantic objects
                        c = c.model_dump(mode="json")
                    out.setdefault(p.tool_name, []).append(json.loads(c) if isinstance(c, str) else c)
                elif isinstance(p, RetryPromptPart):
                    seen_retries.append(f"{p.tool_name}: {p.model_response()[:120]}")
    return out


def last(messages, tool):
    r = returns(messages).get(tool)
    return r[-1] if r else None


def first_prompt(messages) -> str:
    return next(p.content for m in messages if isinstance(m, ModelRequest) for p in m.parts if isinstance(p, UserPromptPart))


def ticket_of(messages) -> int:
    text = first_prompt(messages)
    return int(text.split("icket ")[1].split()[0].strip(".("))


def call(tool, **args):
    return ToolCallPart(tool, args)


def final(info: AgentInfo, data: dict):
    return ToolCallPart(info.output_tools[0].name, data)


# ---------------------------------------------------------------- one script per agent


def boss(messages, info, step):
    prompt = first_prompt(messages)
    if prompt.startswith("TRIAGE"):
        if step == 0:
            return [call("list_open_tickets"), call("get_cash_position"), call("get_board")]
        return [final(info, {
            "today": last(messages, "list_open_tickets")["today"],
            "order": [
                {"ticket_id": 101, "ticket_type": "customer_order", "requester": "Tauhid Zaman",
                 "agents": ["Inventory", "Accounting", "Customer Service"], "linked": ["invoice 501", "CC-TEE-WHITE S x1"],
                 "why": "Tee is out of stock and its reprint is held by unpaid invoice 501, which also blocks 103."},
                {"ticket_id": 102, "ticket_type": "rent_notice", "requester": "Elm City Properties",
                 "agents": ["Facilities", "Accounting"], "linked": ["lease 1"], "why": "Check the notice, then request rent."},
                {"ticket_id": 103, "ticket_type": "price_override", "requester": "Yale AI Club",
                 "agents": ["Inventory", "Accounting", "Customer Service"], "linked": ["CC-HOOD-NAVY M x20"],
                 "why": "Short 12 hoodies and a discount needs human approval."},
            ],
            "shared_blockers": ["Invoice 501 blocks Bulldog Print Co (tickets 101 and 103)"],
            "cash_note": "Cash covers 501 and rent with $160 left.",
        })]

    t = ticket_of(messages)
    route = {101: ["ask_inventory", "ask_accounting", "ask_customer_service"],
             102: ["ask_facilities", "ask_accounting"],
             103: ["ask_inventory", "ask_accounting", "ask_customer_service"]}[t]
    if step == 0:
        return [call("get_ticket", ticket_id=t)]
    if step <= len(route):
        return [call(route[step - 1], ticket_id=t, brief=f"Work ticket {t}; report numbers and board ids.")]
    r = returns(messages)
    acct = r["ask_accounting"][-1]
    needs = [{"kind": "approve_payment", "board_id": p["board_id"], "what": f"Approve {p['kind']} {p['ref_id']}", "amount": p["amount"]}
             for p in acct["payment_requests"]]
    if acct.get("price_review") and acct["price_review"].get("override_board_id"):
        needs.append({"kind": "approve_price_override", "board_id": acct["price_review"]["override_board_id"],
                      "what": "Approve bulk price", "amount": acct["price_review"]["quote_total"]})
    draft = r.get("ask_customer_service", [{}])[-1].get("board_id")
    if draft:
        needs.append({"kind": "review_customer_draft", "board_id": draft, "what": "Review reply", "amount": None})
    decision = {"ticket_id": t, "ticket_type": last(messages, "get_ticket")["ticket"]["type"],
                "worked_by": [{"ask_inventory": "Inventory", "ask_accounting": "Accounting", "ask_facilities": "Facilities",
                               "ask_customer_service": "Customer Service"}[x] for x in route],
                "findings": [r[x][-1]["summary"] for x in route], "final_call": "Team finished; the requests posted wait on a person.",
                "status": "resolved", "needs_human": needs, "customer_draft_board_id": draft,
                "next_steps": ["Human approves; vendor ships in lead days."]}
    done_update = "update_ticket" in r
    if t == 102 and not done_update and not any("update_ticket" in s for s in seen_retries):
        return [final(info, decision)]  # forced mistake: no update_ticket yet
    if not done_update:
        return [call("update_ticket", ticket_id=t, status="resolved", note="Team finished; approvals are on the board.")]
    return [final(info, decision)]


def inventory(messages, info, step):
    t = ticket_of(messages)
    if step == 0:
        return [call("get_ticket", ticket_id=t)]
    tk = last(messages, "get_ticket")["ticket"]
    if step == 1:
        return [call("check_stock", sku=tk["sku"], size=tk["size"], qty_needed=tk["qty"])]
    s = last(messages, "check_stock")
    if step == 2:
        return [call("check_vendor_status")]
    v = next(x for x in last(messages, "check_vendor_status")["vendors"] if x["specialty"] == "apparel reprint")
    on_order = next((i for i in v["open_invoices"] if tk["sku"] in i["description"]), None)
    if step == 3 and not on_order:
        return [call("draft_vendor_order", ticket_id=t, vendor_id=v["vendor_id"], sku=tk["sku"], size=tk["size"],
                     qty=s["short_by"], reason=f"Short {s['short_by']}.")]
    draft = last(messages, "draft_vendor_order")
    return [final(info, {
        "ticket_id": t, "sku": s["sku"], "size": s["size"], "product_name": s["name"], "location": s["location"],
        "qty_needed": s["qty_needed"], "on_hand": s["on_hand"], "short_by": s["short_by"],
        "can_fill_from_stock": s["can_fill_from_stock"], "other_sizes_in_stock": s["other_sizes_in_stock"],
        "already_on_order": f"invoice {on_order['invoice_id']}: {on_order['description']}" if on_order else None,
        "vendor": {"vendor_id": v["vendor_id"], "name": v["name"], "specialty": v["specialty"], "lead_days": v["lead_days"],
                   "can_ship": v["can_ship"], "blocked_by_invoices": [i["invoice_id"] for i in v["open_invoices"]],
                   "arrives_by": v["arrives_by_if_ordered_today"], "why_this_vendor": "Only apparel reprint vendor."},
        "vendor_draft_board_id": draft["board_id"] if draft else None,
        "handoffs": [{"to": "Accounting", "reason": "Invoice 501 blocks the vendor."}],
        "summary": f"{s['on_hand']} on hand, short {s['short_by']}; {v['name']} blocked by invoice 501.",
    })]


def accounting(messages, info, step):
    t = ticket_of(messages)
    plan = {101: [call("check_vendor_status", vendor_id=1), call("request_payment", ticket_id=101, kind="invoice", ref_id=501, reason="Releases the tee reprint.")],
            102: [call("check_rent_due", lease_id=1), call("request_payment", ticket_id=102, kind="rent", ref_id=1, reason="Due in 2 days.")],
            103: [call("check_margin", sku="CC-HOOD-NAVY", qty=20, unit_price=58), call("check_margin", sku="CC-HOOD-NAVY", qty=20, unit_price=49.3),
                  call("request_price_override", ticket_id=103, unit_price=49.3, reason="15% off for 20 units keeps $27.30 margin each."),
                  call("request_payment", ticket_id=103, kind="invoice", ref_id=501, reason="Releases the hoodie restock.")]}[t]
    if step == 0:
        return [call("get_cash_position")]
    if step <= len(plan):
        return [plan[step - 1]]
    r = returns(messages)
    cash = r["get_cash_position"][0]["totals"]
    reqs = [{"board_id": p["board_id"], "kind": "invoice" if "invoice" in p.get("title", "invoice") else "rent",
             "ref_id": 501 if t != 102 else 1, "amount": p["amount"], "status": p.get("board_status", "pending_approval")}
            for p in r.get("request_payment", []) if p.get("board_id")]  # skip refusals (e.g. already paid)
    review = None
    if t == 103:
        m, o = r["check_margin"][-1], r["request_price_override"][-1]
        review = {"sku": m["sku"], "qty": m["qty"], "list_price": m["list_price"], "unit_cost": m["unit_cost"],
                  "proposed_unit_price": m["proposed_unit_price"], "discount_pct": m["discount_pct"],
                  "margin_per_unit": m["margin_per_unit"], "quote_total": m["quote_total"],
                  "override_board_id": o.get("board_id"), "reasoning": "Smallest reasonable bulk discount."}
    return [final(info, {"ticket_id": t, "cash_balance": cash["cash"], "pending_before": cash["pending_requests"],
                         "payment_requests": reqs, "price_review": review, "cash_after_pending": 160.0, "refused": [],
                         "risks": ["Restock of 12 hoodies (~$264) won't fit in the $160 left."] if t == 103 else [],
                         "handoffs": [], "summary": f"Ticket {t}: requests waiting on a human."})]


def facilities(messages, info, step):
    if step == 0:
        return [call("get_ticket", ticket_id=102)]
    if step == 1:
        return [call("check_rent_due", lease_id=1)]
    rd = last(messages, "check_rent_due")
    return [final(info, {"ticket_id": 102, "lease_id": rd["lease_id"], "space_name": rd["space_name"], "landlord": rd["landlord"],
                         "monthly_rent": rd["monthly_rent"], "next_due": rd["next_due"], "days_until_due": rd["days_until_due"],
                         "overdue": rd["overdue"], "notice_matches_records": True, "discrepancy": None,
                         "cash_covers_rent": rd["cash_check"][0]["covers"], "recommendation": "Pay before 2026-09-02.",
                         "handoffs": [{"to": "Accounting", "reason": "Request rent payment for lease 1."}],
                         "summary": f"Rent ${rd['monthly_rent']:.0f} due in {rd['days_until_due']} days; notice matches."})]


def customer_service(messages, info, step):
    t = ticket_of(messages)
    r = returns(messages)
    if step == 0:
        return [call("get_ticket", ticket_id=t)]
    if step == 1:
        tk = r["get_ticket"][-1]["ticket"]
        return [call("check_stock", sku=tk["sku"], size=tk["size"], qty_needed=tk["qty"])]
    if "draft_customer_message" not in r:
        body = {101: "Hi Tauhid! Size S of the Classic Bulldog Tee is sold out right now, but a restock is coming. "
                     "We'll let you know the moment it's in. Sizes M, L and XL are available if you'd like. - Campus Customs",
                103: "Hi Yale AI Club! We can do $49.30 per hoodie."}[t]  # 103: forced mistake (unapproved price)
        if t == 103 and any("Draft not saved" in s for s in seen_retries):
            body = ("Hi Yale AI Club! We have 8 Basic Hoodies in size M ready now and are restocking the rest. "
                    "Your bulk pricing request is being reviewed and we'll follow up soon. - Campus Customs")
        return [call("draft_customer_message", ticket_id=t, subject="Your Campus Customs order", body=body)]
    d = r["draft_customer_message"][-1]
    fake = t == 101 and not any("draft_customer_message and report" in s for s in seen_retries)
    return [final(info, {"ticket_id": t, "to": d["to"], "subject": d["subject"], "body": d["body"],
                         "board_id": 9999 if fake else d["board_id"],  # 101: forced mistake (made-up board id)
                         "facts_used": ["stock from check_stock"], "left_out": ["unpaid invoice"], "summary": "Draft saved."})]


SCRIPTS = {"# Boss": boss, "# Inventory": inventory, "# Accounting": accounting,
           "# Facilities": facilities, "# Customer Service": customer_service}


def scripted(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    step = sum(isinstance(m, ModelResponse) for m in messages)
    returns(messages)  # record retry prompts seen so far
    script = next(fn for head, fn in SCRIPTS.items() if (info.instructions or "").startswith(head))
    return ModelResponse(parts=script(messages, info, step))


# ---------------------------------------------------------------- the test


def db(sql):
    with sqlite3.connect(WORK_DIR / "campus_customs_new.db") as con:
        con.row_factory = sqlite3.Row
        return [dict(r) for r in con.execute(sql)]


async def main(tmp: Path) -> None:
    audit_path = tmp / "audit_trail.json"
    report = await run_team(model=FunctionModel(scripted), audit=AuditTrail(audit_path))

    check("refresh ran first and matched the original (rule 8)", report.refreshed["identical_to_original"])
    check("triage covered all 3 tickets, 101 first", [r.ticket_id for r in report.plan.order] == [101, 102, 103])
    check("all 3 tickets finished", all(o.ok for o in report.outcomes) and len(report.outcomes) == 3)
    for o in report.outcomes:
        if not o.ok:
            print(f"      ticket {o.ticket_id} error: {o.error}")
    check("every ticket ends resolved when its run finishes (approvals stay on the board)",
          [o.decision.status if o.decision else None for o in report.outcomes] == ["resolved"] * 3)
    check("tickets.status updated in the database", {r["status"] for r in db("SELECT status FROM tickets")} == {"resolved"})

    board = db("SELECT id, kind, ticket_id, status, amount, created_by FROM board ORDER BY id")
    kinds = [(b["kind"], b["ticket_id"], b["status"]) for b in board]
    check("board: payment request for invoice 501 ($840) by Accounting, pending",
          any(b["kind"] == "payment_request" and b["amount"] == 840.0 and b["status"] == "pending_approval" and b["created_by"] == "Accounting" for b in board))
    check("board: rent request ($2,400), pending", any(b["kind"] == "payment_request" and b["amount"] == 2400.0 for b in board))
    check("board: only ONE request for invoice 501 even though tickets 101 and 103 both need it",
          sum(b["kind"] == "payment_request" and b["amount"] == 840.0 for b in board) == 1)
    check("board: price override for 103 pending; vendor draft for 12 hoodies on_hold",
          ("price_override", 103, "pending_approval") in kinds and ("vendor_draft", 103, "on_hold") in kinds)
    check("board: no vendor order for the tee (invoice 501 already covers it)", ("vendor_draft", 101, "on_hold") not in kinds)
    check("board: customer drafts for 101 and 103 only (no draft to the landlord)",
          sorted(b["ticket_id"] for b in board if b["kind"] == "customer_draft") == [101, 103])
    check("board: one Boss decision per ticket", sorted(b["ticket_id"] for b in board if b["kind"] == "boss_decision") == [101, 102, 103])
    check("no money moved: payments empty, cash still $3,400, invoice 501 still open",
          not db("SELECT * FROM payments") and db("SELECT balance FROM cash_accounts")[0]["balance"] == 3400.0
          and db("SELECT status FROM invoices")[0]["status"] == "open")

    check("guardrail caught: Boss tried to finish 102 without update_ticket", any("update_ticket" in s for s in seen_retries))
    check("guardrail caught: Customer Service quoted an unapproved $49.30", any("Draft not saved" in s for s in seen_retries))
    check("guardrail caught: Customer Service reported a made-up board id", any("draft_customer_message and report" in s for s in seen_retries))
    drafts = db("SELECT details FROM board WHERE kind = 'customer_draft' AND ticket_id = 103")
    check("the saved 103 draft has no price beyond list and promises no discount",
          "$" not in json.loads(drafts[-1]["details"])["body"] and "being reviewed" in json.loads(drafts[-1]["details"])["body"])

    trail = json.loads(audit_path.read_text())
    agents = {e["agent"] for e in trail}
    events = {e["event"] for e in trail}
    check("audit: every agent and the runner appear", agents == {"runner", "Boss", "Inventory", "Accounting", "Facilities", "Customer Service"})
    check("audit: run, refresh, ticket, agent, model and tool steps all recorded",
          {"run_start", "refresh", "ticket_start", "agent_start", "model_response", "tool_results", "agent_end", "ticket_end", "run_end"} <= events)
    tool_names = {c["tool"] for e in trail for c in e["tool_calls"]}
    check("audit: MCP tool calls and delegations are logged with arguments",
          {"get_ticket", "check_stock", "request_payment", "draft_customer_message", "ask_inventory", "update_ticket"} <= tool_names)
    check("audit: each model step carries token counts", all(e["tokens"] is not None for e in trail if e["event"] == "model_response"))
    check("audit: timestamps only go forward (append-only order)", [e["timestamp"] for e in trail] == sorted(e["timestamp"] for e in trail))
    retries = [r for e in trail for r in e["tool_results"] if "error" in r]
    check("audit: guardrail rejections are in the trail", len(retries) >= 3)
    print(f"      audit entries: {len(trail)}, delegations per ticket: "
          f"{[e['detail']['delegations'] for e in trail if e['event'] == 'ticket_end']}")

    async with mcp_client() as c:
        fresh = (await c.call_tool("refresh_working_copy", {})).structured_content
    check("test work copy refreshed back to the original after the test", fresh["identical_to_original"])

    passed = sum(ok for _, ok in results)
    print(f"\n{passed}/{len(results)} passed")
    sys.exit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as d:
        asyncio.run(main(Path(d)))
