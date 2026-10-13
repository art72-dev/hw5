"""Campus Customs ops API (FastAPI): tickets, agent runs, agent events, approvals, cash and reset.

Run from backend/:
    ../.venv/bin/uvicorn main:app --reload --port 8000
Docs: http://127.0.0.1:8000/docs

Every shop read and write goes through the campus-customs MCP server, the
same server the agents use, so this file never opens the database. Agents
only prepare payments (board requests); the approve route is what changes cash.
"""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from agents.base import MODEL_NAME, mcp_client
from audit import AuditTrail
from team import run_team

PAYABLE = ("payment_request", "vendor_draft")  # approving these moves cash; a price override doesn't


class RunRequest(BaseModel):
    refresh: bool = Field(False, description="Refresh the working database first (rule 8). The first run after the "
                                             "API starts always refreshes, even if this is false.")


class ApproveRequest(BaseModel):
    approved_by: str = Field(min_length=2, max_length=60, description="The person approving. Never an agent.")
    note: str | None = Field(None, max_length=400)


def _decision_from_events(events: list[dict], ticket_id: int, detail: dict) -> dict:
    """Best-effort Boss decision for runs logged before decisions were stored in ticket_end (text is shortened)."""
    mine = [e for e in events if e["ticket_id"] == ticket_id]
    boss_end = next((e for e in reversed(mine) if e["agent"] == "Boss" and e["event"] == "agent_end"), None)
    match = re.match(r"^\[(\w+)\]\s*(.*)$", (boss_end or {}).get("text") or "", re.S)
    reports = {e["agent"]: e["text"] for e in mine if e["event"] == "agent_end" and e["agent"] != "Boss" and e["text"]}
    return {"ticket_id": ticket_id, "ticket_type": "", "worked_by": list((detail.get("delegations") or {}).keys()),
            "findings": [f"{agent}: {text}" for agent, text in reports.items()],
            "final_call": match.group(2) if match else "", "status": match.group(1) if match else detail.get("status"),
            "needs_human": [], "customer_draft_board_id": None, "next_steps": [], "rebuilt_from_audit": True}


def runs_from_audit(trail: list[dict]) -> dict[str, dict]:
    """Rebuild run records from the append-only audit trail, so run history survives an API restart."""
    by_run: dict[str, list[dict]] = {}
    for e in trail:
        if e["run_id"] != "api":
            by_run.setdefault(e["run_id"], []).append(e)
    runs: dict[str, dict] = {}
    for run_id, events in by_run.items():
        start = next((e for e in events if e["event"] == "run_start"), {})
        end = next((e for e in events if e["event"] == "run_end"), {})
        ticket_ends = [e for e in events if e["event"] == "ticket_end"]
        for te in ticket_ends:
            ticket_id, detail, ok = te["ticket_id"], te["detail"] or {}, bool((te["detail"] or {}).get("ok"))
            began = next((e["timestamp"] for e in events if e["event"] == "ticket_start" and e["ticket_id"] == ticket_id),
                         start.get("timestamp", te["timestamp"]))
            key = run_id if len(ticket_ends) == 1 else f"{run_id}:{ticket_id}"
            tokens = (te["tokens"] or {}).get("ticket_total", 0)
            runs[key] = {
                "run_id": key, "ticket_id": ticket_id, "status": "done" if ok else "failed",
                "refresh": bool((start.get("detail") or {}).get("refresh")), "started_at": began,
                "finished_at": te["timestamp"], "error": None if ok else te["text"],
                "result": {"total_tokens": (end.get("tokens") or {}).get("run_total", tokens), "stopped_early": None,
                           "outcomes": [{"ticket_id": ticket_id, "ok": ok, "tokens": tokens, "error": None if ok else te["text"],
                                         "decision": (detail.get("decision") or _decision_from_events(events, ticket_id, detail)) if ok else None}]},
            }
    return runs


@asynccontextmanager
async def lifespan(app: FastAPI):
    path = app.state.audit.path
    if path.exists():
        app.state.runs.update(runs_from_audit(json.loads(path.read_text())))
    async with mcp_client() as client:
        app.state.mcp = client
        yield


app = FastAPI(title="Campus Customs Ops API", version="1.0", lifespan=lifespan,
              description=f"Multi-agent back office on {MODEL_NAME}. Agents prepare; people approve; the approve route moves cash.")
