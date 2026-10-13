"""Run the Campus Customs agent team.

One run:
  1. refresh the working database from the original, through the MCP server (rule 8)
  2. Boss triage: read every open ticket, decide who works on what and in what order
  3. for each ticket: Boss delegates to specialists, then records the final call
Every agent-loop step is appended to output/audit_trail.json as it happens.

From Homework 5/:
  .venv/bin/python backend/team.py --check          # connectivity check, no model calls
  .venv/bin/python backend/team.py                  # run every open ticket
  .venv/bin/python backend/team.py --ticket 101     # run selected tickets (still refreshes first)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pydantic_ai.models import Model  # noqa: E402
from pydantic_ai.usage import RunUsage  # noqa: E402

from agents import ALLOWED_TOOLS, API_ONLY_TOOLS, HUMAN_ONLY_TOOLS, build_team  # noqa: E402
from agents.base import (  # noqa: E402
    BOSS_LIMITS,
    MODEL_NAME,
    RUN_TOKEN_BUDGET,
    TRIAGE_LIMITS,
    get_model,
    mcp_toolset,
    run_logged,
)
from audit import AuditTrail  # noqa: E402
from models import TeamDeps, TeamRunReport, TicketOutcome, TicketRoute, TriagePlan  # noqa: E402

TRIAGE_PROMPT = "TRIAGE MODE. Read every open ticket and return a TriagePlan. Do not delegate."


def ticket_prompt(route: TicketRoute, plan: TriagePlan) -> str:
    blockers = "; ".join(plan.shared_blockers) or "none"
    return (
        f"TICKET MODE. Work ticket {route.ticket_id} ({route.ticket_type}, from {route.requester}).\n"
        f"Planned route from triage: {' -> '.join(route.agents) or 'no specialist'}. Why: {route.why}\n"
        f"Linked records: {', '.join(route.linked) or 'none'}. Shared blockers: {blockers}.\n"
        "Follow the ticket loop in your instructions, record the final call with update_ticket, "
        "then return your BossDecision."
    )


def solo_prompt(ticket_id: int) -> str:
    """One ticket run without triage (from the API): the Boss picks the specialists itself."""
    return (
        f"TICKET MODE. Work ticket {ticket_id}.\n"
        "No triage plan for this run: choose only the specialists this ticket needs, using the routing guide "
        "in your instructions.\n"
        "Follow the ticket loop in your instructions, record the final call with update_ticket, "
        "then return your BossDecision."
    )


async def run_team(ticket_ids: list[int] | None = None, *, refresh: bool = True, triage: bool = True,
                   run_id: str | None = None, model: Model | None = None,
                   audit: AuditTrail | None = None) -> TeamRunReport:
    """Run the team. The CLI refreshes and triages; the API runs one ticket without triage (see main.py)."""
    if not triage and not ticket_ids:
        raise ValueError("Without triage, say which tickets to run.")
    audit = audit or AuditTrail()
    run_id = run_id or uuid.uuid4().hex[:12]
    toolset = mcp_toolset()
    team = build_team(model or get_model(), toolset)
    outcomes: list[TicketOutcome] = []
    total, stopped, plan, refreshed = 0, None, None, None

    async with toolset:
        audit.record(run_id=run_id, agent="runner", event="run_start",
                     detail={"model": MODEL_NAME, "tickets_requested": ticket_ids, "refresh": refresh,
                             "triage": triage, "run_token_budget": RUN_TOKEN_BUDGET})
        if refresh:
            refreshed = await toolset.direct_call_tool("refresh_working_copy", {})
            audit.record(run_id=run_id, agent="runner", event="refresh", detail=refreshed)
            if not refreshed.get("identical_to_original"):
                raise RuntimeError("Working database didn't match the original after refresh; stopping (rule 8).")

        if triage:
            triage_usage = RunUsage()
            try:
                plan = await run_logged(team.boss_triage, "Boss", TRIAGE_PROMPT, TeamDeps(run_id=run_id, audit=audit),
                                        limits=TRIAGE_LIMITS, usage=triage_usage)
            finally:
                total += triage_usage.total_tokens
            work = [(r.ticket_id, ticket_prompt(r, plan), r.model_dump()) for r in plan.order
                    if not ticket_ids or r.ticket_id in ticket_ids]
        else:
            work = [(t, solo_prompt(t), {"ticket_id": t, "route": "chosen by the Boss (no triage)"}) for t in ticket_ids]

        for ticket_id, prompt, route_detail in work:
            if total >= RUN_TOKEN_BUDGET:
                stopped = f"Run token budget reached ({total} of {RUN_TOKEN_BUDGET}); ticket {ticket_id} and later not started."
                break
            deps = TeamDeps(run_id=run_id, audit=audit, ticket_id=ticket_id)
            audit.record(run_id=run_id, ticket_id=ticket_id, agent="runner", event="ticket_start", detail=route_detail)
            boss_usage = RunUsage()
            try:
                decision = await run_logged(team.boss, "Boss", prompt, deps, limits=BOSS_LIMITS, usage=boss_usage)
                outcome = TicketOutcome(ticket_id=ticket_id, ok=True, decision=decision)
            except Exception as exc:
                outcome = TicketOutcome(ticket_id=ticket_id, ok=False, error=f"{type(exc).__name__}: {exc}"[:300])
            outcome.tokens = boss_usage.total_tokens + deps.specialist_tokens
            total += outcome.tokens
            outcomes.append(outcome)
            audit.record(run_id=run_id, ticket_id=ticket_id, agent="runner", event="ticket_end",
                         text=outcome.decision.final_call if outcome.decision else outcome.error,
                         tokens={"ticket_total": outcome.tokens, "run_total": total},
                         detail={"ok": outcome.ok, "status": outcome.decision.status if outcome.decision else None,
                                 "delegations": deps.delegations,
                                 "decision": outcome.decision.model_dump(mode="json") if outcome.decision else None})

        audit.record(run_id=run_id, agent="runner", event="run_end", tokens={"run_total": total},
                     detail={"tickets": [o.ticket_id for o in outcomes], "stopped_early": stopped})
    return TeamRunReport(run_id=run_id, refreshed=refreshed, plan=plan, outcomes=outcomes,
                         total_tokens=total, stopped_early=stopped)


async def check_connectivity() -> dict:
    """Start the MCP server, list its tools and confirm every agent's slice exists and has no human-only tool."""
    toolset = mcp_toolset()
    async with toolset:
        server_tools = {t.name for t in await toolset.list_tools()}
    report = {"model": MODEL_NAME, "server_tools": sorted(server_tools), "agents": {}, "ok": True}
    for agent, allowed in ALLOWED_TOOLS.items():
        missing = sorted(allowed - server_tools)
        human = sorted(allowed & HUMAN_ONLY_TOOLS)
        report["agents"][agent] = {"tools": sorted(allowed), "missing_on_server": missing, "human_only_leak": human}
        report["ok"] &= not missing and not human
    report["human_only_tools"] = sorted(HUMAN_ONLY_TOOLS)
    report["human_only_on_server"] = sorted(HUMAN_ONLY_TOOLS & server_tools)
    report["api_only_tools"] = sorted(API_ONLY_TOOLS & server_tools)
    report["unused"] = sorted(server_tools - set().union(*ALLOWED_TOOLS.values()) - HUMAN_ONLY_TOOLS - API_ONLY_TOOLS)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ticket", type=int, action="append", help="Only run this ticket (repeatable)")
    parser.add_argument("--check", action="store_true", help="Connectivity check only; no model calls")
    args = parser.parse_args()
    if args.check:
        print(json.dumps(asyncio.run(check_connectivity()), indent=2))
        return
    report = asyncio.run(run_team(args.ticket))
    for o in report.outcomes:
        line = f"[{o.decision.status}] {o.decision.final_call}" if o.decision else f"FAILED: {o.error}"
        print(f"Ticket {o.ticket_id} ({o.tokens:,} tokens): {line}")
    print(f"Run {report.run_id}: {report.total_tokens:,} tokens. Audit: output/audit_trail.json")
    if report.stopped_early:
        print(report.stopped_early)


if __name__ == "__main__":
    main()
