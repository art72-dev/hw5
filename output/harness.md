# Campus Customs Multi-Agent Operations — Harness

This file explains the whole system: the data, the agents, the tools and the rules. It grows with each problem.

**Contents:** Shop rules · 1. The database · 2. The MCP server (first 3 tools) · 3. Connecting it + smoke test · 4. The agent team · 5. Safety · 6. Backend routes · 7. Agent dashboard · 8. The resolved run · **9. The finished system at a glance**, which is the one-page summary of tools, agents, routes, dashboard and safety rules.

**Model:** every AI call in this homework uses OpenAI `gpt-6-luna` through Portkey. No other model is used.

## Shop rules

Every agent and tool follows these. The rest of this file refers to them by number.

1. **Today is `desk.date_today`** (2026-08-31 in the data), not the real clock. Overdue means "due before `desk.date_today`."
2. **Vendor lead times** come from `vendors.lead_days`.
3. **A vendor doesn't ship** while it has an open unpaid invoice.
4. **Every payment needs human approval.**
5. **A payment updates every related table:** a new row in `payments`, the invoice marked paid (or the lease's `next_due` moved forward), and a lower `cash_accounts.balance`.
6. **The pay tool refuses** any payment the cash balance can't cover.
7. **Cash only goes out.** No money comes in.
8. **Refresh the working database** from the original before each resolve run.
9. **No emails to customers and no calls to vendors.** Drafts stay on the board.

## 1. The database

### Files

| File | Role |
|---|---|
| `data/campus_customs.db` | **Original.** Never written to. It's the clean starting point. |
| `data/campus_customs_new.db` | **Working copy.** The MCP server and backend read and write this file. |

The working copy was made with a plain file copy and checked against the original (identical SHA-256, `integrity_check` = ok). Shop rule 8 says to refresh it before every resolve run. Run this from `hw5/`:

```bash
cp data/campus_customs.db data/campus_customs_new.db
```

### Tables at a glance

| Table | Rows | What it holds |
|---|---|---|
| `desk` | 1 | The shop's "today" (2026-08-31) |
| `cash_accounts` | 1 | Money the shop can spend: checking, $3,400 |
| `vendors` | 3 | Suppliers and how many days they take to deliver |
| `invoices` | 1 | Bills the shop owes vendors |
| `leases` | 1 | The shop's rent |
| `payments` | 0 | The record of every payment made (empty to start) |
| `pricing` | 4 | What each product costs the shop and sells for |
| `inventory` | 10 | Stock on hand by product and size |
| `tickets` | 3 | The open requests the agents have to work through |

### Fields and why they matter

#### `desk`

| Field | Example | Why it matters for the agent |
|---|---|---|
| `date_today` | 2026-08-31 | The only "today." Every overdue check and every `paid_at` date uses it, never the real clock (rule 1). |
| `notes` | *(empty)* | Free-text shop notes. Empty in this data. |

#### `cash_accounts`

| Field | Example | Why it matters for the agent |
|---|---|---|
| `name` | checking | The account a payment comes out of. Copied into `payments.account`. |
| `balance` | 3400.00 | Money available. The pay tool refuses anything above it (rule 6) and lowers it after every payment (rules 5, 7). |
| `date` | 2026-08-31 | When the balance was last changed. Set to `desk.date_today` when a payment lowers it. |

#### `vendors`

| Field | Example | Why it matters for the agent |
|---|---|---|
| `id` | 1 | Joins to `invoices.vendor_id` to show what the shop owes each vendor. |
| `name` | Bulldog Print Co | Who a draft restock order would go to. Drafts only, never sent (rule 9). |
| `specialty` | apparel reprint | The only clue to which vendor makes which product. There's no SKU-to-vendor link. |
| `lead_days` | 5 | Days until a restock arrives. The only source for delivery estimates (rule 2). |

#### `invoices`

| Field | Example | Why it matters for the agent |
|---|---|---|
| `id` | 501 | The bill being paid. `tickets.invoice_id` and `payments.ref_id` point here. |
| `vendor_id` | 1 | Which vendor is owed. Any open invoice stops that vendor from shipping (rule 3). |
| `amount` | 840.00 | The exact amount to pay. Checked against the cash balance (rule 6). |
| `due_date` | 2026-08-28 | Compared to `desk.date_today`. A due date before today means overdue. |
| `status` | open | `open` means unpaid and blocking the vendor. Set to `paid` after an approved payment (rule 5). |
| `description` | Rush reprint CC-TEE-WHITE S | What the bill is for. #501 is the reprint of the exact tee that ticket 101 needs. |

#### `leases`

| Field | Example | Why it matters for the agent |
|---|---|---|
| `id` | 1 | `tickets.lease_id` and `payments.ref_id` point here. |
| `space_name` | Chapel Street shop | Which space the rent is for. |
| `landlord` | Elm City Properties | Who's owed. Matches the requester on ticket 102. |
| `monthly_rent` | 2400.00 | The exact rent amount to pay. |
| `next_due` | 2026-09-02 | Compared to `desk.date_today` to decide due vs overdue. Moves forward one month after rent is paid (rule 5). |
| `notes` | *(empty)* | Free-text lease notes. Empty in this data. |

#### `payments` (empty to start)

| Field | Example | Why it matters for the agent |
|---|---|---|
| `id` | *(auto)* | Row number for each payment. |
| `kind` | *(none yet)* | What was paid, which tells you whether `ref_id` points to `invoices` or `leases`. |
| `ref_id` | *(none yet)* | The invoice id or lease id that was paid. |
| `amount` | *(none yet)* | Must match the bill and be no more than the cash balance. |
| `account` | *(none yet)* | Which cash account paid (`checking`). |
| `paid_at` | *(none yet)* | The desk date, not the real clock (rule 1). |
| `approved_by` | *(none yet)* | The human who approved the payment. This is the proof for rule 4, so it's never an agent. |

#### `pricing`

| Field | Example | Why it matters for the agent |
|---|---|---|
| `sku` | CC-HOOD-NAVY | Joins to `inventory.sku` and `tickets.sku`. |
| `unit_cost` | 22.00 | What one item costs the shop. Any discount below this loses money. |
| `list_price` | 58.00 | The normal selling price. The starting point for ticket 103's discount. |

#### `inventory`

| Field | Example | Why it matters for the agent |
|---|---|---|
| `sku` + `size` | CC-TEE-WHITE / S | Together they're the key. A ticket's `sku` and `size` find the exact stock row. |
| `name` | Classic Bulldog Tee | Readable product name for drafts and the dashboard. |
| `qty` | 0 | Stock on hand. 0, or less than the ticket's `qty`, means a restock is needed. |
| `location` | Aisle B | Where staff pull the item in the shop. |

#### `tickets`

| Field | Example | Why it matters for the agent |
|---|---|---|
| `id` | 101 | The ticket the agents are working on. |
| `type` | customer_order | Decides which agent picks it up (`customer_order`, `rent_notice`, `price_override`). |
| `requester` | Tauhid Zaman | Who asked. Drafts are written to them but stay on the board (rule 9). |
| `subject` | Bulldog tee | Short title for the dashboard. |
| `sku`, `size`, `qty` | CC-TEE-WHITE, S, 1 | Which product and how many. Used to look up stock and price. |
| `lease_id` | 1 | Links a rent notice to its lease. |
| `invoice_id` | 501 | Links a ticket to the vendor bill tied to it. |
| `status` | open | `open` means work is needed. All 3 tickets are open. |
| `notes` | Needs a tee in size S. | The request in the requester's own words. |
| `created_at` | 2026-08-31T09:15:00-04:00 | When the ticket came in. Sets the order of work. It isn't used for overdue checks. |

### How the tables link

**Real foreign keys** (declared in the schema):
- `tickets.invoice_id` → `invoices.id`
- `tickets.lease_id` → `leases.id`
- `invoices.vendor_id` → `vendors.id`

**Soft links** (matching values, no foreign key):
- `tickets.sku` + `tickets.size` → `inventory.sku` + `inventory.size`
- `tickets.sku` → `pricing.sku`
- `payments.ref_id` → `invoices.id` or `leases.id`, depending on `payments.kind`
- `payments.account` → `cash_accounts.name`
- Product → vendor: only through `vendors.specialty` ("apparel reprint" covers tees and hoodies; "mugs and small goods" covers mugs)

```
tickets ─┬─ invoice_id ─▶ invoices ─ vendor_id ─▶ vendors
         ├─ lease_id ───▶ leases
         └─ sku + size ─▶ inventory, pricing

payments ─┬─ ref_id ──▶ invoices (kind = invoice) or leases (kind = rent)
          └─ account ─▶ cash_accounts
```

### The 3 open tickets

Today is **2026-08-31** and checking has **$3,400**.

#### Ticket 101: customer order, Tauhid Zaman, 1 Classic Bulldog Tee in size S

| Link | What the data says |
|---|---|
| `inventory` (CC-TEE-WHITE, S) | **0 in stock** (Aisle B). Sizes M, L and XL have stock, but the customer asked for S. |
| `pricing` | Sells for $28, costs $8. |
| `invoices` #501 (via `invoice_id`) | $840 for "Rush reprint CC-TEE-WHITE S," due 2026-08-28, **open, 3 days overdue**. |
| `vendors` #1 Bulldog Print Co | Apparel reprint, **5 lead days**. |

**What it means:** The reprint for this tee is already billed, but Bulldog Print Co won't ship while #501 is unpaid (rule 3). The path is: a human approves the $840 payment, the payment is recorded, #501 is marked `paid`, and then the vendor can ship. The tee arrives about 5 days after it ships (rule 2). A reply to Tauhid can be drafted but stays on the board (rule 9).

#### Ticket 102: rent notice, Elm City Properties

| Link | What the data says |
|---|---|
| `leases` #1 (via `lease_id`) | Chapel Street shop, $2,400 a month, next due **2026-09-02**. |

**What it means:** The rent is due in **2 days**, so it isn't overdue yet. The notice arrived by email ("shop rent due in 2 days"). The agent should check that claim against `leases` rather than take it on trust, and here it checks out. Paying needs human approval (rule 4). Afterwards: a new `payments` row, `next_due` moved to 2026-10-02, and a lower cash balance (rule 5).

#### Ticket 103: price override, Yale AI Club, 20 navy hoodies in size M with a bulk discount

| Link | What the data says |
|---|---|
| `inventory` (CC-HOOD-NAVY, M) | **8 in stock** (Aisle A), so **12 short**. |
| `pricing` | Sells for $58, costs $22. 20 at full price = $1,160. 20 at cost = $440. |
| `vendors` (by specialty) | Apparel restocks would come from Bulldog Print Co, 5 lead days, which is **also blocked by open invoice #501**. |

**What it means:** The database has no discount rule, so the price is a human decision. The agent can show the numbers: any price above $22 a hoodie still makes money. Filling all 20 needs a restock of 12, and that is held up by the same unpaid #501. In this system the sale doesn't add cash (rule 7: cash only goes out).

### Cash picture

| Step | Balance |
|---|---|
| Start (checking) | $3,400 |
| Pay invoice #501 | − $840 → $2,560 |
| Pay rent for lease #1 | − $2,400 → **$160** |

Both payments fit, in either order, with only $160 left. Anything else that needs cash afterwards would be refused by the pay tool (rule 6). For example, if 12 restocked hoodies were billed at the $22 unit cost, that's $264.

**Key takeaway:** paying invoice #501 unblocks two tickets (101 and 103), not just one.

### Gaps in the data

- **No SKU-to-vendor link.** Agents have to infer the supplier from `vendors.specialty`.
- **No discount policy.** Ticket 103's price needs a human decision.
- **`payments.kind` has no list of allowed values**, and `ref_id` has no foreign key. We choose the labels (for example `invoice` and `rent`) and must keep them consistent.
- **No "on order" column in `inventory`.** The reprint billed on #501 doesn't show up in `qty` until it arrives.
- **Only `open` appears in `tickets.status`.** The other statuses get defined when the agents start closing tickets.
- **Crest Mug (CC-MUG-CREST) is out of stock**, but no ticket mentions it. Elm City Gifts (mugs, 3 lead days) would supply it.

## 2. The MCP server

> **Problem 3 snapshot:** the first 3 read-only tools. The server grew to 16 tools in Problem 5 and 17 in Problem 7. See **section 4** for the full list, the tables each one uses, and which agent gets it.

`mcp_server/server.py` is a FastMCP server and the shared toolbox for every agent on the team. It opens **only** the working copy, `data/campus_customs_new.db`, and opens it **read-only**, so these tools can't change data, make payments or send messages. "Today" always comes from `desk.date_today`. Every value a tool returns comes from the database. If a SKU, size, vendor or lease isn't on file, the tool returns an error listing what is on file instead of guessing. See `mcp_server/README.md` for how to run it, and section 3 for how it's connected.

### The 3 tools

| Tool | Reads | Tickets it helps unlock |
|---|---|---|
| `check_stock(sku, size, qty_needed)` | `inventory`, `pricing` | **101**, **103** |
| `check_vendor_status(vendor_id)` | `vendors`, `invoices`, `desk`, `cash_accounts` | **101**, **103** |
| `check_rent_due(lease_id)` | `leases`, `desk`, `cash_accounts` | **102** |

### Each tool tied to its ticket

Each row shows the call made with that ticket's own fields and what it really returned from the working copy (tested in-process on 2026-10-10).

| Ticket | Tool call | What it returned | Why this is the right tool for this ticket |
|---|---|---|---|
| **101:** Tauhid, 1 tee in size S | `check_stock("CC-TEE-WHITE", "S", 1)` | 0 on hand, short 1, Aisle B. M (5), L (3) and XL (2) in stock. $28 list, $8 cost. | Ticket 101 is one exact SKU and size, and only `inventory` can show that size S is at 0. That stock-out is what turns a simple sale into a restock problem. |
| **101:** Tauhid, 1 tee in size S | `check_vendor_status(1)` | Bulldog Print Co: `can_ship: false`, no arrival date. Invoice #501, $840, due 2026-08-28, 3 days overdue. Checking covers it, leaving $2,560. 5 lead days. | Ticket 101's `invoice_id` is 501, and this is the only tool that reads `invoices` and applies rule 3. It shows the tee is stuck behind an unpaid bill, not just out of stock. |
| **102:** Elm City Properties, rent notice | `check_rent_due(1)` | Chapel Street shop, $2,400, next due 2026-09-02, due in 2 days, not overdue. Checking covers it, leaving $1,000. | Ticket 102's `lease_id` is 1. This tool checks the email's "due in 2 days" against `leases` and `desk.date_today`, and shows whether cash covers the rent before anyone asks for approval. |
| **103:** Yale AI Club, 20 hoodies in size M | `check_stock("CC-HOOD-NAVY", "M", 20)` | 8 on hand, short 12, Aisle A. $58 list, $22 cost, $36 margin each. `quote_at_list` = $1,160, `cost_floor` = $440 (quote only, not cash in). | Ticket 103 asks for a quantity and a discount. This tool gives both the 12-unit shortfall and the $22 cost floor a human needs to decide the price. |
| **103:** Yale AI Club, 20 hoodies in size M | `check_vendor_status()` (all vendors) | Only Bulldog Print Co does "apparel reprint," and it's blocked by #501. Elm City Gifts (mugs, 3 days) and QuickShip CT (courier, 1 day) can ship, but neither makes hoodies. | The data has no product-to-vendor link, so the agent has to list every vendor's specialty. That shows the 12 missing hoodies are blocked by the same #501 as ticket 101. |

### Rules check: do the tools follow the shop rules?

Every shop rule (top of this file), checked against the 3 tools. The "Tested" column comes from a 17-check test run. The normal cases ran on the working copy. The edge cases ran on a throwaway copy changed to trigger each rule (an invoice not yet due, $0 cash, overdue rent, rent due today, an empty `desk`, a missing database). The real database files were unchanged afterwards.

| Rule | How the tools follow it | Tested |
|---|---|---|
| **1. Today is `desk.date_today`** | `check_vendor_status` and `check_rent_due` take "today" only from `desk`. Overdue means due *before* today, and due today isn't overdue. An empty `desk` returns an error, with no fallback to the clock. `check_stock` doesn't use dates. | #501 is 3 days overdue. Rent is due in 2 days. Rent set to 8/25 shows overdue by 6. Rent set to 8/31 shows "due today," not overdue. Empty `desk` returns an error. |
| **2. Lead times from `vendors`** | `arrives_by_if_ordered_today` = `desk.date_today` + `vendors.lead_days`, with no other source. | Elm City Gifts arrives 2026-09-03 (3 days). QuickShip CT arrives 2026-09-01 (1 day). |
| **3. Open invoice means no shipping** | **Any** `open` invoice blocks the vendor, even one that isn't due yet. A blocked vendor gets `can_ship: false` and no arrival date. | Bulldog is blocked by #501. An open invoice due 9/20 still blocks Elm City Gifts. |
| **4. Human approval for payments** | No tool pays. The tool descriptions and `blocked_reason` say payment needs human approval. | All 3 tools are marked read-only. |
| **5. Payments update every related table** | Nothing to update yet: the tools open the database read-only. The pay tool that writes `payments`, `invoices`/`leases` and `cash_accounts` comes in a later problem. | The checksum is the same before and after every run. |
| **6. Refuse if cash can't cover it** | Each open invoice and each rent check carries a `cash_check` per account (`covers`, `balance_after`). It uses the same helper and field names in both tools. `covers` is false at $0 or when the balance is below the bill. | #501 is covered ($2,560 after). Rent is covered ($1,000 after). With balance set to $0, both show `covers: false`. |
| **7. Cash only goes out** | `check_stock` returns `quote_at_list` and `cost_floor` with a "quote only" note. The old `list_total` field, which looked like income, was removed. No tool adds money. | No revenue field, and the quote is labelled. |
| **8. Refresh before each run** | The tools only open `campus_customs_new.db`. If it's missing, they return an error and never read the original. Refreshing is the run's job (later problem). | A missing working copy returns an error. |
| **9. No emails or calls** | The tools can't send anything. The server instructions every agent sees say drafts stay on the board. | Read-only, with no network access. |

**Fixed during this check:**
- **Rule 6:** `check_vendor_status` only showed the raw balance. Now each invoice gets the same `cash_check` as rent.
- **Rule 7:** `list_total` was renamed to `quote_at_list` and labelled "quote only."
- **Rules 3, 4 and 9:** now written into the server instructions and tool descriptions that agents read.
- **Rule 1:** an empty `desk` returns a clear error instead of crashing.

### What these tools don't do yet

They find the blockers but don't clear them. Tickets 101 and 102 can only be resolved with a payment, which needs human approval, a cash-balance check and updates to `payments`, `invoices` or `leases`, and `cash_accounts` (rules 4–6). Ticket 103 needs a human pricing decision. Those come in later problems. *(Built in Problem 5: the payment-request, price-override and human-only pay tools are in section 4.)*

## 3. Connecting the MCP server and smoke test

### Config

The server is registered as **`campus-customs`** in `hw5/.mcp.json`, which is what Claude Code reads. It also has a local entry in the workspace `.mcp.json` with absolute paths. *(Problem 4 first saved it as `mcp.json`. Problem 11 renamed it `.mcp.json` with relative paths, so the repo works wherever it's cloned.)*

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

- **Runtime:** Python 3.10 in `hw5/.venv` with FastMCP 4.1.0 (`requirements.txt`). Transport is stdio, and the startup banner is off so nothing extra gets written next to the protocol.
- **Loading:** Claude Code reads project servers when a session starts and asks once before enabling a new one.

### Smoke test (`output/mcp_smoke.json`)

`mcp_server/smoke_test.py` starts the server from the `.mcp.json` entry over stdio and lists its tools: `check_rent_due`, `check_stock`, `check_vendor_status`. It then sends each tool one prompt-driven call for a real ticket and checks every value against `data/campus_customs_new.db` with its own SQL. Derived values, such as days overdue and balance after paying, are computed in SQL, so the server can't pass just by agreeing with itself.

| Tool | Prompt that made the call | Arguments | Output | Same value in the database | Match |
|---|---|---|---|---|---|
| `check_stock` | "Ticket 101: Tauhid Zaman needs 1 Classic Bulldog Tee (CC-TEE-WHITE) in size S. Do we have one in stock, and what does it cost us and sell for?" | `sku=CC-TEE-WHITE, size=S, qty_needed=1` | 0 on hand, short 1, Aisle B, $8 cost, $28 list | `inventory.qty` = 0, `location` = Aisle B; `pricing` $8 / $28; short = 1 − 0 = 1 | ✅ 6/6 fields |
| `check_vendor_status` | "Ticket 101 is linked to invoice 501. Can Bulldog Print Co (vendor 1) ship the tee reprint, how overdue is the bill, and could checking cover it?" | `vendor_id=1` | Can't ship. #501, $840, due 2026-08-28, 3 days overdue. 5 lead days. $2,560 left after | `invoices` #501 open, $840, due 8/28; `julianday(desk) − julianday(due)` = 3; `vendors.lead_days` = 5; $3,400 − $840 = $2,560 | ✅ 8/8 fields |
| `check_rent_due` | "Ticket 102: Elm City Properties emailed that the Chapel Street shop rent is due in 2 days. Is that right by the desk date, is it overdue, and can we cover it?" | `lease_id=1` | $2,400, due 2026-09-02, due in 2 days, not overdue, $1,000 left after | `leases` #1, $2,400, `next_due` 9/2; `julianday(next_due) − julianday(desk)` = 2; $3,400 − $2,400 = $1,000 | ✅ 7/7 fields |

**Result:** 3 of 3 tools passed, 21 of 21 fields matched, and the database was unchanged (same SHA-256 before and after). The 17 rule checks from section 2 also pass on the venv's FastMCP 4.1.0.

**How the calls were made:** through a FastMCP stdio client launched from the `.mcp.json` entry, which is the same command Claude Code runs. The `called_via` field in the JSON records this.

## 4. The agent team

Five PydanticAI agents, all on `gpt-6-luna` through Portkey, all getting their shop facts from the **one** campus-customs MCP server. There's no second shop-tools layer. Each agent gets a **filtered slice** of the same MCP toolset (`backend/agents/base.py → tools_for`), and the backend never opens the database itself: even the refresh before a run is an MCP tool call.

```
hw5/
├── .mcp.json                      # how Claude Code and the backend start the MCP server
├── mcp_server/server.py           # 17 tools: 9 read (1 dashboard-only), 5 board, 3 human-only
├── backend/
│   ├── models.py                  # every data type (agent outputs, audit events, run report)
│   ├── audit.py                   # append-only output/audit_trail.json
│   ├── main.py                    # FastAPI routes for the dashboard (section 6)
│   ├── team.py                    # runner: refresh -> triage -> one Boss run per ticket (CLI); one-ticket runs for the API
│   ├── build_run_report.py        # rebuilds resolved_tickets.json + resolved_board.html (Problem 9)
│   ├── agents/
│   │   ├── base.py                # gpt-6-luna model, MCP connection, token limits, logged agent loop
│   │   ├── boss.py                # Boss (+ triage) and the ask_* delegation tools
│   │   └── inventory.py · accounting.py · facilities.py · customer_service.py
│   ├── prompts/                   # one prompt file per agent
│   │   └── boss.md · inventory.md · accounting.md · facilities.md · customer_service.md
│   └── tests/                     # test_mcp_tools · test_team_offline · test_api · demo_backend
├── frontend/                      # React + Vite + TypeScript dashboard (section 7)
└── output/                        # harness, plan page, run report, screenshots, audit trail
```

### How the agents connect

```
                          ┌──────────── runner (backend/team.py) ────────────┐
                          │ 1. refresh_working_copy (MCP, rule 8)            │
                          │ 2. Boss triage  -> TriagePlan (order + routes)   │
                          │ 3. Boss per ticket -> BossDecision               │
                          └──────────────────────────────────────────────────┘
                                              │
                ┌──────────── Boss ───────────┴─────────────────────────────┐
                │ ask_inventory · ask_accounting · ask_facilities ·         │
                │ ask_customer_service  (+ its own MCP slice)               │
                └──────┬──────────────┬───────────────┬──────────────┬──────┘
                   Inventory      Accounting      Facilities   Customer Service
                       └──────────────┴───────┬───────┴──────────────┘
                                   campus-customs MCP server (stdio)
                                              │
                                 data/campus_customs_new.db
```

- **Hub and spoke.** The Boss is connected to every specialist and every specialist is connected to the MCP server. Specialists don't call each other. They return `handoffs` ("Accounting should pay invoice X") and the Boss decides. That keeps every loop bounded: no specialist can start another one.
- **Shared memory is the board.** Agents learn what others did from the `board` table, so tickets don't double-post. For example, ticket 103's Accounting finds ticket 101's request for invoice #501 already on the board and reuses it.
- **The agent loop.** Each run goes step by step with `agent.iter()`. The model asks for tools, the MCP server answers, the model either asks again or returns its output model, and an **output validator** checks that output against the tool results before it's accepted. A failed check sends the agent a retry message. Every step is appended to the audit trail as it happens.

### Each agent, its MCP tools and its output

| Agent | Job (business functions) | MCP tools | Other tools | Returns | Output check (rejects and retries if it fails) |
|---|---|---|---|---|---|
| **Boss (triage)** | Reads every open ticket, picks only the specialists each one needs, and orders the work so blockers go first | `list_open_tickets`, `get_ticket`, `get_board`, `get_cash_position` | none | `TriagePlan` | Plan covers every open ticket, and `today` equals the desk date |
| **Boss (ticket)** | Briefs specialists in order, follows useful handoffs, makes and records the final call | `list_open_tickets`, `get_ticket`, `get_board`, `get_cash_position`, `update_ticket` | `ask_inventory`, `ask_accounting`, `ask_facilities`, `ask_customer_service` | `BossDecision` | `update_ticket` was called and its status matches. `worked_by` lists only agents actually asked. Every board id came from a tool or report. |
| **Inventory** | Stock by SKU and size, shortfall, other sizes, aisle, already on order?, which vendor (by specialty), lead time, draft vendor order | `get_ticket`, `check_stock`, `check_vendor_status`, `draft_vendor_order` | none | `InventoryReport` | on hand, short and needed match `check_stock`. Vendor `can_ship` and `lead_days` match `check_vendor_status`. The draft id came from `draft_vendor_order`. |
| **Accounting** | Cash, invoices, margins, payment requests and price overrides for human approval, money risks | `get_ticket`, `get_cash_position`, `check_vendor_status`, `check_rent_due`, `check_margin`, `get_board`, `request_payment`, `request_price_override` | none | `AccountingReport` | Cash equals `get_cash_position`. Every request id and amount came from `request_payment` or the board. The override id came from `request_price_override`. |
| **Facilities** | Leases, rent and the space. Checks the notice against the lease, due vs. overdue, landlord, amount, cash cover. Hands payment to Accounting. | `get_ticket`, `check_rent_due` | none | `FacilitiesReport` | Rent, next due, days until due, overdue and cash cover all match `check_rent_due` |
| **Customer Service** | Drafts the reply to the ticket's requester, kept on the board and never sent | `get_ticket`, `check_stock`, `check_vendor_status`, `get_board`, `draft_customer_message` | none | `CustomerDraft` | Board id, recipient, subject and body are exactly what `draft_customer_message` saved |
| **Human only** (dashboard later) | Approve or reject requests, pay approved requests, refresh before a run | `decide_request`, `pay_approved_request`, `refresh_working_copy` | none | none | Not in any agent's allowlist. `team.py --check` proves it. |

### Every MCP tool and the tables it uses

| Tool | Kind | Reads | Writes | Used by |
|---|---|---|---|---|
| `list_open_tickets` | read | tickets, desk | none | Boss |
| `list_tickets` | read (dashboard) | tickets, desk | none | `GET /api/tickets` only, no agent |
| `get_ticket` | read | tickets, invoices, vendors, leases, inventory, pricing, desk, board | none | Boss, Inventory, Accounting, Facilities, Customer Service |
| `check_stock` | read | inventory, pricing | none | Inventory, Customer Service |
| `check_vendor_status` | read | vendors, invoices, desk, cash_accounts | none | Inventory, Accounting, Customer Service |
| `check_rent_due` | read | leases, desk, cash_accounts | none | Facilities, Accounting |
| `get_cash_position` | read | cash_accounts, invoices, vendors, leases, payments, board, desk | none | Boss, Accounting |
| `check_margin` | read | pricing | none | Accounting |
| `get_board` | read | board | none | Boss, Accounting, Customer Service |
| `request_payment` | board | tickets, invoices, vendors, leases, cash_accounts, board, desk | board | Accounting |
| `request_price_override` | board | tickets, pricing, inventory, board, desk | board | Accounting |
| `draft_vendor_order` | board | tickets, vendors, invoices, inventory, pricing, board, desk | board | Inventory |
| `draft_customer_message` | board | tickets, pricing, board, desk | board | Customer Service |
| `update_ticket` | board | tickets, board, desk | tickets, board | Boss |
| `decide_request` | human only | board, vendors, invoices, cash_accounts | board | a person, through `POST /api/board/{id}/approve` |
| `pay_approved_request` | human only | desk, board, invoices or leases, vendors, pricing, cash_accounts | **payments, invoices or leases, cash_accounts, board** (one transaction) | a person, through `POST /api/board/{id}/approve` |
| `refresh_working_copy` | human / runner only | `campus_customs.db` (original, read only) | `campus_customs_new.db` (replaced) | CLI runner before every run, `POST /api/reset`, and the first API run |

### Why these tools: what the 3 open tickets need

| Ticket | What it needs that the first 3 tools couldn't do | Tool added |
|---|---|---|
| All | The Boss must see every ticket and its links before routing | `list_open_tickets`, `get_ticket` |
| 101 (tee size S) | Invoice #501 must be **paid** before Bulldog ships. That needs a request a human approves, then a payment that updates every table. The tee reprint is already on #501, so a second order must be blocked. | `request_payment`, `decide_request`, `pay_approved_request`, duplicate-order guard in `draft_vendor_order` |
| 101, 103 | The customers need a reply, and it must stay a draft | `draft_customer_message` |
| 102 (rent) | Rent must be requested and paid, and the lease's `next_due` moved forward a month | `request_payment(kind="rent")`, `pay_approved_request` |
| 103 (20 hoodies, discount) | Margin at a proposed price, a discount a human approves, and a restock draft for the 12 short (on hold behind #501) | `check_margin`, `request_price_override`, `draft_vendor_order` |
| All | One view of cash vs. **all** bills and pending requests, so two tickets can't spend the same $3,400 | `get_cash_position` |
| All | The Boss records the final call, and the team doesn't double-post | `update_ticket`, `get_board` |
| Every run | Refresh before each run (rule 8), through MCP, not a file copy in the backend | `refresh_working_copy` |

### The `board` table (the only table added)

The MCP server creates `board` in the **working copy** the first time something is posted. It holds payment requests, price overrides, vendor drafts, customer drafts and Boss decisions, each with who posted it, the desk date, its status and, once decided, the human who decided. It isn't shop data and nothing is invented in it. It's the team's output, waiting for a human. The original database never gets it, and `refresh_working_copy` wipes it before each run.

Statuses: payment and price requests go `pending_approval` → `approved` or `rejected` → (payments) `paid`. Drafts are `draft`, or `on_hold` for a vendor that can't ship, or `superseded` by a newer draft. Boss decisions are `logged`.

### Running it

```bash
.venv/bin/python backend/team.py --check        # connectivity: 17 tools, every agent's slice, no human-only leaks
.venv/bin/python backend/team.py                # full run on every open ticket (refreshes first)
.venv/bin/python backend/team.py --ticket 101   # selected tickets (still refreshes first)
```

### Audit trail (`output/audit_trail.json`)

Append-only JSON list. Each entry is one loop step: `timestamp`, `run_id`, `ticket_id`, `agent`, `step`, `event`, `tool_calls` (tool and arguments), `tool_results` (shortened results or errors), `text`, `tokens` (this request and the running total) and `detail`. Events are `run_start`, `refresh`, `ticket_start`, `agent_start`, `model_response`, `tool_results`, `agent_end`, `agent_stopped`, `ticket_end`, `run_end`.

- Entries are written **as each step happens**, by an atomic rewrite that only adds. Nothing earlier is changed or removed.
- Text is cut to 500 characters, and emails, card-like numbers and API-key-like strings are redacted first.
- The file is created on the first real run. The audit itself gets recorded in a later problem.

### Tested so far

| Check | Result |
|---|---|
| `backend/team.py --check` | All tools on the server (16 in Problem 5, 17 after Problem 7 added `list_tickets`). Every agent's slice found. No human-only tool in any agent. |
| `backend/tests/test_mcp_tools.py` (throwaway database copy) | **45/45.** Every tool, every guardrail, approve → pay updating `payments`, `invoices`/`leases`, `cash_accounts` and `board`, double-pay refused, rule 6 at request time and at pay time, refresh. |
| `backend/tests/test_team_offline.py` (scripted model, real runner, real MCP server, temp audit file) | **24/24.** Full loop on all 3 tickets: one request for #501 shared by 101 and 103, no re-order of the tee, no landlord draft, no money moved. 3 forced mistakes all caught: no `update_ticket`, an unapproved price, a fake board id. 133 audit entries. |
| Live model ping | `gpt-6-luna` through Portkey answered: 24 tokens, 2.5 s. |
| Database after all tests | Working copy identical to the original (same SHA-256). |

### Rules check for the new tools

| Rule | How the team follows it |
|---|---|
| **1. Today = `desk.date_today`** | Every date check, `paid_at`, the cash `date` and the board's `desk_date` use the desk date. Triage is rejected if its `today` isn't the desk date. |
| **2. Lead times from `vendors`** | Vendor drafts give arrival = desk date + `lead_days` only if the vendor can ship. Customer Service may only say "about N days once it ships." |
| **3. Open invoice means no shipping** | `draft_vendor_order` puts the order `on_hold` with the blocking invoice ids. Tested: an open invoice not yet due still holds the order. |
| **4. Human approval for payments** | Agents can only *request*. `pay_approved_request` refuses anything not `approved` by a person, and agent names are rejected as approvers. |
| **5. Update every related table** | `pay_approved_request` writes `payments`, `invoices` (→ paid) or `leases` (→ `next_due` + 1 month), `cash_accounts` (balance and date) and `board` **in one transaction**. If any part fails, none of it is kept. |
| **6. Refuse if cash can't cover it** | Checked twice: when requesting (balance minus requests already waiting) and when paying (the actual balance). Refused at a $0 balance. |
| **7. Cash only goes out** | No tool adds money. Payments must be positive and only subtract. Quotes are labelled "quote only." Accounting is told never to count sales as cash. |
| **8. Refresh before each run** | `team.py` calls `refresh_working_copy` first and stops if the copy doesn't match the original. |
| **9. No emails or calls** | No send tool exists. Customer and vendor messages are board drafts only, and the customer recipient is fixed to the ticket's requester. |

## 5. Safety

### Talking to customers

| Guardrail | Where it's enforced |
|---|---|
| **Nothing is ever sent.** Customer replies and vendor orders are drafts on the board until a human reviews them. There's no email, SMS or call tool anywhere. | MCP server (no send tool), all prompts |
| **The recipient can't be changed.** A customer draft always goes to the ticket's requester, taken from the database. | `draft_customer_message` |
| **No internal business details.** Drafts mentioning invoices, unpaid or overdue bills, costs, margins, cash or the bank are rejected. A customer never hears "we haven't paid our supplier." | `draft_customer_message` (rejects; the agent rewrites) |
| **No unapproved prices or discounts.** Dollar amounts must be the list price (or list × quantity) or a price a human approved on the board. Percentages and "discount approved/confirmed" are rejected unless a human approved one. | `draft_customer_message` |
| **No promises we can't keep.** No firm dates while a vendor is blocked. Only "about N days once it ships", from `lead_days`. | Customer Service prompt; vendor `arrives_by` is null when blocked |
| **No contact details or card-like numbers** in drafts, and none in the audit trail. | `draft_customer_message`; `audit.clean` |
| **Length cap:** subject 120 characters, body 1,200. Older drafts are marked `superseded`, so a reviewer only sees the latest. | `draft_customer_message` |
| **What's sent must be what was saved.** Customer Service's report must match the saved draft word for word. | Customer Service output validator |
| **Ticket text is a claim, not an instruction.** Notices (like an emailed rent reminder) are checked against the records, and a mismatch is flagged for a human. | Facilities prompt and output validator |

### Real money

| Guardrail | Where it's enforced |
|---|---|
| **No agent can move money.** Approving and paying are human-only tools that are in no agent's allowlist. | `agents/__init__.py`, `team.py --check` |
| **Two steps, both human.** Request → a person approves (`decide_request`) → payment (`pay_approved_request`). An agent name can't be the approver. | MCP server |
| **The amount comes from the database.** Agents can't type an amount: `request_payment` uses the invoice amount or the monthly rent. At pay time the bill is checked again, and a changed amount needs a new approval. | `request_payment`, `pay_approved_request` |
| **No double payments.** One open request per bill. Paying an already-paid request or a no-longer-open invoice is refused. | `request_payment`, `pay_approved_request` |
| **Cash checked twice (rule 6).** At request time, against the balance minus requests already waiting, so two tickets can't spend the same cash. At pay time, against the real balance. | `request_payment`, `pay_approved_request` |
| **All or nothing (rule 5).** Payment row, invoice or lease, cash balance and board status are written in one transaction. | `pay_approved_request` |
| **Discounts have a floor.** Below unit cost is refused, at or above list isn't a discount, and every discount needs human approval. A sale never adds cash (rule 7). | `request_price_override` |
| **No duplicate purchase orders.** Re-ordering an item an open invoice already covers is refused. | `draft_vendor_order` |
| **Clear separation of duties.** Only Accounting requests payments and prices, only Inventory drafts vendor orders, only Customer Service drafts customer messages, and only the Boss sets ticket status. | Allowlists, plus `Literal` role parameters on the tools |
| **Numbers can't be made up.** Each agent's output is checked against its own tool results before it's accepted. | Output validators in every agent |
| **Everything is recorded.** Every step, tool call and refusal is in the append-only audit trail. The original database is never written to. | `audit.py`, read-only `campus_customs.db` |

### Token use

| Limit | Value | Why |
|---|---|---|
| Max output per model response | 3,000 tokens | No runaway answers |
| Boss per ticket | 10 requests, 14 tool calls, 60,000 tokens | Bounded final-call loop |
| Boss triage | 4 requests, 6 tool calls, 30,000 tokens | Triage is a quick read, not work |
| Each specialist run | 6 requests, 8 tool calls, 40,000 tokens (or what's left in the ticket budget, if less) | A specialist can't loop forever |
| Whole team, one ticket | 150,000 tokens. No new specialist starts with under 10,000 left. | One hard ticket can't drain the run |
| Whole run | 450,000 tokens. No new ticket starts past this. | Caps the cost of a run |
| Asks per specialist | At most 2 per ticket | Stops Boss-specialist ping-pong |
| Routing | Triage picks **only** the specialists a ticket needs (no "ask everyone") | Fewer runs, fewer tokens |
| Small tool replies | `get_board` returns at most 25 items. Audit text is shortened. | Keeps the context window small |
| Prompt rules | "One call per fact, never repeat a call with the same arguments", briefs under 80 words | Fewer requests |
| Graceful stop | A specialist that hits its limit returns a `SpecialistFailure` and the Boss decides. A failed ticket doesn't stop the others. | Limits never crash the run |
| Visibility | Every model step logs its input and output tokens and the running total | You can see where tokens went |

These numbers are settings in `backend/agents/base.py`, so they can be tightened once real runs show actual usage.

## 6. Backend routes (`backend/main.py`)

FastAPI, started from `backend/` with `../.venv/bin/uvicorn main:app --reload --port 8000` (docs at `/docs`). Every route reaches the shop **only through the campus-customs MCP server**: one MCP client opened when the app starts, the same server the agents use. Agents only **prepare** payments on the board. **`POST /api/board/{id}/approve` is the only thing that changes cash.**

- `GET /api/tickets`: the 3 tickets, each marked `open` or `resolved` (plus raw status and the last agent run).
- `POST /api/tickets/{ticket_id}/run`: start the agent team on one ticket in the background and return its `run_id` (202). One run at a time, otherwise 409.
- `GET /api/runs/{run_id}`: one run's status (`running`, `done` or `failed`) and the Boss's decision.
- `GET /api/events`: recent agent events, newest first. Who acted, what they said, and which tools they used. Filter with `?ticket_id=`, `?run_id=`, `?agent=` and `?limit=`.
- `GET /api/board`: what the agents posted for people (payment requests, price overrides, purchase drafts, customer drafts). Filter with `?ticket_id=`, `?kind=` and `?status=`.
- `POST /api/board/{board_id}/approve`: a person clicks Approve (`{"approved_by": "<name>"}`). A **payment** or **purchase** is approved and paid in one step, updating `payments`, the invoice or lease, and `cash_accounts`. A price override is just approved.
- `GET /api/cash`: the current checking balance from `cash_accounts`, plus what's waiting for approval.
- `POST /api/reset`: reset the working database to the original values (rule 8). This clears the board, payments and every change. Refused while a run is going.

**How runs and refreshes fit together (rule 8):** start a session with `POST /api/reset`, then run tickets one at a time. A one-ticket run doesn't refresh again, so ticket 103 can see the #501 request that ticket 101 posted. The **first** run after the API starts always refreshes, even if you forget to reset. A one-ticket run skips triage, and the Boss picks the specialists from its routing guide. The CLI (`backend/team.py`) still does refresh, triage, then all tickets.

**Approve, step by step:**
1. `decide_request` records the person's approval. An agent's name is rejected as approver.
2. For a payment or purchase, `pay_approved_request` runs: one transaction, with the cash re-checked (rule 6).
3. If cash can't cover it, the route returns 409, nothing changes, and the item stays approved. Clicking Approve again later retries the payment.
4. Approving an already-paid item is refused (no double payment).
5. A purchase can only be approved once its vendor has no open invoice (rule 3), and only if its estimated cost (unit cost × qty) fits in cash after the requests already waiting. A purchase payment is recorded as `payments.kind = 'purchase'` (`ref_id` = the board id). Inventory doesn't change until the goods arrive.

**Audit:** people's approvals and resets are appended to `output/audit_trail.json` too (`agent: "human"`, events `human_decision` and `reset`), next to every agent step.

**Not built yet:** login. Anyone who can reach the API can approve. In a real shop this route would sit behind sign-in, and `approved_by` would come from the signed-in person, not the request body. CORS only allows the dashboard's dev address (`localhost:5173`).

**Tested offline** (`backend/tests/test_api.py`, scripted model, real routes and MCP server, temp audit file): **21/21**.
- Reset gives $3,400. The tickets are open. A run on 999 returns 404.
- A run on 101 returns 202, a second run returns 409, and a reset during the run returns 409. The run finishes (Problem 7: `waiting_on_human`; since Problem 8: `resolved`), without triage and without a second refresh.
- Events show Boss, Inventory, Accounting and Customer Service with their tools, and no Facilities.
- Cash is still $3,400 after the agents ran.
- Approving as "Accounting" returns 400. Approving #501 pays it ($3,400 → $2,560, invoice paid), and approving it again returns 409.
- Ticket 102: rent approved, `next_due` → 10/02, $160 left.
- Ticket 103: the price override is approved with no cash change, and the 12-hoodie purchase ($264) is refused because only $160 is left.
- A final reset gives back $3,400, an empty board and open tickets.
- The MCP tool tests now cover a purchase too (**49/49**): approval refused while #501 is open, then paid for $264 once #501 is paid.

## 7. Agent dashboard (`frontend/`)

React + Vite + TypeScript, started with `npm run dev` in `frontend/` (http://localhost:5173). It talks to the backend at `http://localhost:8000` (`frontend/.env`), and the backend allows that Vite origin through CORS (`VITE_ORIGINS` in `main.py`). The look and the reasons behind it are in **`output/design.md`**.

- **Tickets:** `GET /api/tickets`. All 3 as cards. Pick one, then **Start the team** (`POST /api/tickets/{id}/run`). One run at a time.
- **Live feed:** `GET /api/events?run_id=` and `GET /api/runs/{id}` every second. Each agent's briefs, tool steps, results, guardrail send-backs and reports, in its own colour and voice.
- **Resolved:** when the run finishes, the Boss records `resolved` (approvals stay on the board, rule 4), the ticket is stamped RESOLVED, and **What the team did** shows the final call, the crew, the findings and the tokens.
- **Approvals:** `GET /api/board` and `POST /api/board/{id}/approve`. A signed approval. Payments and purchases are paid by the route, and refusals show the backend's reason.
- **Cash:** `GET /api/cash`. The checking balance counts down after an approved payment, with a receipt showing before → after.
- **Reset:** `POST /api/reset`.

**Change in this problem:** a ticket run now ends **`resolved`** (team finished, every approval on the board) or **`blocked`** (couldn't finish). `waiting_on_human` is no longer a final state. `update_ticket` allows `resolved` while approvals wait and returns them as `still_waiting_on_a_person`. The Boss's output check enforces resolved or blocked, and the API refuses to re-run a resolved ticket until a reset.

## 8. The resolved run (Problem 9)

**Reset first.** `POST /api/reset` called `refresh_working_copy`, so the working copy was byte-for-byte the original (SHA-256 `23686a90…f360a`). **Starting balance: $3,400.00** in checking (`cash_accounts`, dated 2026-08-31). No payments, all 3 tickets open.

**Then all 3 tickets, one at a time, from the dashboard** ("Start the team"). The real `gpt-6-luna` team ran each through the API, and each ended **resolved**. After each run a person approved what the team had prepared. For this run the user OK'd the approvals in chat; Claude clicked Approve, signed *"Anjali (OK'd in chat, clicked by Claude)"*, so the audit trail is honest about who clicked.

| Ticket | Boss called | MCP tool calls | Left on the board | Human step | Checking | Run |
|---|---|---|---|---|---|---|
| **101** Bulldog tee | Inventory → Accounting → Customer Service | 13 | #501 payment request $840, draft to Tauhid | #501 approved and paid (payment #1) | $3,400 → **$2,560** | 26 s · 18 requests · 56,460 tokens |
| **102** Rent due | Facilities → Accounting | 7 | Rent request $2,400 | Rent approved and paid (payment #2). Lease next due → 2026-10-02 | $2,560 → **$160** | 13 s · 12 requests · 32,763 tokens |
| **103** Bulk hoodies | Inventory → Accounting → Customer Service | 16 | Restock draft 12 × $22 = $264, discount $55 × 20, draft to the club | Discount approved (no cash). Purchase tried and **refused** ($264 > $160, rule 6) | $160 → **$160** | 47 s · 19 requests · 63,373 tokens |

- **Ending balance: $160.00 = `cash_accounts.balance`** (checked read-only in `data/campus_customs_new.db`). `payments` has 2 rows, invoice #501 is `paid`, and all 3 tickets are `resolved`.
- **Delegation:** 8 specialist calls out of a possible 12, the same as the plan. Facilities only on 102, Inventory and Customer Service never on 102.
- **Totals:** 152,596 tokens and 49 model requests. Every run stayed well inside the token limits (the biggest ticket used 63k of its 150k budget). No guardrail send-backs in this run.
- **Where it's recorded:**
  - `output/desk_tickets.html`: **Actual** sections and the **Cash** tab.
  - `output/resolved_tickets.json`: id, final status, outcome, each agent's contribution and tools, human approvals, cash before and after, and run stats.
  - `output/resolved_board.html`: dashboard screenshots after each ticket resolved and after each approval.
  - `output/audit_trail.json`: every loop step and every human decision.
- **Rebuild the report:** `.venv/bin/python backend/build_run_report.py --shots <folder>`. Screenshots come from `frontend/scripts/snap_board.cjs` (headless Chrome).

**What went wrong, and the fix.** The first full run (01:18–01:23: runs `9036a12feb61`, `4cec5cd9335b`, `5802d248c5c6`) also resolved all 3 tickets with the same cash steps. Afterwards, running the offline test suites **reset the real working database**, because the tests drove the real MCP server, which pointed at `data/campus_customs_new.db`. Fixes:
1. The MCP server now takes `CAMPUS_CUSTOMS_WORK_DIR`. The tests and the demo backend set it to a temp folder, so they can never touch the working copy. The original is still always `data/campus_customs.db`. Verified: the working copy's modification time was unchanged across all three suites.
2. The API now **rebuilds run history from the audit trail** on startup, so the dashboard keeps its runs after a restart. New runs store the Boss's full decision in `ticket_end`.
3. **Refused approval attempts** are now logged (`human_decision`, `approved: false`).

The run was then redone from a clean reset (runs `001671679c16`, `4212a2db5c63`, `636c01144889`), and every report uses that second run. Both runs stay in the append-only audit trail: 264 entries, 6 runs, 8 human decisions, 3 resets.

## 9. The finished system at a glance

```
person ──▶ React dashboard (frontend/, :5173) ──▶ FastAPI (backend/main.py, :8000)
                                                    │  approve route = the only place cash changes
                                                    ├──▶ agent team (PydanticAI, gpt-6-luna via Portkey)
                                                    │      Boss ─┬─ Inventory ─ Accounting ─ Facilities ─ Customer Service
                                                    │            └── each agent: its own slice of the MCP tools
                                                    └──▶ campus-customs MCP server (FastMCP, stdio) ──▶ data/campus_customs_new.db
                                                                       refresh reads data/campus_customs.db (never written)
```

**Run it:** `.venv/bin/uvicorn main:app --reload --port 8000` from `backend/`, then `npm run dev` from `frontend/`, then open http://localhost:5173. Or run the CLI with no dashboard: `.venv/bin/python backend/team.py` (refresh, triage, all tickets).

### The tools (MCP server, 17)

| Tool | Kind | Tables | Who |
|---|---|---|---|
| `list_open_tickets` | read | tickets, desk | Boss |
| `list_tickets` | read | tickets, desk | dashboard (`GET /api/tickets`) |
| `get_ticket` | read | tickets, invoices, vendors, leases, inventory, pricing, desk, board | all five agents |
| `check_stock` | read | inventory, pricing | Inventory, Customer Service |
| `check_vendor_status` | read | vendors, invoices, desk, cash_accounts | Inventory, Accounting, Customer Service |
| `check_rent_due` | read | leases, desk, cash_accounts | Facilities, Accounting |
| `get_cash_position` | read | cash_accounts, invoices, vendors, leases, payments, board, desk | Boss, Accounting |
| `check_margin` | read | pricing | Accounting |
| `get_board` | read | board | Boss, Accounting, Customer Service |
| `request_payment` | board (writes board) | tickets, invoices, vendors, leases, cash_accounts, board, desk | Accounting |
| `request_price_override` | board | tickets, pricing, inventory, board, desk | Accounting |
| `draft_vendor_order` | board | tickets, vendors, invoices, inventory, pricing, board, desk | Inventory |
| `draft_customer_message` | board | tickets, pricing, board, desk | Customer Service |
| `update_ticket` | board (writes tickets, board) | tickets, board, desk | Boss |
| `decide_request` | human only | board, vendors, invoices, cash_accounts | approve route |
| `pay_approved_request` | human only | **writes payments, invoices or leases, cash_accounts, board** (one transaction) | approve route |
| `refresh_working_copy` | human / runner only | reads the original, replaces the working copy | reset route, CLI runner, first API run |

### The five agents

| Agent | Does | MCP tools | Returns (checked against its own tool results) |
|---|---|---|---|
| **Boss** | Reads every ticket (triage), picks only the specialists needed, briefs them one at a time, makes and records the final call (`resolved` or `blocked`) | list_open_tickets, get_ticket, get_board, get_cash_position, update_ticket, plus ask_inventory / ask_accounting / ask_facilities / ask_customer_service | `TriagePlan`, `BossDecision` |
| **Inventory** | Stock by SKU and size, shortfall, other sizes, already on order?, vendor by specialty, lead time, restock draft | get_ticket, check_stock, check_vendor_status, draft_vendor_order | `InventoryReport` |
| **Accounting** | Cash, invoices, margins. The only agent that requests payments and price overrides, always for a person to approve | get_ticket, get_cash_position, check_vendor_status, check_rent_due, check_margin, get_board, request_payment, request_price_override | `AccountingReport` |
| **Facilities** | Lease, rent and space. Checks a notice against the lease, then hands payment to Accounting | get_ticket, check_rent_due | `FacilitiesReport` |
| **Customer Service** | Drafts the reply to the requester. It stays on the board and is never sent. | get_ticket, check_stock, check_vendor_status, get_board, draft_customer_message | `CustomerDraft` |

Prompts: `backend/prompts/<agent>.md`. Data types: `backend/models.py`. Agents: `backend/agents/`.

### The API routes (`backend/main.py`)

- `GET /api/tickets`: the 3 tickets, open or resolved, with each one's last run.
- `POST /api/tickets/{id}/run`: start the team on one ticket (202). One run at a time; a resolved ticket needs a reset first.
- `GET /api/runs/{run_id}`: a run's status and the Boss's decision (rebuilt from the audit trail after a restart).
- `GET /api/events`: newest-first agent events: who acted, what they said, which tools they used.
- `GET /api/board`: what the agents prepared for a person.
- `POST /api/board/{id}/approve`: a person approves. Payments and purchases are paid here, the only place cash changes. Refusals are explained and logged.
- `GET /api/cash`: the checking balance from `cash_accounts`.
- `POST /api/reset`: back to the original database (rule 8).

### The dashboard (`frontend/`)

React + Vite + TypeScript in a neutral, earthy "back-office ledger" style (`output/design.md`).
- Tickets as index cards, and **Start the team** on the selected one.
- A live feed where every agent has its own monogram, ink and voice.
- A RESOLVED stamp and a **What the team did** summary when a run ends.
- A signed **Waiting on you** in-tray.
- A checking balance that counts down when a payment is approved.

### The safety rules

**Shop rules (all 9 enforced in code):**
- Today is `desk.date_today`.
- Lead times come from `vendors`.
- A vendor with an open invoice doesn't ship.
- Every payment needs human approval.
- A payment updates `payments`, the invoice or lease, and `cash_accounts` in one transaction.
- The pay tool refuses anything cash can't cover, checked when requesting and again when paying.
- Cash only goes out.
- Refresh before each run.
- No emails or calls; drafts stay on the board.

**Customer messages:**
- Drafts only.
- The recipient is fixed to the requester.
- No internal details (bills, costs, margins, cash), no contact details or card-like numbers.
- No price except list or a human-approved one, no unapproved discount or percentage, no firm dates while a vendor is blocked.
- Length is capped.

**Real money:**
- No agent can approve or pay; those tools are human-only and in no allowlist.
- Amounts always come from the database.
- One request per bill, no double payment.
- An agent's name can't approve.
- Discounts have a cost floor.
- No duplicate purchase orders.
- Every agent's output is checked against its tool results.
- Every step and every human decision is in the append-only audit trail.

**Tokens:**
- 3,000 output tokens per response.
- Per-agent request and tool-call limits.
- 150k tokens per ticket and 450k per run.
- At most 2 asks per specialist.
- Triage picks only the needed specialists.
- Small tool replies.
- Graceful stops.
- Tokens logged on every step. The real run used 152,596 for all 3 tickets.

**Data and testing:**
- The original database is never written.
- Tests and demos run on a temp copy (`CAMPUS_CUSTOMS_WORK_DIR`), never the working copy.
- 49 MCP checks, 24 offline team checks and 22 API checks pass.
