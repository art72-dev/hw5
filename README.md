# Campus Customs Multi-Agent Operations: Homework 5

A back office for **Campus Customs**, Yale's merch shop, run by a team of AI agents. The agents work through the shop's open tickets (customer orders, rent, unpaid vendor bills, discounts), and a person watches them on a dashboard and approves anything that costs money.

- **MCP server** (`mcp_server/`, FastMCP): 17 tools over the shop database. These tools are the only way anything reads or changes shop data.
- **Agent team** (`backend/`, PydanticAI, OpenAI **`gpt-6-luna`** through Portkey): **Boss**, **Inventory**, **Accounting**, **Facilities** and **Customer Service**. Each agent gets only the MCP tools its job needs.
- **API** (`backend/main.py`, FastAPI): tickets, agent runs, live agent events, approvals, cash and reset.
- **Dashboard** (`frontend/`, React + Vite + TypeScript): pick a ticket, watch the agents work, approve payments, and watch the checking balance.

Agents **prepare**, a **person approves**, and only the approve route moves cash.

## Folder structure

```
hw5/
├── AI_prompts.md            # every problem's prompt, follow-ups and result
├── requirements.txt
├── .env.example
├── .gitignore
├── .mcp.json                # registers the MCP server for Claude Code (paths relative to hw5/)
├── README.md
├── data/
│   ├── campus_customs.db    # the original: never written
│   └── campus_customs_new.db  # the working copy the system uses (shipped in its post-run state: $160)
├── mcp_server/
│   ├── server.py            # the 17 tools
│   ├── smoke_test.py        # Problem 4 smoke test -> output/mcp_smoke.json
│   └── README.md
├── frontend/                # React + Vite + TypeScript dashboard
├── backend/
│   ├── main.py              # FastAPI routes
│   ├── models.py            # every data type
│   ├── team.py              # runner (CLI): refresh -> triage -> one Boss run per ticket
│   ├── audit.py             # append-only output/audit_trail.json
│   ├── build_run_report.py  # rebuilds resolved_tickets.json + resolved_board.html
│   ├── agents/              # boss.py, inventory.py, accounting.py, facilities.py, customer_service.py, base.py
│   ├── prompts/             # boss.md, inventory.md, accounting.md, facilities.md, customer_service.md
│   └── tests/               # offline tests (temp database, scripted model)
└── output/
    ├── harness.md           # the whole system: data, tools, agents, routes, dashboard, safety, run
    ├── mcp_smoke.json       # Problem 4: each tool vs. the database
    ├── desk_tickets.html    # plan vs. actual per ticket, Cash, Reflection
    ├── design.md            # dashboard design choices
    ├── resolved_tickets.json  # Problem 9 result per ticket
    ├── resolved_board.html  # dashboard screenshots for each resolved ticket
    ├── audit_trail.json     # every agent-loop step and human decision
    └── github_url.txt
```

## How to run

You need Python 3.10+, Node 20.19+ and a Portkey key. Run every command from the `hw5/` folder unless a step says otherwise.

### 0. One-time setup

```bash
cp .env.example .env          # then put your PORTKEY_API_KEY in .env (never commit .env)
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cd frontend && npm install && cd ..
```

### 1. Copy the original database to the working copy (whenever you need a clean run)

`data/campus_customs.db` is the **original**, and nothing ever writes to it. `data/campus_customs_new.db` is the **working copy**: the MCP server, the backend and the agents all read and write it.

The repo ships the working copy in its **post-run state** (after Problem 9: checking $160, two payments, all tickets resolved), so it matches the files in `output/`. To start clean, copy the original over it:

```bash
cp data/campus_customs.db data/campus_customs_new.db
```

After the copy you have the starting data: checking $3,400, 3 open tickets, an empty `payments` table, and invoice #501 open.

### 2. Start the MCP server

The MCP server (`mcp_server/server.py`, FastMCP) runs over **stdio**, so whatever uses it launches it with the command in `.mcp.json`.