VITE_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]  # the dashboard (frontend/: npm run dev)
app.add_middleware(CORSMiddleware, allow_origins=VITE_ORIGINS, allow_methods=["GET", "POST"], allow_headers=["Content-Type"])
app.state.model = None           # None means gpt-6-luna; tests can swap in a scripted model
app.state.audit = AuditTrail()   # output/audit_trail.json
app.state.runs = {}              # run_id -> run record
app.state.tasks = {}             # run_id -> asyncio.Task (kept so tasks aren't garbage-collected)
app.state.active_run = None
app.state.last_refresh = None


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


async def mcp_call(tool: str, args: dict | None = None) -> Any:
    """Call one MCP tool. Tool errors become HTTP errors (unknown ids -> 404, bad input -> 400)."""
    result = await app.state.mcp.call_tool(tool, args or {}, raise_on_error=False)
    if result.is_error:
        message = result.content[0].text if result.content else f"{tool} failed"
        raise HTTPException(404 if message.startswith("No ") else 400, detail=message)
    return result.structured_content


async def checking_balance() -> dict:
    position = await mcp_call("get_cash_position")
    account = next((a for a in position["accounts"] if a["name"] == "checking"), None)
    if account is None:
        raise HTTPException(404, "No checking account in cash_accounts.")
    return {"account": "checking", "balance": account["balance"], "as_of": account["as_of"],
            "desk_date": position["today"], "pending_requests": position["totals"]["pending_requests"],
            "cash_after_pending": position["totals"]["cash_after_pending"]}


def last_run_for(ticket_id: int) -> dict | None:
    runs = [r for r in app.state.runs.values() if r["ticket_id"] == ticket_id]
    return {k: runs[-1][k] for k in ("run_id", "status", "started_at", "finished_at")} if runs else None


async def run_in_background(record: dict) -> None:
    try:
        report = await run_team([record["ticket_id"]], refresh=record["refresh"], triage=False,
                                run_id=record["run_id"], model=app.state.model, audit=app.state.audit)
        outcome = report.outcomes[0] if report.outcomes else None
        record["result"] = report.model_dump(mode="json")
        record["status"] = "done" if outcome and outcome.ok else "failed"
        record["error"] = None if outcome and outcome.ok else (outcome.error if outcome else report.stopped_early)
    except Exception as exc:
        record["status"], record["error"] = "failed", f"{type(exc).__name__}: {exc}"[:300]
    finally:
        record["finished_at"] = _now()
        app.state.active_run = None


# ---------------------------------------------------------------- routes


@app.get("/api/tickets")
async def list_tickets() -> dict:
    """The tickets, each marked open or resolved, plus the last agent run on it."""
    data = await mcp_call("list_tickets")
    for ticket in data["tickets"]:
        ticket["last_run"] = last_run_for(ticket["id"])
    return data


@app.post("/api/tickets/{ticket_id}/run", status_code=202)
async def run_ticket(ticket_id: int, body: RunRequest | None = None) -> dict:
    """Start the agent team on one ticket in the background. Poll /api/runs/{run_id} or /api/events."""
    ticket = (await mcp_call("get_ticket", {"ticket_id": ticket_id}))["ticket"]  # 404 if it doesn't exist
    if ticket["status"] == "resolved":
        raise HTTPException(409, f"Ticket {ticket_id} is already resolved. Reset to run it again.")
    if app.state.active_run:
        raise HTTPException(409, f"Run {app.state.active_run} is still going. One run at a time.")
    refresh = (body.refresh if body else False) or app.state.last_refresh is None
    run_id = uuid.uuid4().hex[:12]
    record = {"run_id": run_id, "ticket_id": ticket_id, "status": "running", "refresh": refresh,
              "started_at": _now(), "finished_at": None, "result": None, "error": None}
    app.state.runs[run_id] = record
    app.state.active_run = run_id
    if refresh:
        app.state.last_refresh = record["started_at"]
    app.state.tasks[run_id] = asyncio.create_task(run_in_background(record))
    return record


@app.get("/api/runs/{run_id}")
async def get_run(run_id: str) -> dict:
    """Status and result of one agent run (running, done or failed)."""
    if run_id not in app.state.runs:
        raise HTTPException(404, f"No run {run_id}.")
    return app.state.runs[run_id]


