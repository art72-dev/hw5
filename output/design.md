# Campus Customs back office: dashboard design

The dashboard (`frontend/`, React + Vite + TypeScript) is where a person watches the agent team work a ticket and approves anything that costs money. It talks to the FastAPI backend at `http://localhost:8000` (`frontend/.env → VITE_API_URL`), and the backend allows the Vite origin, `http://localhost:5173`, through CORS.

```bash
# backend/   (terminal 1)
../.venv/bin/uvicorn main:app --reload --port 8000
# frontend/  (terminal 2)
npm install
npm run dev        # http://localhost:5173
```

## The look: a quiet back-office ledger

The brief was **neutral and earthy**. I designed it like the back room of a family-run shop: warm paper, brown ink, a ledger, rubber stamps. Not a flashy control room.

| Role | Colour | Where it shows up | Why |
|---|---|---|---|
| Paper | `#f4efe6` page, `#fbf8f2` cards | Background, panels | Warm and easy on the eyes through a long run. It feels like paper, not a screen. |
| Bark (ink) | `#2f2a24` text, `#4a3b2c` Boss | Headings, the Boss | A dark brown reads softer than black and still has strong contrast. |
| Clay / terracotta | `#a8593a` | "Start the team", focus rings | The one warm accent, saved for "do something." |
| Olive | `#5d6a40` | Resolved, "Approve & pay", receipts | Earthy green for done and safe to proceed. |
| Ochre | `#b0862c` | "Team working…", waiting approvals, guardrails | Harvest yellow for "needs attention" without alarm. |
| Rust | `#964532` | Errors, blocked, the cash-drop chip | Muted red for problems and money leaving. |
| Stone | `#8b8377` | System lines, quiet text | Neutral gray-brown for things that matter less. |

Each colour has **one meaning everywhere**. Olive always means done, ochre always means waiting on someone, and rust always means trouble or money going out. After one run you can read the screen by colour alone, though colour is never the *only* signal (see Accessibility).

**Type:**
- **Serif headings** (Iowan Old Style, falling back to Palatino or Georgia, all built into macOS and Windows with no web font to download) give it the ledger feel.
- A **system sans** body keeps the details legible.
- **Monospace** for tool names and arguments (`check_stock`, `CC-TEE-WHITE · S · 1`), so the machine parts look different from the human words.
- Money uses **tabular numbers**, so digits don't jump around as the balance counts down.

**Texture:** two very faint radial washes (clay top-left, olive top-right) on the page, and ruled lines on the ticket cards like an index card. No images and no web fonts, so it loads instantly.

