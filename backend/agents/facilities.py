"""Facilities agent: leases, rent and the shop space. Checks notices against the lease; Accounting raises any payment."""

from __future__ import annotations

from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.models import Model

from agents.base import load_prompt, model_settings, tool_results, tools_for
from models import FacilitiesReport, TeamDeps

NAME = "Facilities"
TOOLS = frozenset({"get_ticket", "check_rent_due"})


def build(model: Model, toolset: MCPToolset) -> Agent[TeamDeps, FacilitiesReport]:
    agent = Agent(
        model,
        name=NAME,
        deps_type=TeamDeps,
        output_type=FacilitiesReport,
        instructions=load_prompt("facilities"),
        toolsets=[tools_for(toolset, TOOLS)],
        model_settings=model_settings(),
        retries=2,
    )

    @agent.output_validator
    def lease_facts_match(ctx: RunContext[TeamDeps], out: FacilitiesReport) -> FacilitiesReport:
        leases = [r for r in tool_results(ctx, "check_rent_due") if isinstance(r, dict) and r.get("lease_id") == out.lease_id]
        if not leases:
            raise ModelRetry(f"Call check_rent_due for lease {out.lease_id} before reporting on it.")
        lease = leases[-1]
        mine = (out.monthly_rent, out.next_due.isoformat(), out.days_until_due, out.overdue)
        tool = (lease["monthly_rent"], lease["next_due"], lease["days_until_due"], lease["overdue"])
        if mine != tool:
            raise ModelRetry(f"monthly_rent / next_due / days_until_due / overdue must match check_rent_due: {tool}.")
        covers = any(c["covers"] for c in lease["cash_check"])
        if out.cash_covers_rent != covers:
            raise ModelRetry(f"cash_covers_rent must be {covers}, from check_rent_due's cash_check.")
        return out

    return agent
