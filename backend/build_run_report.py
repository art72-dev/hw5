"""Build the Problem 9 run report from the live system.

Reads the API (runs, tickets, board, cash), the MCP server (payments,
cash_accounts) and output/audit_trail.json, then writes:
  output/resolved_tickets.json   one entry per ticket: status, outcome, agents, approvals, cash
  output/resolved_board.html     the dashboard screenshots for each resolved ticket (embedded)

Needs the backend on :8000. From hw5/:
  .venv/bin/python backend/build_run_report.py --shots <folder with 101_resolved.jpg ...>
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import html
import json
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

HW = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HW / "backend"))

from agents.base import MODEL_NAME, mcp_client  # noqa: E402

API = "http://localhost:8000"
OUT = HW / "output"
ROLE = {
    "Boss": "Read the ticket, chose the specialists, made and recorded the final call",
    "Inventory": "Stock by SKU and size, shortfall, already on order?, which vendor, restock draft",
    "Accounting": "Cash, invoices, margins; payment requests and price overrides for a person",
    "Facilities": "Checked the notice against the lease: amount, due date, overdue, cash cover",
    "Customer Service": "Drafted the reply to the requester; saved on the board, never sent",
}


def get(path: str):
    with urllib.request.urlopen(f"{API}{path}") as r:
        return json.load(r)


def seconds(a: str, b: str) -> int:
    return round((datetime.fromisoformat(b) - datetime.fromisoformat(a)).total_seconds())


async def mcp_cash() -> dict:
    async with mcp_client() as c:
        return (await c.call_tool("get_cash_position", {})).structured_content


def build(shots: Path | None) -> dict:
    tickets = {t["id"]: t for t in get("/api/tickets")["tickets"]}
    board = get("/api/board")["items"]
    position = asyncio.run(mcp_cash())
    trail = json.loads((OUT / "audit_trail.json").read_text())

    run_ids = {tid: t["last_run"]["run_id"] for tid, t in tickets.items() if t["last_run"]}
    last_reset = max((e for e in trail if e["event"] == "reset"), key=lambda e: e["timestamp"])
    human = [e for e in trail if e["agent"] == "human" and e["event"] == "human_decision"
             and e["timestamp"] > last_reset["timestamp"]]
    runs = {tid: get(f"/api/runs/{rid}") for tid, rid in run_ids.items()}

    # Cash walk: start, then each ticket's paid board items in ticket order.
    start_balance = position["accounts"][0]["balance"] + sum(p["amount"] for p in position["payments_made"])
    paid_by_ticket = {tid: [b for b in board if b.get("ticket_id") == tid and b["status"] == "paid"] for tid in tickets}
    balance = start_balance
    report_tickets = []
    for tid in sorted(tickets, key=lambda t: runs[t]["started_at"] if t in runs else ""):
        t, run = tickets[tid], runs.get(tid)
        if not run:
            continue
        outcome = run["result"]["outcomes"][0]
        decision = outcome["decision"] or {}
        events = [e for e in trail if e["run_id"] == run["run_id"]]
        agents = []
        for name in ["Boss", *decision.get("worked_by", [])]:
            mine = [e for e in events if e["agent"] == name]
            tools = [c["tool"] for e in mine for c in e["tool_calls"]
                     if not c["tool"].startswith(("final_result", "ask_"))]
            delegated = [c["tool"].removeprefix("ask_").replace("_", " ").title()
                         for e in mine for c in e["tool_calls"] if c["tool"].startswith("ask_")]
            said = next((e["text"] for e in reversed(mine) if e["event"] == "agent_end"), "")
            agents.append({
                "agent": name, "role": ROLE[name],
                "contributed": said.split("] ", 1)[1] if said.startswith("[") else said,
                "mcp_tools": tools, **({"delegated_to": delegated} if delegated else {}),
                "model_requests": sum(1 for e in mine if e["event"] == "model_response"),
            })

        before = balance
        balance -= sum(b["amount"] for b in paid_by_ticket[tid])
        items = [b for b in board if b.get("ticket_id") == tid and b["kind"] != "boss_decision"]
        approvals = []
        for need in decision.get("needs_human", []):
            item = next((b for b in board if b["id"] == need["board_id"]), {})
            event = next((e for e in reversed(human) if (e["detail"] or {}).get("board_id") == need["board_id"]), None)
            refused = event and (event["detail"] or {}).get("approved") is False
            if item.get("status") == "paid":
                result = f"approved and paid (payment #{item.get('payment_id')})"
            elif item.get("kind") == "price_override" and item.get("status") == "approved":
                result = "approved; no cash moves for a price"
            elif refused:
                result = f"approval tried and refused: {event['detail']['reason']}"
            elif item.get("kind") == "customer_draft":
                result = "draft waiting for a person to review; never sent"
            else:
                result = f"still {item.get('status', 'waiting')}"
            approvals.append({"board_id": need["board_id"], "kind": need["kind"], "what": need["what"],
                              "amount": need["amount"], "result": result,
                              "decided_by": item.get("decided_by") or (event and event["text"].split(" tried")[0]),
                              "decided_at": item.get("decided_at") or (event and event["timestamp"])})
        report_tickets.append({
            "id": tid, "type": t["type"], "requester": t["requester"], "subject": t["subject"],
            "final_status": t["status"], "outcome": decision.get("final_call"),
            "agents": agents, "human_approvals": approvals,
            "board_items": [{"board_id": b["id"], "kind": b["kind"], "status": b["status"], "title": b["title"]} for b in items],
            "cash": {"before": before, "after": balance, "moved": round(balance - before, 2)},
            "run": {"run_id": run["run_id"], "started_at": run["started_at"], "finished_at": run["finished_at"],
                    "seconds": seconds(run["started_at"], run["finished_at"]), "tokens": outcome["tokens"],
                    "model_requests": sum(1 for e in events if e["event"] == "model_response"),
                    "mcp_tool_calls": sum(1 for e in events for c in e["tool_calls"]
                                          if not c["tool"].startswith(("final_result", "ask_"))),
                    "delegations": next((e["detail"]["delegations"] for e in events if e["event"] == "ticket_end"), {}),
                    "guardrail_send_backs": sum(1 for e in events for r in e["tool_results"] if "error" in r)},
        })

    ending = position["accounts"][0]["balance"]
    report = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "model": MODEL_NAME, "desk_date": position["today"],
        "reset_before_run": {"at": last_reset["timestamp"], **(last_reset["detail"] or {})},
        "starting_balance": start_balance, "ending_balance": ending,
        "ending_balance_matches_cash_accounts": abs(balance - ending) < 0.005,
        "cash_accounts": position["accounts"], "payments": position["payments_made"],
        "total_tokens": sum(t["run"]["tokens"] for t in report_tickets),
        "tickets": report_tickets,
    }
    (OUT / "resolved_tickets.json").write_text(json.dumps(report, indent=2) + "\n")
    if shots:
        write_board_html(report, shots)
    return report


def write_board_html(report: dict, shots: Path) -> None:
    def img(name: str) -> str:
        path = shots / name
        return f"data:image/jpeg;base64,{base64.b64encode(path.read_bytes()).decode()}" if path.exists() else ""

    money = lambda n: f"${n:,.2f}"
    sections = []
    for t in report["tickets"]:
        crew = " → ".join(a["agent"] for a in t["agents"][1:])
        approvals = "".join(
            f"<li><b>{html.escape(a['kind'].replace('_', ' '))}</b> (board #{a['board_id']}"
            f"{', ' + money(a['amount']) if a['amount'] else ''}): {html.escape(a['result'])}</li>"
            for a in t["human_approvals"])
        shots_html = ""
        for suffix, label in (("resolved", "Right after the run finished: ticket stamped Resolved, nothing paid yet"),
                              ("approved", "After the human approval step")):
            src = img(f"{t['id']}_{suffix}.jpg")
            if src:
                shots_html += (f'<figure><figcaption>{label}</figcaption>'
                               f'<img src="{src}" alt="Dashboard, ticket {t["id"]}, {suffix}"></figure>')
        sections.append(f"""