- **For the app, you don't start it by hand.** The FastAPI backend (step 3) starts its own MCP server process on startup, and every agent run starts one too. No extra terminal is needed.
- **To start it by itself and confirm it works:**

  ```bash
  .venv/bin/python backend/team.py --check    # starts the MCP server, lists its 17 tools and each agent's tools, no model calls
  .venv/bin/python mcp_server/server.py       # or run it raw: it waits for an MCP client on stdio (Ctrl+C to stop)
  ```

- **In Claude Code:** open `hw5/` as the project and approve `campus-customs` when asked. `.mcp.json` registers it, and the tools appear as `mcp__campus-customs__<tool>`.

### 3. Start the FastAPI backend (terminal 1)

```bash
cd backend
../.venv/bin/uvicorn main:app --reload --port 8000
```

The API is at http://localhost:8000, with docs at http://localhost:8000/docs. On startup it connects to the MCP server and rebuilds past run history from `output/audit_trail.json`.

### 4. Start the React board (terminal 2)

```bash
cd frontend
npm run dev
```

Open **http://localhost:5173**. The board talks to the backend at `http://localhost:8000` (the backend allows this origin through CORS).

### 5. Reset the database before a full three-ticket run

Always start a full run from the original data (rule 8). With the backend running, pick one of these. They all copy the original over the working copy:

- On the board, click **Reset data** (top right).
- `curl -X POST http://localhost:8000/api/reset`
- Step 1's `cp` command.

Then run the three tickets **one at a time, in order**:
1. Pick **ticket 101**, then **Start the team**. Watch each agent in the live feed until the ticket is stamped **Resolved**.
2. Under **Waiting on you**, type your name in *Approving as* and approve what the team posted. The checking balance drops only after a payment is approved.
3. Do the same for **102**, then **103**.

With the same approvals as the graded run (pay #501, pay rent, approve the discount, try the restock), checking goes **$3,400 → $2,560 → $160 → $160**, and the $264 restock is refused because cash can't cover it.

Each ticket run makes real `gpt-6-luna` calls (about 30k–65k tokens per ticket).

**Without the board:** `.venv/bin/python backend/team.py` resets the database itself, triages, and runs all three tickets in one go. Approvals then wait on the board or the API.

## Tests (offline: no model calls, temp database)

```bash
.venv/bin/python backend/tests/test_mcp_tools.py      # 49 checks: every tool, guardrail and money rule
.venv/bin/python backend/tests/test_team_offline.py   # 24 checks: the full agent loop with a scripted model
.venv/bin/python backend/tests/test_api.py            # 22 checks: every route, approve -> pay -> cash
```

The tests set `CAMPUS_CUSTOMS_WORK_DIR` to a temp folder, so they never touch `data/campus_customs_new.db`.

## Shop rules (enforced in the tools)

1. "Today" is `desk.date_today`, never the real clock.
2. Vendor lead times come from `vendors.lead_days`.
3. A vendor with an open invoice doesn't ship.
4. Every payment needs human approval.
5. A payment updates `payments`, the invoice or lease, and `cash_accounts` in one transaction.
6. A payment cash can't cover is refused.
7. Cash only goes out.
8. The working database is refreshed before each run.
9. No emails or calls. Drafts stay on the board.

## The resolved run (Problem 9)

| Ticket | Agents | Human step | Checking |
|---|---|---|---|
| 101 Bulldog tee | Inventory → Accounting → Customer Service | Paid invoice #501 ($840) | $3,400 → $2,560 |
| 102 Rent due | Facilities → Accounting | Paid rent ($2,400) | $2,560 → $160 |
| 103 Bulk hoodies | Inventory → Accounting → Customer Service | Approved the $55 discount. The $264 restock was refused (rule 6). | $160 → $160 |

The ending balance of **$160.00** matches `cash_accounts` in `data/campus_customs_new.db`. For the full story see `output/harness.md` (start with section 9, "The finished system at a glance") and `output/desk_tickets.html`.
