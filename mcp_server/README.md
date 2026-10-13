# Campus Customs MCP Server

## What it's for

This is the **shared toolbox for the Campus Customs ops agent team** (Boss, Inventory, Accounting, Facilities, Customer Service). Agents never write SQL and the backend never opens the database itself. Every shop fact, every draft, every approval request and every payment goes through these tools, so the rules live in one place.

It's built with [FastMCP](https://gofastmcp.com) and talks over stdio. It's registered with Claude Code (the vibe coder) through `.mcp.json`, and the backend agents connect with the same command from `mcp.json`.

## Which database it uses

| File | How the server uses it |
|---|---|
| `../data/campus_customs_new.db` | **The working copy.** Every tool works on this file. Read tools open it read-only. Board and human-only tools write to it inside transactions. |
| `../data/campus_customs.db` | **The original.** Only `refresh_working_copy` touches it, and only to *read* it as the source of a fresh copy. Nothing ever writes to it. |

The server adds one table to the working copy, **`board`**. It holds payment requests, price overrides, vendor drafts, customer drafts and Boss decisions while they wait for a human. Refreshing wipes it.

Refresh the working copy before a run (rule 8). `backend/team.py` does this automatically by calling `refresh_working_copy`. By hand, from `hw5/`:

```bash
cp data/campus_customs.db data/campus_customs_new.db
```

If the working copy is missing, every tool returns an error saying so.

**Tests and demos never use the working copy.** They set `CAMPUS_CUSTOMS_WORK_DIR` to a temp folder, and the server then keeps its working database there. The original is still always `data/campus_customs.db`. Without that variable, the server uses `data/campus_customs_new.db` as normal.

## The 17 tools

### Read: look things up; the database is opened read-only (9)

| Tool | Inputs | Reads | Returns | Agents |
|---|---|---|---|---|
| `list_open_tickets` | none | tickets, desk | Every unresolved ticket, oldest first, with linked ids | Boss |
| `list_tickets` | none | tickets, desk | Every ticket with `state` open or resolved | dashboard only (`GET /api/tickets`) |
| `get_ticket` | `ticket_id` | tickets, invoices, vendors, leases, inventory, pricing, desk, board | The ticket plus its invoice (with vendor and days overdue), lease (days until due), stock row, price and board items | all five |
| `check_stock` | `sku`, `size`, `qty_needed` | inventory, pricing | On hand, short by, aisle, other sizes, cost, list price, `quote_at_list`, `cost_floor` | Inventory, Customer Service |
| `check_vendor_status` | `vendor_id` (empty = all) | vendors, invoices, desk, cash_accounts | Can it ship, open invoices with days overdue and a cash check, lead days, arrival if ordered today | Inventory, Accounting, Customer Service |
| `check_rent_due` | `lease_id` | leases, desk, cash_accounts | Rent, next due, days until due, overdue, cash check | Facilities, Accounting |
| `get_cash_position` | none | cash_accounts, invoices, vendors, leases, payments, board, desk | Cash, every open bill, next rent, requests waiting, payments made, cash after pending, cash after all bills | Boss, Accounting |
| `check_margin` | `sku`, `qty`, `unit_price` | pricing | Discount %, margin per unit and total, below-cost flag | Accounting |
| `get_board` | `ticket_id`, `kind`, `status` (all optional) | board | At most the 25 newest matching board items | Boss, Accounting, Customer Service |

### Board: agents post drafts and requests; nothing is sent or paid (5)

| Tool | Inputs | Reads | Writes | Guardrails | Agent |
|---|---|---|---|---|---|
| `request_payment` | `ticket_id`, `kind` (invoice/rent), `ref_id`, `reason` | tickets, invoices, vendors, leases, cash_accounts, board, desk | board | Amount comes from the database. Refuses if already paid, already waiting, or if cash after waiting requests can't cover it (rule 6). | Accounting |
| `request_price_override` | `ticket_id`, `unit_price`, `reason` | tickets, pricing, inventory, board, desk | board | SKU, size and quantity come from the ticket. Below cost is refused. At or above list isn't a discount. No duplicates. | Accounting |
| `draft_vendor_order` | `ticket_id`, `vendor_id`, `sku`, `size`, `qty`, `reason` | tickets, vendors, invoices, inventory, pricing, board, desk | board | `on_hold` if the vendor has an open invoice (rule 3). Arrival = desk date + lead days (rule 2). Refuses to re-order what an open invoice already covers. Never sent. | Inventory |
| `draft_customer_message` | `ticket_id`, `subject`, `body` | tickets, pricing, board, desk | board | Recipient = the ticket's requester. Rejects internal terms (invoice, unpaid, cost, margin, cash…), contact details, unapproved prices or percentages, and "discount approved". Max 120 / 1,200 characters. Never sent (rule 9). | Customer Service |
| `update_ticket` | `ticket_id`, `status`, `note` | tickets, board, desk | tickets, board | Logs the decision on the board. `resolved` is allowed while approvals wait; they stay on the board and are returned as `still_waiting_on_a_person`. | Boss |

