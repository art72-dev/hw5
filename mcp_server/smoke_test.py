"""Smoke test for the campus-customs MCP server.

Launches the server over stdio from the entry in .mcp.json (the same command
Claude Code runs), calls each tool once for a real ticket, then checks the
output against data/campus_customs_new.db with its own SQL. The derived
numbers (days overdue, balance after, ...) are computed in SQL, not by the
server code, so a bug in the server can't hide by agreeing with itself.

Run from Homework 5/:  .venv/bin/python mcp_server/smoke_test.py
Writes:                output/mcp_smoke.json
"""

import asyncio
import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path

from fastmcp import Client

HW = Path(__file__).resolve().parent.parent
CONFIG_PATH = HW / ".mcp.json"
DB_PATH = HW / "data" / "campus_customs_new.db"
OUT_PATH = HW / "output" / "mcp_smoke.json"

CASES = [
    {
        "ticket": 101,
        "prompt": "Ticket 101: Tauhid Zaman needs 1 Classic Bulldog Tee (CC-TEE-WHITE) in size S. "
                  "Do we have one in stock, and what does it cost us and sell for?",
        "tool": "check_stock",
        "arguments": {"sku": "CC-TEE-WHITE", "size": "S", "qty_needed": 1},
        "sql": """
            SELECT i.name, i.qty AS on_hand, i.location, p.unit_cost, p.list_price,
                   MAX(0, 1 - i.qty) AS short_by
            FROM inventory i JOIN pricing p ON p.sku = i.sku
            WHERE i.sku = 'CC-TEE-WHITE' AND i.size = 'S'
        """,
        "compare": lambda out: {
            "name": out["name"],
            "on_hand": out["on_hand"],
            "location": out["location"],
            "unit_cost": out["pricing"]["unit_cost"],
            "list_price": out["pricing"]["list_price"],
            "short_by": out["short_by"],
        },
    },
    {
        "ticket": 101,
        "prompt": "Ticket 101 is linked to invoice 501. Can Bulldog Print Co (vendor 1) ship the tee reprint, "
                  "how overdue is the bill, and could checking cover it?",
        "tool": "check_vendor_status",
        "arguments": {"vendor_id": 1},
        "sql": """
            SELECT v.name, v.lead_days, inv.id AS invoice_id, inv.amount, inv.due_date,
                   CAST(julianday(d.date_today) - julianday(inv.due_date) AS INTEGER) AS days_overdue,
                   (SELECT COUNT(*) FROM invoices WHERE vendor_id = v.id AND status = 'open') = 0 AS can_ship,
                   c.balance - inv.amount AS balance_after
            FROM vendors v
            JOIN invoices inv ON inv.vendor_id = v.id AND inv.status = 'open'
            CROSS JOIN desk d CROSS JOIN cash_accounts c
            WHERE v.id = 1 AND c.name = 'checking'
        """,
        "compare": lambda out: {
            "name": out["vendors"][0]["name"],
            "lead_days": out["vendors"][0]["lead_days"],
            "invoice_id": out["vendors"][0]["open_invoices"][0]["invoice_id"],
            "amount": out["vendors"][0]["open_invoices"][0]["amount"],
            "due_date": out["vendors"][0]["open_invoices"][0]["due_date"],
            "days_overdue": out["vendors"][0]["open_invoices"][0]["days_overdue"],
            "can_ship": int(out["vendors"][0]["can_ship"]),
            "balance_after": out["vendors"][0]["open_invoices"][0]["cash_check"][0]["balance_after"],
        },
    },
    {
        "ticket": 102,
        "prompt": "Ticket 102: Elm City Properties emailed that the Chapel Street shop rent is due in 2 days. "
                  "Is that right by the desk date, is it overdue, and can we cover it?",
        "tool": "check_rent_due",
        "arguments": {"lease_id": 1},
        "sql": """
            SELECT l.space_name, l.landlord, l.monthly_rent, l.next_due,
                   CAST(julianday(l.next_due) - julianday(d.date_today) AS INTEGER) AS days_until_due,
                   l.next_due < d.date_today AS overdue,
                   c.balance - l.monthly_rent AS balance_after
            FROM leases l CROSS JOIN desk d CROSS JOIN cash_accounts c
            WHERE l.id = 1 AND c.name = 'checking'
        """,
        "compare": lambda out: {
            "space_name": out["space_name"],
            "landlord": out["landlord"],
            "monthly_rent": out["monthly_rent"],
            "next_due": out["next_due"],
            "days_until_due": out["days_until_due"],
            "overdue": int(out["overdue"]),
            "balance_after": out["cash_check"][0]["balance_after"],
        },
    },
]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def db_row(sql: str) -> dict:
    with closing(sqlite3.connect(f"{DB_PATH.as_uri()}?mode=ro", uri=True)) as con:
        con.row_factory = sqlite3.Row
        row = con.execute(sql).fetchone()
        return dict(row) if row else {}


async def main() -> None:
    config = json.loads(CONFIG_PATH.read_text())
    raw = config["mcpServers"]["campus-customs"]
    absolute = lambda p: str(HW / p) if ("/" in p and not Path(p).is_absolute()) else p
    entry = {"command": absolute(raw["command"]), "args": [absolute(a) for a in raw["args"]]}
    sha_before = sha256(DB_PATH)

    results = []
    async with Client({"mcpServers": {"campus-customs": entry}}) as client:
        listed = sorted(t.name for t in await client.list_tools())
        for case in CASES:
            reply = await client.call_tool(case["tool"], case["arguments"])
            output = reply.structured_content
            row = db_row(case["sql"])
            from_tool = case["compare"](output)
            compared = [
                {"field": k, "tool": v, "db": row.get(k), "match": v == row.get(k)}
                for k, v in from_tool.items()
            ]
            results.append({
                "ticket": case["ticket"],
                "prompt": case["prompt"],
                "tool": case["tool"],
                "arguments": case["arguments"],
                "output": output,
                "db_check": {"sql": " ".join(case["sql"].split()), "row": row},
                "compared": compared,
                "match": all(c["match"] for c in compared),
            })

    sha_after = sha256(DB_PATH)
    report = {
        "test": "campus-customs MCP smoke test",
        "run_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "server": {
            "name": "campus-customs",
            "config_file": CONFIG_PATH.name,
            "transport": "stdio",
            "called_via": "FastMCP stdio client launched from the .mcp.json entry (the same command Claude Code runs)",
            "command": entry["command"],
            "args": entry["args"],
        },
        "database": f"data/{DB_PATH.name}",
        "tools_listed": listed,
        "results": results,
        "db_unchanged": sha_before == sha_after,
        "db_sha256": sha_after,
        "summary": {
            "tools_tested": len({r["tool"] for r in results}),
            "passed": sum(r["match"] for r in results),
            "failed": sum(not r["match"] for r in results),
        },
    }
    OUT_PATH.write_text(json.dumps(report, indent=2) + "\n")

    for r in results:
        print(f"{'PASS' if r['match'] else 'FAIL'}  {r['tool']:<20} ticket {r['ticket']}")
    print(f"tools listed: {listed}")
    print(f"db unchanged: {report['db_unchanged']}  ->  {OUT_PATH.relative_to(HW)}")


if __name__ == "__main__":
    asyncio.run(main())