<section class="ticket" id="t{t['id']}">
  <header>
    <h2>Ticket {t['id']} · {html.escape(t['subject'])}</h2>
    <span class="chip">{html.escape(t['final_status'])}</span>
  </header>
  <p class="meta">{html.escape(t['type'])} · {html.escape(t['requester'])} · run <code>{t['run']['run_id']}</code> ·
     {t['run']['seconds']}s · {t['run']['tokens']:,} tokens · {t['run']['model_requests']} model requests</p>
  <p class="outcome">{html.escape(t['outcome'] or '')}</p>
  <div class="facts">
    <div><h3>Team</h3><p>Boss → {html.escape(crew)}</p></div>
    <div><h3>Cash</h3><p>{money(t['cash']['before'])} → <b>{money(t['cash']['after'])}</b>
       {'(' + money(t['cash']['moved']) + ')' if t['cash']['moved'] else '(no change)'}</p></div>
  </div>
  <h3>Human approvals</h3><ul>{approvals}</ul>
  <div class="shots">{shots_html}</div>
</section>""")

    page = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Campus Customs: Resolved Board</title>
<style>
  :root {{ --bg:#0a0a0d; --panel:#15151b; --line:#2c2c38; --text:#f4f1f6; --muted:#a8a3b3; --pink:#ff4fa3; --pink-soft:#ff8cc6; --pink-dim:#4a1f36; }}
  * {{ box-sizing: border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--text); font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif; padding:28px clamp(16px,4vw,48px) 64px; }}
  .wrap {{ max-width:1100px; margin:0 auto; }}
  .eyebrow {{ color:var(--pink); font-weight:700; letter-spacing:.14em; text-transform:uppercase; font-size:12px; }}
  h1 {{ margin:6px 0; font-size:clamp(26px,3.4vw,36px); }} h1 span {{ color:var(--pink); }}
  .sub {{ color:var(--muted); max-width:820px; }}
  nav {{ display:flex; gap:8px; flex-wrap:wrap; margin:16px 0 8px; }}
  nav a {{ color:var(--text); text-decoration:none; border:1px solid var(--line); background:var(--panel); padding:6px 14px; border-radius:999px; }}
  nav a:hover {{ border-color:var(--pink); }}
  .summary {{ display:flex; gap:10px; flex-wrap:wrap; margin:10px 0 24px; }}
  .summary span {{ background:var(--panel); border:1px solid var(--line); border-radius:999px; padding:6px 14px; font-size:13px; }}
  .summary b {{ color:var(--pink-soft); }}
  .ticket {{ background:var(--panel); border:1px solid var(--line); border-radius:14px; padding:22px; margin:0 0 26px; }}
  .ticket header {{ display:flex; gap:12px; align-items:baseline; flex-wrap:wrap; }}
  .ticket h2 {{ margin:0; font-size:23px; }}
  .chip {{ font-size:12px; font-weight:650; padding:3px 10px; border-radius:999px; border:1px solid var(--pink); color:var(--pink-soft); background:var(--pink-dim); }}
  .meta {{ color:var(--muted); font-size:13px; margin:4px 0 12px; }}
  code {{ background:#0f0f14; border:1px solid var(--line); border-radius:6px; padding:1px 6px; color:var(--pink-soft); font-size:12.5px; }}
  .outcome {{ border-left:3px solid var(--pink); padding:8px 14px; background:#101016; border-radius:0 8px 8px 0; }}
  .facts {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(260px,1fr)); gap:12px; }}
  h3 {{ color:var(--pink-soft); font-size:13px; letter-spacing:.06em; text-transform:uppercase; margin:16px 0 6px; }}
  .facts p {{ margin:0; }} ul {{ margin:0; padding-left:20px; }} li {{ margin:4px 0; }}
  .shots {{ display:grid; gap:22px; margin-top:16px; }}
  figure {{ margin:0; }} figcaption {{ color:var(--muted); font-size:13px; margin-bottom:6px; }}
  img {{ width:100%; border-radius:10px; border:1px solid var(--line); display:block; background:#fff; }}
  footer {{ color:var(--muted); font-size:12.5px; margin-top:24px; }}
</style></head><body><div class="wrap">
<div class="eyebrow">Campus Customs · Problem 9</div>
<h1>The board, <span>ticket by ticket</span></h1>
<p class="sub">Screenshots of the React dashboard (<code>frontend/</code>) taken during the real run on {html.escape(report['model'])}.
Each ticket shows the board right after the agents resolved it, and again after the human approval step.</p>
<div class="summary">
  <span>Desk date <b>{report['desk_date']}</b></span>
  <span>Starting balance <b>{money(report['starting_balance'])}</b></span>
  <span>Ending balance <b>{money(report['ending_balance'])}</b> = cash_accounts</span>
  <span>Tokens <b>{report['total_tokens']:,}</b></span>
</div>
<nav>{''.join(f'<a href="#t{t["id"]}">Ticket {t["id"]}</a>' for t in report['tickets'])}</nav>
{''.join(sections)}
<footer>Generated {report['generated_at']} by <code>backend/build_run_report.py</code> from the API, the MCP server and
<code>output/audit_trail.json</code>. Screenshots: <code>frontend/scripts/snap_board.cjs</code> (headless Chrome, 1440px wide).</footer>
</div></body></html>
"""
    (OUT / "resolved_board.html").write_text(page)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--shots", type=Path, help="Folder with <ticket>_resolved.jpg and <ticket>_approved.jpg")
    args = parser.parse_args()
    report = build(args.shots)
    for t in report["tickets"]:
        print(f"{t['id']}: {t['final_status']} · cash {t['cash']['before']} -> {t['cash']['after']} · "
              f"{', '.join(a['agent'] for a in t['agents'])} · {len(t['human_approvals'])} approvals")
    print(f"start {report['starting_balance']} -> end {report['ending_balance']} "
          f"(matches cash_accounts: {report['ending_balance_matches_cash_accounts']}), tokens {report['total_tokens']:,}")


if __name__ == "__main__":
    main()