### Human only: never in any agent's allowlist (3)

| Tool | Inputs | Writes | Guardrails | Who |
|---|---|---|---|---|
| `decide_request` | `board_id`, `decision`, `decided_by`, `note` | board | Approver must be a person, not an agent name. Payment and price requests must be `pending_approval`. A purchase (vendor draft) needs a vendor with no open invoice (rule 3), and its est. cost must fit in cash after waiting requests (rule 6). | A person, through `POST /api/board/{id}/approve` |
| `pay_approved_request` | `board_id` | **payments, invoices or leases, cash_accounts, board**, in one transaction | Must be `approved` by a person (rule 4). Bill or unit cost re-checked. No double pay. Refused if cash can't cover it (rule 6). `paid_at` = desk date. Invoice → `paid`, rent → lease `next_due` + 1 month, purchase → `payments.kind = 'purchase'` with inventory unchanged until arrival (rule 5). Cash only goes down (rule 7). | A person, through `POST /api/board/{id}/approve` |
| `refresh_working_copy` | none | replaces `campus_customs_new.db` | Copies the original exactly and confirms the checksum (rule 8) | The runner, before every run |

## Rules built into the tools

| Rule | How the tools follow it |
|---|---|
| 1. Today is `desk.date_today` | All dates come from `desk`, never the clock. Overdue means "due before today." `paid_at`, the cash `date` and board `desk_date` use the desk date. An empty `desk` returns an error. |
| 2. Lead times from `vendors` | Arrival = desk date + `vendors.lead_days`, only when the vendor can ship |
| 3. Open invoice means no shipping | Any open invoice blocks the vendor, even if it isn't due yet: `can_ship: false`, no arrival date, vendor drafts `on_hold` |
| 4. Payments need human approval | Agents can only request. Approving and paying are human-only, and agent names are rejected as approvers. |
| 5. Payments update every related table | `pay_approved_request` writes payments, invoice or lease, cash and board together, or not at all |
| 6. Refuse if cash can't cover it | Checked when requesting (minus requests already waiting) and again when paying. Refused at $0. |
| 7. Cash only goes out | No tool adds money. Quotes are labelled "quote only." Payments must be positive. |
| 8. Refresh before each run | `refresh_working_copy`, called first by `backend/team.py` |
| 9. No emails or calls | There's no send tool. Customer and vendor messages are board drafts only. |

**No invented data:** if a SKU, size, vendor, lease or ticket isn't in the database, the tool returns an error listing what *is* on file.

## How to run

From `hw5/` (Python 3.10+):

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python mcp_server/server.py
```

The server waits for an MCP client on stdio, so running it by itself just sits there. That's expected.

## Connect it to Claude Code

`hw5/.mcp.json` registers the server for Claude Code. Paths are relative to `hw5/`, so it works wherever the folder is cloned:

```json
{
  "mcpServers": {
    "campus-customs": {
      "command": ".venv/bin/python",
      "args": ["mcp_server/server.py"]
    }
  }
}
```

Open `hw5/` as the project in Claude Code and approve `campus-customs` when it asks. The tools appear as `mcp__campus-customs__<tool>`. The backend starts the server from the same entry, with paths resolved against `hw5/` (`backend/agents/base.py`).

## Tests

```bash
.venv/bin/python mcp_server/smoke_test.py   # Problem 4: the first 3 tools over stdio vs. the database -> output/mcp_smoke.json
.venv/bin/python backend/tests/test_mcp_tools.py    # all 17 tools and every guardrail, on a throwaway database copy (49 checks)
```