## Layout

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ (CC) Campus Customs back office · desk date      CHECKING $3,400.00   [Reset]│  sticky
├───────────────┬────────────────────────────────────┬─────────────────────────┤
│ TICKETS       │ TICKET 101 · THE TEAM AT WORK      │ WHAT THE TEAM DID       │
│ ┌───────────┐ │  ── Ticket 101 handed to the Boss ──│  final call, crew,      │
│ │#101  Open │ │  (B) Boss → Inventory: brief       │  findings, next, tokens │
│ │Bulldog tee│ │   • Inventory checked the shelf    │                         │
│ │[Start the │ │     ↳ 0 on hand · short 1          │ WAITING ON YOU          │
│ │  team]    │ │  (In) Inventory: report            │  Approving as [name]    │
│ └───────────┘ │  (B) FINAL CALL memo               │  ┌ Payment $840.00 ┐    │
│ #102 · #103   │  (B) Boss is working…              │  └ [Approve & pay] ┘    │
│               │                                    │  Drafts (never sent)    │
└───────────────┴────────────────────────────────────┴─────────────────────────┘
```

The three columns follow the work from left to right: **pick a ticket → watch the team → decide**.

- **Left, Tickets.** All 3 tickets as index cards. You pick one here and press **Start the team**.
- **Center, The team at work.** The live feed is the biggest column, because it's what you watch.
- **Right, What the team did + Waiting on you.** The summary of the finished run, then the in-tray of approvals. The decisions sit next to the result that produced them.
- **Top bar.** The checking balance is **always visible** (the bar is sticky), because every approval changes it, and you should see the money move the moment you click.

It narrows from 3 columns to 2 on laptops (summary and approvals move underneath) and to 1 on phones. On phones the header becomes compact and stops being sticky, so it doesn't take up a third of the screen.

## Tickets: index cards

- Each card shows the ticket number in mono, a **status pill**, the subject, type and requester, and the requester's own words in serif italic (*"Needs a tee in size S."*), so the human side of the ticket stays visible.
- A **coloured edge** shows the stage: stone when open, ochre while the team works, olive when resolved, rust when blocked or failed.
- The pill **breathes** while the team works.
- **"N waiting on you"** appears in ochre when that ticket left approvals on the board.
- **When the run finishes, the ticket is marked resolved**, and a rotated olive **RESOLVED rubber stamp** lands on the card with a small press animation. It's the clearest sign that the agents are done, and it fits the back-office paper theme. The card leaves room so the stamp never covers the text.
- Only one run at a time: while a run is going, every "Start the team" button is disabled, with a tooltip saying why. That matches the backend's one-run rule.

## How the agents read differently

The center feed is built from the backend's audit events (`GET /api/events?run_id=…`, checked every second during a run). The goal is that you can tell **who** is talking and **what kind** of thing happened without reading every word.

**Every agent has a desk identity:**

| Agent | Monogram | Ink | Tagline | Voice on screen |
|---|---|---|---|---|
| Boss | **B** | bark brown | reads every ticket · makes the call | The **final call** is a bordered memo with a "FINAL CALL" label in larger serif. It's the one thing on screen that looks official. |
| Inventory | **In** | olive | counts the shelves | Short factual reports: what's on hand, what's short |
| Accounting | **Ac** | ochre-gold | watches every dollar | Reports in **tabular numbers**, so amounts line up like a ledger |
| Facilities | **Fa** | slate | minds the lease | Plain reports about the lease and dates |
| Customer Service | **CS** | clay | writes to customers | Reports in **serif italic**, like a letter, because this agent writes to people |

The monograms look like ink stamps (a coloured ring with initials), and each agent's notes sit on a pale wash of its own colour.

**Every kind of event has its own shape:**

- **Briefs:** "**Boss → Inventory**" with the brief underneath and a coloured rule on the left in the receiving agent's ink. You see the handoff happen.
- **Actions:** a small coloured dot, the agent's name and a **plain-English verb** ("checked the shelf", "asked a person to approve a payment", "drafted a reply"), followed by the arguments in mono. Plain words for people, the exact tool name still available for checking.
- **Results:** an indented "↳" line with a one-line summary ("0 on hand · short 1 · Aisle B", "On the board for approval: $840.00 · nothing paid yet"). Click it to see the raw tool output. The dashboard pulls these fields out even when the audit trail has cut a long result short.
- **Guardrails:** an ochre "GUARDRAIL" tag whenever a check sent an agent back, for example a customer draft that quoted an unapproved price, or an answer with a made-up board id. Showing them on purpose builds trust: you can *see* the safety checks working.
- **Reports:** a card in the agent's wash with its monogram, name, tagline and summary.
- **System lines:** small centered stone text between rules ("Ticket 101 handed to the Boss", "Run finished · N tokens").
- **Working:** "**Inventory** is working…" with animated dots at the bottom, so a quiet stretch never looks frozen.

The feed scrolls itself while a run is live and stops scrolling when it ends, so you can read back.

## Resolving a ticket

When the run finishes:
1. The ticket's pill turns olive **Resolved** and the stamp lands.
2. **What the team did** appears on the right:
   - the Boss's final call in serif
   - **crew chips** in each agent's ink: only the agents who actually worked, so a lean plan is visible (ticket 102 shows just Boss, Facilities and Accounting)
   - one line of findings per specialist, and what happens next
   - a footer with time taken, tokens used, and the status the ticket was marked
3. The in-tray refreshes with anything the team left for a person.

## Cash balance and approvals

**The in-tray ("Waiting on you").**
- The panel says plainly: *"Agents only prepare. Nothing is paid until a person approves here."*
- **"Approving as"** must be filled in before anything can be approved. Approvals are signed, and the backend refuses an agent's name. The name is remembered on this computer.
- Each item is a small card with a coloured top edge for its kind: **Payment** (ochre), **Purchase** (olive), **Discount** (stone). It shows who prepared it, the details (vendor, due date, days overdue, estimated cost, "on hold: vendor has an open invoice"), and the amount in large serif.
- Buttons say what they'll do: **"Approve & pay"** for payments and purchases, **"Approve price"** for a discount, which moves no money.
- When an approval is refused, the reason appears right on the card, word for word from the backend. For example: *"Cash can't cover this purchase: est. $264.00, $160.00 left…"* The person learns *why*, not just that it failed.
- Below the approvals: **draft replies, never sent** (dashed cards in serif italic, like letters on the desk), then a **Done** list showing who signed each item.

**The checking balance.**
- It sits top right in large serif, with "after waiting approvals $X" underneath, so you can see a payment coming before you approve it.
- When a payment is approved, the number **counts down** to the new balance over about a second, and a rust **"−$840.00"** chip rises above it and fades. A one-frame jump is easy to miss; a countdown is felt.
- A **receipt** appears at the top of the in-tray: *"Paid $840.00 for 'Pay invoice #501 to Bulldog Print Co' · checking $3,400.00 → $2,560.00 · signed …"*. The card itself leaves the queue, so the receipt keeps the before and after visible.
- The balance only ever comes from `GET /api/cash` and the approve route's reply (`cash_accounts.balance`). The dashboard never works money out on its own.

**Reset data** (top right, quiet button) puts the shop back to the original database after a confirmation. It's disabled while a run is going.

## Creative choices, and why

- **The ledger metaphor.** Index cards, ink stamps, rubber stamps, a day-book feed, an in-tray and receipts. Real objects from a shop's back office make an abstract multi-agent system feel familiar. Everyone knows what a stamped card or a signed receipt means.
- **Agents as desks, not chatbots.** Monograms and taglines ("counts the shelves", "watches every dollar") give each agent a job and a personality without cartoon avatars, which would clash with the calm tone.
- **Plain verbs over tool names.** "Checked the vendors" is readable by the shop owner; `check_vendor_status` stays visible in mono for anyone checking.
- **Guardrails shown, not hidden.** Seeing "sent back" moments makes the system more trustworthy.
- **Motion only where it means something.** The stamp landing (done), the pill breathing (working), the dots (thinking) and the cash counting down (money moved). Nothing moves just for decoration. Anyone who prefers reduced motion gets none of it.
- **The crew chips show the plan was lean.** Seeing only the agents who were needed backs up the "don't ask every specialist" strategy from the plan.

## Accessibility

- Status is never shown by colour alone. Every pill has words ("Resolved", "Team working…"), the stamp says RESOLVED, and refusals are written out.
- The feed is an `aria-live` region, so new agent steps are announced. Ticket cards can be selected with the keyboard (Tab, then Enter or Space), and every control has a visible clay focus ring.
- Text contrast stays high on the warm paper (bark on paper is well above WCAG AA). Muted colours are kept for supporting text.
- `prefers-reduced-motion` turns off every animation.

## Theme note

The project default for web apps is black and pink (AGENTS.md). This dashboard is **neutral and earthy because that's what was asked for here**. The default still applies elsewhere: Problem 6's plan page, `output/desk_tickets.html`, stays black and pink.

## Checked in the browser

With a demo backend (the real API with the scripted test model, so no tokens were spent and the real audit trail wasn't touched):
1. **Ticket 101:** the feed streamed Boss → Inventory → Accounting → Customer Service live. A guardrail showed when Customer Service was sent back. The ticket was stamped **Resolved** with a summary. Approving #501 counted checking down from **$3,400 to $2,560**, with the −$840 chip and a receipt.
2. **Ticket 102:** the crew was only Boss, Facilities and Accounting. Approving rent took checking from **$2,560 to $160**.
3. **Ticket 103:** a purchase and a discount waited on the board. Approving the $264 purchase was **refused** with the reason shown, because only $160 was left.

The phone layout has no sideways scrolling, and the only console message was that expected 409. Afterwards the database was reset to the original and the real backend restarted.