@app.get("/api/events")
async def recent_events(limit: int = Query(50, ge=1, le=500), ticket_id: int | None = None,
                        run_id: str | None = None, agent: str | None = None) -> dict:
    """Newest-first agent events from the audit trail: who acted, what they said, which tools they used."""
    path = app.state.audit.path
    trail = json.loads(path.read_text()) if path.exists() else []
    if run_id and ":" in run_id:  # one ticket out of a multi-ticket CLI run
        run_id, ticket_id = run_id.split(":")[0], int(run_id.split(":")[1])
    picked = [e for e in trail
              if (ticket_id is None or e["ticket_id"] == ticket_id)
              and (run_id is None or e["run_id"] == run_id)
              and (agent is None or e["agent"].lower() == agent.lower())]
    events = [{
        "timestamp": e["timestamp"], "run_id": e["run_id"], "ticket_id": e["ticket_id"], "agent": e["agent"],
        "step": e["step"], "event": e["event"], "said": e["text"],
        "tools_used": [c["tool"] for c in e["tool_calls"]], "tool_calls": e["tool_calls"],
        "tool_results": e["tool_results"], "tokens": e["tokens"], "detail": e["detail"],
    } for e in reversed(picked[-limit:])]
    return {"count": len(events), "total_in_trail": len(trail), "events": events}


@app.get("/api/board")
async def board(ticket_id: int | None = None, kind: str | None = None, status: str | None = None) -> dict:
    """What the agents posted for people: payment requests, price overrides, purchase drafts, customer drafts."""
    args = {k: v for k, v in (("ticket_id", ticket_id), ("kind", kind), ("status", status)) if v is not None}
    return await mcp_call("get_board", args)


@app.post("/api/board/{board_id}/approve")
async def approve(board_id: int, body: ApproveRequest) -> dict:
    """A person clicks Approve. Payments and purchases are paid right here; this is the only place cash changes."""
    decided = await mcp_call("decide_request", {"board_id": board_id, "decision": "approve",
                                                 "decided_by": body.approved_by, "note": body.note})
    retry_payment = decided["status"] == "refused" and decided.get("current_status") == "approved" \
        and decided.get("kind") in PAYABLE
    if decided["status"] == "refused" and not retry_payment:
        app.state.audit.record(run_id="api", agent="human", event="human_decision",
                               text=f"{body.approved_by} tried to approve board {board_id}; refused: {decided['reason']}",
                               detail={"board_id": board_id, "kind": decided.get("kind"), "approved": False,
                                       "reason": decided["reason"]})
        raise HTTPException(409, decided["reason"])

    response: dict[str, Any] = {"board_id": board_id, "approved": decided}
    if decided["kind"] in PAYABLE:
        before = await checking_balance()
        paid = await mcp_call("pay_approved_request", {"board_id": board_id})
        if paid["status"] == "refused":
            app.state.audit.record(run_id="api", agent="human", event="human_decision",
                                   text=f"{body.approved_by} approved board {board_id}; payment refused: {paid['reason']}",
                                   detail={"board_id": board_id, "kind": decided["kind"], "paid": False})
            raise HTTPException(409, {"approved": True, "paid": False, "reason": paid["reason"]})
        response["payment"] = paid
        response["cash_before"] = before["balance"]
    response["cash"] = await checking_balance()
    app.state.audit.record(run_id="api", agent="human", event="human_decision",
                           text=f"{body.approved_by} approved board {board_id} ({decided['kind']})",
                           detail={"board_id": board_id, "kind": decided["kind"],
                                   "payment": response.get("payment", {}).get("payment"),
                                   "cash_after": response["cash"]["balance"]})
    return response


@app.get("/api/cash")
async def cash() -> dict:
    """The current checking balance from cash_accounts (plus what's waiting for approval)."""
    return await checking_balance()


@app.post("/api/reset")
async def reset() -> dict:
    """Reset the working database to the original values (rule 8): clears the board, payments and every change."""
    if app.state.active_run:
        raise HTTPException(409, f"Run {app.state.active_run} is still going. Reset after it finishes.")
    refreshed = await mcp_call("refresh_working_copy")
    app.state.last_refresh = _now()
    app.state.audit.record(run_id="api", agent="human", event="reset", detail=refreshed)
    return {"refreshed": refreshed, "cash": await checking_balance()}
