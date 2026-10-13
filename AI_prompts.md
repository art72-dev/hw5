# Campus Customs Multi-Agent Operations — Work Log

## Project goal

Build a multi-agent operations system for **Campus Customs** (Yale's merch shop) that runs the shop's back office: customer orders, rent, unpaid vendor bills and anything else the shop has to handle. Three pieces:

- **MCP server:** tools that read and update the shop database
- **FastAPI backend:** a multi-agent team (Boss, Inventory, Accounting, Facilities, Customer Service, and others) that works through the shop's tickets
- **React dashboard:** where a human tracks what the agents are doing and approves requests

**Kickoff prompt:** "Ok we're working on a campus customs multi agent operations something that runs keeps records of customers orders, rent, unpaid bills whatever the shop has to handle. We're building 3 pieces today- MCP server, FastAPI backend with a multi agent team and a react dashboard so a human can track the agents and approve requests. The agent team has connectivity of Boss, Inventory, Accounting, Facilities, Customer service, etc. I have the data we need in our homework 5 file. We might have to make a copy of the database file called the data/campus_customs_new.db to point our server and backend to the wrokign copy. A couple of rules we're going to abide by- desk.date_today is today to dertermine the overdue. vendor leads comes from the vendors table. Vendors don't ship products when they have an open unpaid invoice. Human approval is required for any payment. Update the relevant table when payment is made if not points will be deducted. If there's no cash balance, the pay tool must refuse the. Cash only goes out, no money comes in. Before a run to resolve the issue, the database should be refreshed. Do not email the customers or call vendors , drafts stay on board. Strictly use gpt-6-luna only for this homework. Ok that's the gist so we'll build this 1 problem at a time. Follow my instructions!"

## Working rules

- Keep project work in the `Homework 5` folder.
- The original data is `data/campus_customs.db`. The MCP server and backend point at a working copy, `data/campus_customs_new.db`.
- Use OpenAI through Portkey with the `gpt-6-luna` model for this homework.
- Keep `PORTKEY_API_KEY` in the workspace-root `.env`. Never hard-code, print or commit it.
- Style the dashboard with a black-and-pink visual theme.
- Build one step at a time, one problem per step.

## Shop rules the agents must follow

1. **Today is `desk.date_today`** (2026-08-31 in the data), not the real clock. Overdue means "due before `desk.date_today`."
2. **Vendor lead times** come from `vendors.lead_days`.
3. **A vendor doesn't ship** while it has an open unpaid invoice.
4. **Every payment needs human approval.**
5. **A payment updates every related table:** a new row in `payments`, the invoice marked paid (or the lease's `next_due` moved forward), and a lower `cash_accounts.balance`. Missing updates cost points.
6. **The pay tool refuses** any payment the cash balance can't cover.
7. **Cash only goes out.** No money comes in.
8. **Refresh the working database** from the original before each resolve run.
9. **No emails to customers and no calls to vendors.** Drafts stay on the board.

## Problems and prompts

For each problem, record the problem number and title, the prompt I gave, a follow-up prompt if one was needed, and the result/evidence.

### Problem 1: Vibe coder prompts

- **Prompt:** "Problem 1-Vibe coder prompts. Create the AI_prompts .md and update it as we work like we did for the other assignments."
- **Follow-up prompt:** None needed.
- **Result / evidence:** `AI_prompts.md` (this file), set up like the earlier homework logs: project goal, kickoff prompt, working rules, the shop rules the agents must follow, and one entry per problem with the prompt, follow-up and result. It is updated after each prompt.

### Problem 2: Study the Campus Customs database

- **Prompt:** "Problem 2- Study the campus customs database. Let's open the data/campus_customs.db and look through the tables and fields. Copy this to the data/campus_customs_new.db file, we'll update this as we go!  Let's study the 3 open tickets and see how they link to other tables. Start the output/harness.md. List the fields and one short line on why that matters for the agent. We'll keep updating the file as we go"
- **Follow-up prompt:** None needed.
- **Result / evidence:**
  - `data/campus_customs_new.db`: working copy of the original, confirmed identical (same SHA-256, integrity check ok). The original stays untouched as the refresh source.
  - `output/harness.md` started with section 1, "The database":
    - **9 tables:** `desk`, `cash_accounts`, `vendors`, `invoices`, `leases`, `payments` (empty), `pricing`, `inventory`, `tickets`. Every field is listed with an example value and one line on why it matters for the agent.
    - **Links:** 3 real foreign keys (ticket → invoice, ticket → lease, invoice → vendor) plus soft links (ticket SKU/size → inventory and pricing; payment `ref_id` → invoice or lease).
    - **Ticket 101 (Tauhid, tee size S):** 0 in stock. The reprint is billed on invoice #501 ($840, Bulldog Print Co), which is 3 days overdue, so the vendor won't ship until it's paid. Lead time is 5 days.
    - **Ticket 102 (rent):** $2,400 is due 2026-09-02, 2 days away, so it isn't overdue.
    - **Ticket 103 (Yale AI Club, 20 hoodies size M):** 8 in stock, 12 short. There's no discount rule, so pricing is a human call (cost $22, list $58). The restock is also blocked by #501.
    - **Cash:** $3,400. Paying #501 and the rent leaves $160.
    - **Gaps:** no SKU-to-vendor link, no discount policy, no "on order" stock, no set labels for `payments.kind`, an out-of-stock mug with no ticket.

### Problem 3: Build the MCP server

- **Prompt:** "Problem 3- Build the MCP server. Make an mcp_server/ using the FastMCP. It will talk to data/campus_customs_new.db. Don't connect it or use it yet but every agent in the homework will end up using the tools in the MCP server. For now let's write 3 rules that you might need for the tickets in the database. Do not invent new data, use what you have. in the output/harness.md list these 3 tools and iclude which tools reads which tickets it helps unlock and a sentence on why that tool is the right one for that one. Don't be vague, tie the tool to the ticket. In the mcp_server/README.md file that explains what the MCP server is for and which database file it uses and the 3 tools it has!"
- **Follow-up prompt:** "use gpt-6-luna! and make sure the tools are consistent with the rules I mentioned earlier"
  - **Model:** confirmed `gpt-6-luna` for every AI call in this homework, now stated at the top of `output/harness.md`.
  - **Rules check:** went through all 3 tools against all 9 shop rules and fixed 4 gaps:
    - **Rule 6:** `check_vendor_status` only showed the raw balance. Each open invoice now gets the same `cash_check` (`covers`, `balance_after`) as rent.
    - **Rule 7:** `check_stock`'s `list_total` looked like income. It's now `quote_at_list` with a "quote only" note.
    - **Rules 3, 4 and 9:** "any open invoice blocks, even if not yet due," "payments need human approval" and "drafts stay on the board" are now in the server instructions and tool descriptions that agents read.
    - **Rule 1:** an empty `desk` returns a clear error instead of crashing, with no fallback to the real clock.
  - **Tested:** 17/17 checks passed. The edge cases ran on a throwaway copy: an open invoice due 9/20 still blocks the vendor, $0 cash shows `covers: false`, rent due 8/25 shows overdue by 6, rent due 8/31 shows "due today," empty `desk` and a missing database return errors. The real database files were unchanged.
  - **Docs:** `output/harness.md` now starts with the model and the 9 shop rules, and section 2 has a rules-check table (rule, how the tools follow it, test). `mcp_server/README.md` has the same rule-by-rule table.
- **Result / evidence:**
  - `mcp_server/server.py` (FastMCP 4): 3 read-only tools that open `data/campus_customs_new.db` in read-only mode. "Today" comes from `desk.date_today`. No invented data: an unknown SKU, size, vendor or lease returns an error that lists what's on file.
    - `check_stock(sku, size, qty_needed)` reads `inventory` and `pricing`. Ticket 101: tee S has 0, short 1. Ticket 103: hoodie M has 8, short 12, $22 cost and $58 list.
    - `check_vendor_status(vendor_id)` reads `vendors`, `invoices`, `desk` and `cash_accounts`. Bulldog Print Co can't ship: invoice #501, $840, 3 days overdue, 5 lead days. This blocks tickets 101 and 103.
    - `check_rent_due(lease_id)` reads `leases`, `desk` and `cash_accounts`. Ticket 102: $2,400 due in 2 days, not overdue, and checking covers it.
  - `mcp_server/README.md`: what the server is for, which database file it uses (and that it never opens the original), the 3 tools, the rules built in, and how to run it.
  - `output/harness.md` section 2: the 3 tools, what each reads, which tickets each helps unlock, and one tool-to-ticket row per pair with the real call, the real result and why that tool fits that ticket.
  - `requirements.txt`: `fastmcp`.
  - **Checked:** tested in-process with an in-memory FastMCP client. All 3 tools return the values above, and bad inputs (size XXL, unknown SKU, qty 0, vendor 99, lease 9) return clear errors. The database checksum is the same before and after. Not connected to any agent or `.mcp.json`.

### Problem 4: Add the MCP server to the vibe coder and test each tool

- **Prompt:** "Problem 4- Add the MCP server to vibe coder and test each tool. Lets add the MCP server to this project so that the you can call the tools. Keep record of the JSON text in mcp.json at the project root. Let's also test these 3 rules and save the evidence in output/mcp_smoke.json. For each tool, include the prompt that made it, tool name and an output that matches in the data/campus_customs_new.db"
- **Follow-up prompt:** None needed.
- **Result / evidence:**
  - **Registered** the server as `campus-customs`:
    - The JSON is saved in `mcp.json` at the Homework 5 root.
    - The same entry was added to the workspace `.mcp.json`, where Claude Code reads it. The two existing servers were left untouched.
    - It runs from `Homework 5/.venv` (Python 3.10, FastMCP 4.1.0) over stdio, with the startup banner turned off.
  - **`mcp_server/smoke_test.py`:** starts the server from the `mcp.json` entry over stdio, lists its 3 tools, and calls each one for a real ticket. It then checks every value against `data/campus_customs_new.db` with its own SQL (days overdue and balance-after are computed in SQL).
  - **`output/mcp_smoke.json`:** for each tool, the prompt that made the call, the tool name, the arguments, the full output, the SQL and database row, and a field-by-field comparison.
    - `check_stock`, ticket 101: tee S has 0 on hand, short 1, Aisle B, $8 / $28. 6/6 fields match.
    - `check_vendor_status`, ticket 101: Bulldog Print Co can't ship. #501, $840, 3 days overdue, 5 lead days, $2,560 left after. 8/8 match.
    - `check_rent_due`, ticket 102: $2,400 due 9/2, due in 2 days, not overdue, $1,000 left after. 7/7 match.
    - **3/3 tools passed, 21/21 fields matched, and the database was unchanged.** The 17 rule checks still pass on FastMCP 4.1.0.
  - **Docs:** `output/harness.md` section 3 (config plus a smoke-test table) and `mcp_server/README.md` ("Connect it to Claude Code" and "Smoke test").
  - **Note:** this session started before the server was added, so Claude Code hadn't loaded `campus-customs` yet. The smoke calls went through a stdio MCP client using the same `mcp.json` command, recorded as `called_via` in the JSON.

### Problem 5: Build the agent team and grow the MCP tools

- **Prompt:** "Problem 5- Build the agent team and grow the MCP tools. Let's build the agentic tools using PydanticAI. Boss, That reads every ticket, decides who should wok on it and makes the final call. Inventory checks the stocks by SKU and size, reads shortfalls and figures out which agent to vendor to contact. Accounting that oversees the cash, invoice, margins and payment orders that needs human approval. Facilities that handles all the monthly space side like lease, rent, etc. and also the customer srvice that drafts the messages for the customers. Unsure full connectivity so include prompts that are needed for agent loops and creating models. Put all the agent prompts in the backend/prompts/ - one single file per agent, data types in backend/models.py and the agent files under backend/. Build them as detailed as possible, do not skip any business function. Analyse all the open tickets and any tools that are needed as a part of the agent so the MCP server that work well. facts come from data/campus_customs_new.db. Do not invent any second shop-tools layer that bypasses the MCP.  Make it append the output/audit_trail.json as they run for each agent- loop step and record the audit later. In the output/harness.md, list each agent and MCP tools along with the table each tool uses. Add the safety section- include guardrails that a real business would need when the agent wants to communicate with the customer and real money and make sure not too many tokens are being used. Also update the mcp_server/README.md so the tools list match what we have up till noe"
- **Follow-up prompt:** None needed.
- **Result / evidence:**
  - **MCP server grown from 3 to 16 tools**, worked out from what the 3 open tickets need:
    - **8 read tools:** `list_open_tickets`, `get_ticket`, `check_stock`, `check_vendor_status`, `check_rent_due`, `get_cash_position`, `check_margin`, `get_board`.
    - **5 board tools,** where agents post and nothing is sent or paid: `request_payment`, `request_price_override`, `draft_vendor_order`, `draft_customer_message`, `update_ticket`.
    - **3 human-only tools:** `decide_request`, `pay_approved_request` (writes `payments`, the invoice or lease, `cash_accounts` and `board` in one transaction) and `refresh_working_copy`.
    - One new table, `board`, in the working copy only, holds drafts and approval requests.
  - **Agent team (PydanticAI 2.49, `gpt-6-luna` through Portkey):**
    - Files: `backend/agents/` has `boss.py` (Boss plus triage), `inventory.py`, `accounting.py`, `facilities.py` and `customer_service.py`, with shared pieces in `base.py`. Prompts are one file per agent in `backend/prompts/`, and every data type is in `backend/models.py`.
    - **Connectivity:** hub and spoke. The Boss reaches each specialist through `ask_*` tools. Each agent gets a filtered slice of the same MCP server, and there is no second tools layer. Even the refresh before a run is an MCP call.
    - **Output checks:** each agent's output is checked against its own tool results before it's accepted, so it can't invent numbers or board ids.
  - **`backend/team.py` runner:** refresh, Boss triage (picks only the specialists each ticket needs), then one Boss run per ticket. `--check` tests connectivity without model calls.
  - **`backend/audit.py`:** appends every agent-loop step to `output/audit_trail.json` as it happens. Entries are redacted and never rewritten. The file is created on the first real run, so the audit gets recorded later.
  - **`output/harness.md`:**
    - Section 4: the folder layout, the connection diagram, each agent with its MCP tools, output and output check, and every tool with the tables it reads and writes and who uses it.
    - Why each new tool exists, ticket by ticket, and a rules check for the new tools.
    - Section 5, Safety: guardrails for customer messages, real money and token use.
  - **Other files:**
    - `mcp_server/README.md` rewritten for all 16 tools.
    - `requirements.txt` adds `pydantic-ai-slim[openai,mcp]` and `python-dotenv`.
  - **Tested:**
    - `team.py --check`: all 16 tools present, and no human-only tool in any agent.
    - `tests/test_mcp_tools.py`: **45/45.** Covers every guardrail, approve → pay updating all 4 tables, double-pay refused, and rule 6 at request and at pay time.
    - `tests/test_team_offline.py`: **24/24.** A scripted model ran the full loop on all 3 tickets through the real MCP server. One request for #501 served both 101 and 103, the tee wasn't re-ordered, no money moved, and 3 forced mistakes were caught.
    - **Live ping:** `gpt-6-luna` answered (24 tokens).
    - **Database:** the working copy is identical to the original afterwards.

### Problem 6: Plan the three tickets

- **Prompt:** "Problem 6- Plan the three tickets. Let's build the output/desk_tickets.html, a page that you can double click with one tab per ticket. What I expect the team to do on every open ticket- What is the problem, how could we resolve it and how much time would that take. Add the Cash and Reflection tabs for later problems- leave them with a "coming later" note for now. On each ticket tab, an Expected section only. Leave room for an actual section that we will fill in after we run the agents. In the Expected section include who the boss would call first and why, all the agents to called to delegate with a strategic approach and an MCP tool that expects to run and use. Plans that select every specialist on every ticket score low. We'll compare the plan to what actually happens when the agent resolves the 3 tickets."
- **Follow-up prompt:** None needed. (This message arrived while Problem 5 was being finished, so Problem 5 was completed first.)
- **Result / evidence:**
  - **The page:** `output/desk_tickets.html` is a single self-contained file (no internet, no server) in the black-and-pink theme.
    - **Tabs:** Ticket 101, Ticket 102, Ticket 103, Cash and Reflection. Cash and Reflection say "coming later."
    - **Header:** desk date, cash, and the Boss's triage step. The expected order is **101 → 102 → 103**: 101 holds the overdue invoice #501, which unblocks 101 and 103; rent is due in 2 days; and 103 reuses 101's #501 request.
  - **Each ticket tab's Expected section** covers:
    - The problem, with numbers from the database.
    - How we'd resolve it, step by step.
    - How long it takes: the agent run (estimate), the human step, and the real-world date.
    - **Who the Boss calls first and why.**
    - The delegation route, with the **skipped specialists and why**.
    - Every MCP tool we expect each agent to run, with its expected result.
    - The expected end state.
  - **Strategic delegation: 8 of 12** possible specialist calls:
    - **101:** Inventory → Accounting → Customer Service. Facilities is skipped. Inventory deliberately does *not* draft a vendor order, because #501 already covers the tee.
    - **102:** Facilities → Accounting. Inventory and Customer Service are skipped, since the landlord isn't a customer.
    - **103:** Inventory → Accounting → Customer Service. Facilities is skipped. Accounting reuses #501's request and flags that the ~$264 restock won't fit in the $160 left after #501 and rent.
  - **Actual section:** each ticket tab has an empty dashed section with an Expected-vs-Actual table (Boss called first, specialists, MCP tools, board items, final status, time, requests and tokens), ready to fill in after the agents run.
  - **Checked in the browser:**
    - Tabs work by click and by arrow, Home and End keys, and the URL hash (`#t103`) opens the right tab.
    - No console errors and no sideways scrolling.
    - On mobile, the tab bar now stays one swipeable row after a fix.
    - The file has no external links and its HTML is well-formed.
    - A `hw5-output` static server was added to `.claude/launch.json` for previewing.

### Problem 7: Backend routes

- **Prompt:** "Problem 7- backend routes. In the backend/main.py use the FASTAPI and routes that return the 3 tickets, whether each is open or resolved, take a ticket ID and run an agent on the team, return recent agent events- what each gent said and what tools they used so that board refreshed approve a payment or purchase after the human clicks approve (agent only preps the pay , route is what changes cash), Return the current checking balance from cash_acoounts nd reset the background to the orginal values when you want to run a refresh. Let's start the backend/ folder with uvicorn main:app --reload --port 8000. In the output/harness.md list each route in one line( what URL or what does it do)"
- **Follow-up prompt:** Port 8000 was already taken by the Homework 4 backend. Asked how to proceed, and the answer was "Stop HW4, use 8000."
- **Result / evidence:**
  - **`backend/main.py`:** FastAPI, all shop access through the campus-customs MCP server (one client opened when the app starts). 8 routes:
    - `GET /api/tickets`: the 3 tickets, open or resolved.
    - `POST /api/tickets/{id}/run`: runs the team on one ticket in the background (202). One run at a time.
    - `GET /api/runs/{run_id}`: a run's status and the Boss's decision.
    - `GET /api/events`: what each agent said and which tools it used, newest first.
    - `GET /api/board`: what the agents prepared.
    - `POST /api/board/{id}/approve`: a person approves. Payments and purchases are paid here; **the only place cash changes.**
    - `GET /api/cash`: the checking balance from `cash_accounts`.
    - `POST /api/reset`: back to the original database.
  - **MCP server (17 tools):**
    - New `list_tickets` read tool for the dashboard.
    - `decide_request` and `pay_approved_request` now also handle **purchases** (vendor drafts). A purchase needs the vendor to have no open invoice (rule 3) and cash after waiting requests (rule 6). It's recorded as `payments.kind = 'purchase'`.
  - **Runner:** `run_team` now supports one-ticket runs for the API, without triage and without a second refresh. The first API run always refreshes (rule 8). The Boss prompt says to pick only the needed specialists when there's no triage plan.
  - **Audit:** people's approvals and resets are now in `output/audit_trail.json` too (`human_decision` and `reset`).
  - **`output/harness.md` section 6:** each route in one line, plus how runs, refreshes and approvals fit together, and the missing login.
  - **`mcp_server/README.md`** updated for 17 tools. `requirements.txt` adds `fastapi` and `uvicorn`.
  - **Tested:**
    - **API (`tests/test_api.py`, scripted model): 21/21.** Reset, run 101, approve #501 ($3,400 → $2,560), approve rent (→ $160), approve the 103 price with no cash change, the $264 purchase refused, then reset back to $3,400.
    - **MCP tools: 49/49**, now including a purchase paid for $264 after #501.
    - **Offline team: 24/24. Smoke: 3/3.** `--check` ok.
  - **Started:** stopped the old Homework 4 uvicorn (as chosen) and started Homework 5 on **port 8000** (`hw5-backend` in `.claude/launch.json`, run as `python -m uvicorn` because the preview launcher can't run the venv's `uvicorn` script).
  - **Checked live:**
    - `/docs` lists all 8 routes.
    - Reset matches the original. Tickets are 101, 102 and 103, all open. Cash is $3,400. The board is empty.
    - Bad ticket, run and board ids return 404 with clear messages.
    - The live reset wrote the first audit entry (`human` / `reset`).
    - No live agent run yet: that uses `gpt-6-luna` tokens and is saved for the real run.

### Problem 8: Agent dashboard

- **Prompt:** "Problem- 8 Agent dashboard. Let's build the front end dashboard in frontend/ with React + Vite + TypeScript. Call the agents you need from problem 7. the board should all all 3 tickets, pick one ticket and let the agent team start working on it,  show what each agent says as it performs the task, mark the ticket resolved when the run finishes, short summary on what the agent did on that ticket, let a human approve a [ay when purchasing and show the checking balance( drop after the pay is approved). Keep the dashboard design neutral and earthy. Tell the front end talk to the backend at http://localhost8000. On the backend, allow Vite origin so the browser can call those routes. npm run dev Write in the output/design.md the look we went for with our dashboard. The layout, how agents and read differently and resolve the tickets, cash balance, etc. and why. Including the creative choices."
- **Follow-up prompt:** Port 5173 was taken by the Homework 4 frontend. Asked, and the answer was "Stop HW4 frontend, use 5173."
- **Result / evidence:**
  - **`frontend/`** (React 19 + Vite 8 + TypeScript, strict), run with `npm run dev` on http://localhost:5173. It talks to `http://localhost:8000` through `frontend/.env` (`VITE_API_URL`).
    - **Tickets:** all 3 as index cards. Pick one, then **Start the team**. One run at a time.
    - **Live feed:** polls `/api/events` every second.
      - Each agent has its own monogram, ink colour, tagline and voice.
      - It shows Boss → specialist briefs, plain-English tool steps, one-line results, guardrail send-backs, reports, and the Boss's "Final call" memo.
    - **When the run finishes:** the ticket flips to **Resolved** with a rubber-stamp animation, and **What the team did** shows the final call, the crew, the findings, the next steps, the time and the tokens.
    - **Waiting on you:** signed approvals ("Approving as"). "Approve & pay" for payments and purchases, "Approve price" for discounts. Refusals show the backend's reason, and a receipt shows checking before → after. Draft replies are shown and marked "never sent."
    - **Checking balance:** always visible in the header. It **counts down** when a payment is approved, with a "−$840.00" chip.
  - **Backend:**
    - CORS allows the Vite origin (`VITE_ORIGINS` = `localhost:5173` and `127.0.0.1:5173`). A preflight check from 5173 passes, and other origins get no access header.
    - **A finished run now marks the ticket `resolved`.** The Boss ends `resolved` (approvals stay on the board) or `blocked`, enforced by its output check.
    - `update_ticket` allows `resolved` with approvals still waiting and returns them.
    - The run route refuses a resolved ticket until a reset.
    - Tests updated: **MCP 49/49, offline team 24/24, API 22/22.**
  - **`output/design.md`:** the look (a quiet back-office ledger: paper, bark, clay, olive, ochre, rust, stone, each colour with one meaning), the layout, how agents read differently, resolving tickets, cash and approvals, the creative choices, accessibility, and the theme note.
  - **Other docs:**
    - `output/harness.md` section 7 covers the dashboard and the status change.
    - `output/desk_tickets.html` now expects `resolved` as the final status, with a footnote.
    - `mcp_server/README.md` is updated.
  - **Checked in the browser** with a demo backend (`tests/demo_backend.py`: the real API with the scripted model, so no tokens and no writes to the real audit trail):
    - **101:** streamed live and stamped Resolved. Approving #501 took checking from $3,400 to **$2,560**.
    - **102:** the crew was Boss, Facilities and Accounting only. Approving rent took checking to **$160**.
    - **103:** the $264 purchase was refused with the reason shown.
    - Mobile layout fixed (compact header, no sideways scrolling).
    - Then the data was reset and the **real backend restarted on 8000**. The dashboard is on 5173 against it, with 3 open tickets and $3,400.

### Problem 9: Resolve the tickets

- **Prompt:** "Problem 9- Resolve the ticket.  Before we run a full run on the tickets but first reset the wokring database data/campus_customs_new.db again so we start clean. State the starting cash balance and run all 3 tickets until resolved. Open the output/desk_tickets.html and on each ticket tab fill the actual section that we kept blank with what agent worked, what tool was called upon, etc. Let's compare the expected section and this actual section later. On the Cash section of the output/desk_tickets.html, show me what the starting balance is, how the cash moves after each of these tickets and the ending balance is after each( it must match the cash_accounts in the working database. Save the output/resolved_tickets.json for each ticket include id, final status, short outcome, what agent contributed and the human approval needed. In the output/resolved_board.html save the screenshots of the React board for each resolved ticket. Append the runs to output/audit_trail.json and finish the output/harness.md with the tools, five agents, API routes, dashboard and the safety rules"
- **Follow-up prompts:**
  - **Approvals:** asked who approves payments. Answer: "You click, signed as me." For ticket 103: "Approve discount, try purchase."
  - **Restore:** after my test run wiped the working database, asked how to restore. Answer: "Re-run all 3 for real."
- **Result / evidence:**
  - **Reset → starting balance $3,400.00** (`cash_accounts` checking, working copy identical to the original).
  - **All 3 tickets resolved by the real `gpt-6-luna` team, from the dashboard:**
    - **101:** Inventory → Accounting → Customer Service. #501 approved and paid, $3,400 → **$2,560**.
    - **102:** Facilities → Accounting. Rent approved and paid, → **$160**, lease next due 2026-10-02.
    - **103:** Inventory → Accounting → Customer Service. The $55 discount was approved with no cash moved; the $264 purchase was **refused** by rule 6. Stays **$160**.
    - **Totals:** 8 of 12 specialist calls, as planned. 152,596 tokens.
  - **Approvals:** signed "Anjali (OK'd in chat, clicked by Claude)".
  - **Ending balance $160.00 = `cash_accounts`.** 2 payments, all tickets resolved.
  - **`output/desk_tickets.html`:** each ticket's **Actual** section filled in next to Expected: who the Boss called, specialists, MCP tools per agent, board items, status, human approval, time, requests and tokens. The **Cash** tab shows start, ticket-by-ticket and step-by-step movement, and the end, with a read-only check against the database.
  - **`output/resolved_tickets.json`:** per ticket: id, final status, outcome, each agent's contribution and tools, human approvals and their results, cash before and after, run stats.
  - **`output/resolved_board.html`:** 6 dashboard screenshots (resolved, and after approval, for each ticket), embedded so the page works offline. Taken with `frontend/scripts/snap_board.cjs` (headless Chrome, `puppeteer-core`).
  - **`output/audit_trail.json`:** appended by the backend. Now 264 entries: both runs plus 8 human decisions.
  - **`output/harness.md`:** finished with a contents line, section 8 (the run, the incident and the fixes) and section 9 (a one-page summary of the tools, five agents, API routes, dashboard and safety rules).
  - **`scripts/build_run_report.py`:** rebuilds the JSON and the screenshot page from the API, the MCP server and the audit trail.
  - **Incident and fixes:**
    - **What happened:** the first full run succeeded, but running the offline tests afterwards reset the real working database.
    - **Fix 1:** tests and the demo now use a temp database (`CAMPUS_CUSTOMS_WORK_DIR`), verified by an unchanged modification time.
    - **Fix 2:** the API rebuilds run history from the audit trail after a restart.
    - **Fix 3:** refused approval attempts are now logged.
    - **Then:** the full run was redone from a clean reset, and every report uses that second run.

### Problem 10: Reflection

- **Prompt:** "Problem 10- Reflection. Let's open output/desk_tickets.html and work on the reflection tab. How would I evaluate the performance of the agents on each ticket- I would say all the tickets correctly call the right agents to help them solve the problem, elaborate on why. For each ticket, how did the actual compare to each ticket and why? What would have been simpler as one agent with tools and why? 3 new problems that campus customs might face that the agent team could solve with the tools that were build- I would say it could answer chat bot questions with the customer service agent, automate placing orders on specific high demand items ofcourse place only after human approval, remind to pay accounts payable. 3 new problems campus customs might face that the agent could not solve with the tools we build and explain tool and agent that are needed to be solving those."
- **Follow-up prompts:**
  - "Tie all our answers and observations back to campus groups tickets and agents and our website"
  - "let's only focus on homework 5"
  - **What changed:**
    - **Trace lines:** every finding in the Reflection tab (17 cards) now opens with three parts: the **ticket** it comes from (101 Tauhid, 102 the landlord, 103 Yale AI Club, a campus group), the **agents** involved, and **where it shows on the dashboard** (the Resolved stamp, live feed, crew chips, Payment and Purchase cards, draft replies, the checking balance).
    - **Section 6, "Tying it together":** a table with one row per ticket (who asked, agents in order, what they left for a person, where it showed, what the person did, the lesson), and a table showing which ticket each next idea grows from, its agents, the dashboard change it needs, and whether today's tools cover it.
    - **Homework 5 only:** "website" means the Homework 5 dashboard. All mentions of the Homework 4 store site and its chat assistant were removed.
- **Result / evidence:** the **Reflection** tab in `output/desk_tickets.html` is filled in, using numbers from `output/audit_trail.json` and `output/resolved_tickets.json`.
  1. **How the agents performed:**
     - A scorecard per ticket: right specialists, right order, rules kept, money safety, customer safety, effort against the plan.
     - The verdict: **every ticket called the right agents, and only those** (8 of 12 calls). The routing was identical in both full runs, with the same $55 proposal.
     - Why each ticket's specialists were right.
     - What could be better: `get_ticket` ran 10 times for 3 tickets; the Boss's coordination was 35% of requests; a reply rewrite in run 1 cost +21k tokens; there's no discount policy.
  2. **Plan vs. actual per ticket**, with what matched, what differed and why:
     - **101:** matched. A few more requests than planned, because each agent re-reads the ticket.
     - **102:** an exact match.
     - **103:** the restock draft wasn't on hold and there was no #501 request, because #501 had been approved and paid right after 101. The price was $55 rather than the predicted $49–52.
     - **Overall:** cash exactly as forecast ($3,400 → $2,560 → $160 → $160), and 86 s for all three against about 4–5 minutes planned.
  3. **One agent with tools?**
     - **Yes** for 102 (about 5 requests against 12), **borderline** for 101, **no** for 103 (three jobs; mixing them risks leaking internal facts to the customer).
     - The trade-off is separation of duties, focused prompts, per-role checks and a clear audit trail.
     - The recommendation: route by complexity.
  4. **Three problems the team can solve with today's tools:**
     - A customer-question chatbot through Customer Service.
     - Reordering high-demand items, only after a person approves.
     - Accounts-payable reminders through Accounting.
     - Each comes with the exact tools, the safety guards and proof from this run.
  5. **Three problems it can't solve:**
     - **Receiving goods and fulfilling orders:** a Receiving agent with `receive_shipment`, `reserve_stock`/`fulfill_order` and `advance_desk_date`.
     - **Money coming in:** a Sales & Billing agent with orders, invoices, payments received and refunds, plus a Planning agent with a cash and demand forecast. Needs a change to rule 7.
     - **Talking to the outside world safely:** a Communications agent with `send_approved_message`, `ingest_inbox`, `track_shipment` and `verify_payee_change`.
  - **Checked in the browser:** all 5 sections render, the tab label reads "how it went", there's no sideways scrolling, and the HTML is well-formed.
