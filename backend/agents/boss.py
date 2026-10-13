"""Boss agent: reads every ticket, decides who works on it, and makes the final call.

The Boss is the hub of the team. It reaches each specialist through an
`ask_*` delegation tool and the shop through its own slice of the MCP
server. Specialists don't call each other; they return `handoffs` and the
Boss decides whether to follow them, which keeps every loop bounded.
"""

from __future__ import annotations

from typing import Any

from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.models import Model
from pydantic_ai.usage import RunUsage

from agents.base import (
    MAX_ASKS_PER_SPECIALIST,
    MIN_TOKENS_TO_DELEGATE,
    TICKET_TOKEN_BUDGET,
    load_prompt,
    model_settings,
    run_logged,
    specialist_limits,
    tool_calls,
    tool_results,
    tools_for,
)
from models import (
    AccountingReport,
    BossDecision,
    CustomerDraft,
    FacilitiesReport,
    InventoryReport,
    SpecialistFailure,
    TeamDeps,
    TriagePlan,
)

NAME = "Boss"
TOOLS = frozenset({"list_open_tickets", "get_ticket", "get_board", "get_cash_position", "update_ticket"})
TRIAGE_TOOLS = frozenset({"list_open_tickets", "get_ticket", "get_board", "get_cash_position"})  # read-only, no delegation
DELEGATION_TOOLS = ("ask_inventory", "ask_accounting", "ask_facilities", "ask_customer_service")


def _board_ids(value: Any) -> set[int]:
    """Every board id mentioned anywhere in a tool result (board_id, *_board_id, or board item ids)."""
    found: set[int] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key.endswith("board_id") and isinstance(item, int):
                found.add(item)
            elif key in ("board", "items", "pending_payment_requests") and isinstance(item, list):
                found.update(i["id"] for i in item if isinstance(i, dict) and isinstance(i.get("id"), int))
            found |= _board_ids(item)
    elif isinstance(value, list):
        for item in value:
            found |= _board_ids(item)
    elif hasattr(value, "model_dump"):
        found |= _board_ids(value.model_dump())
    return found


def build(model: Model, toolset: MCPToolset, specialists: dict[str, Agent]) -> Agent[TeamDeps, BossDecision]:
    agent = Agent(
        model,
        name=NAME,
        deps_type=TeamDeps,
        output_type=BossDecision,
        instructions=load_prompt("boss"),
        toolsets=[tools_for(toolset, TOOLS)],
        model_settings=model_settings(parallel_tools=False),  # delegate one specialist at a time, in order
        retries=2,
    )

    async def delegate(ctx: RunContext[TeamDeps], name: str, ticket_id: int, brief: str) -> Any:
        deps = ctx.deps
        if ticket_id != deps.ticket_id:
            raise ModelRetry(f"You're working ticket {deps.ticket_id}. Brief specialists on that ticket only.")
        asked = deps.delegations.get(name, 0)
        if asked >= MAX_ASKS_PER_SPECIALIST:
            raise ModelRetry(f"{name} has already been asked {asked} times on this ticket. Decide with what you have.")
        remaining = TICKET_TOKEN_BUDGET - deps.specialist_tokens - ctx.usage.total_tokens
        if remaining < MIN_TOKENS_TO_DELEGATE:
            return SpecialistFailure(agent=name, reason=f"Ticket token budget nearly used ({remaining} tokens left). Decide with what you have.")
        deps.delegations[name] = asked + 1
        usage = RunUsage()
        prompt = f"Ticket {ticket_id}.\nBrief from the Boss: {brief.strip()}"
        try:
            return await run_logged(specialists[name], name, prompt, deps, limits=specialist_limits(remaining), usage=usage)
        except Exception as exc:  # usage limit, model or tool failure: the Boss decides what to do next
            return SpecialistFailure(agent=name, reason=f"{type(exc).__name__}: {str(exc)[:200]}")
        finally:
            deps.specialist_tokens += usage.total_tokens

    @agent.tool
    async def ask_inventory(ctx: RunContext[TeamDeps], ticket_id: int, brief: str) -> InventoryReport | SpecialistFailure:
        """Ask Inventory to check stock for the ticket's SKU and size, the shortfall, whether it's already on order, and which vendor can restock it (it drafts a vendor order if one is needed).

        Args:
            ticket_id: The ticket you are working.
            brief: What you need and any facts already known, in under 80 words.
        """
        return await delegate(ctx, "Inventory", ticket_id, brief)

    @agent.tool
    async def ask_accounting(ctx: RunContext[TeamDeps], ticket_id: int, brief: str) -> AccountingReport | SpecialistFailure:
        """Ask Accounting about cash, an invoice, rent payment or a discount. Accounting posts payment requests and price overrides for human approval; it never pays.

        Args:
            ticket_id: The ticket you are working.
            brief: What you need (e.g. the invoice id or lease id to request payment for, or the discount asked for) and facts other specialists found, in under 80 words.
        """
        return await delegate(ctx, "Accounting", ticket_id, brief)

    @agent.tool
    async def ask_facilities(ctx: RunContext[TeamDeps], ticket_id: int, brief: str) -> FacilitiesReport | SpecialistFailure:
        """Ask Facilities to check a rent or lease notice against the lease: amount, due date, overdue or not, and whether cash covers it.

        Args:
            ticket_id: The ticket you are working.
            brief: What the notice claims and what you need checked, in under 80 words.
        """
        return await delegate(ctx, "Facilities", ticket_id, brief)

    @agent.tool
    async def ask_customer_service(ctx: RunContext[TeamDeps], ticket_id: int, brief: str) -> CustomerDraft | SpecialistFailure:
        """Ask Customer Service to draft a reply to the ticket's requester. It is saved on the board for a human and never sent.

        Args:
            ticket_id: The ticket you are working.
            brief: The confirmed facts the customer may hear (stock, timing, what's under review) and what to leave out, in under 80 words.
        """
        return await delegate(ctx, "Customer Service", ticket_id, brief)

    @agent.output_validator
    def final_call_is_recorded(ctx: RunContext[TeamDeps], out: BossDecision) -> BossDecision:
        if out.status not in ("resolved", "blocked"):
            raise ModelRetry("A ticket run ends 'resolved' (work done, approvals on the board) or 'blocked' (couldn't finish).")
        updates = [r for r in tool_results(ctx, "update_ticket") if isinstance(r, dict) and r.get("status") == "updated"]
        if not updates:
            raise ModelRetry("Record your final call with update_ticket before returning your decision.")
        if updates[-1]["ticket_id"] != out.ticket_id or updates[-1]["to"] != out.status:
            raise ModelRetry(f"Your decision's status must match the last update_ticket call ({updates[-1]['to']}).")

        asked = {name: tool_calls(ctx, tool) for name, tool in
                 zip(("Inventory", "Accounting", "Facilities", "Customer Service"), DELEGATION_TOOLS)}
        not_asked = [w for w in out.worked_by if not asked[w]]
        if not_asked:
            raise ModelRetry(f"worked_by lists {not_asked}, but you never asked them.")

        known: set[int] = set()
        for tool in (*DELEGATION_TOOLS, "get_ticket", "get_board", "get_cash_position"):
            for result in tool_results(ctx, tool):
                known |= _board_ids(result)
        unknown = [a.board_id for a in out.needs_human if a.board_id is not None and a.board_id not in known]
        if out.customer_draft_board_id is not None and out.customer_draft_board_id not in known:
            unknown.append(out.customer_draft_board_id)
        if unknown:
            raise ModelRetry(f"Board ids {unknown} didn't come from any tool or specialist report. Use real ids only.")
        return out

    return agent


def build_triage(model: Model, toolset: MCPToolset) -> Agent[TeamDeps, TriagePlan]:
    """The Boss in triage mode: same instructions, read-only tools, no delegation, returns a TriagePlan."""
    agent = Agent(
        model,
        name=NAME,
        deps_type=TeamDeps,
        output_type=TriagePlan,
        instructions=load_prompt("boss"),
        toolsets=[tools_for(toolset, TRIAGE_TOOLS)],
        model_settings=model_settings(),
        retries=2,
    )

    @agent.output_validator
    def plan_covers_every_ticket(ctx: RunContext[TeamDeps], out: TriagePlan) -> TriagePlan:
        listed = [r for r in tool_results(ctx, "list_open_tickets") if isinstance(r, dict)]
        if not listed:
            raise ModelRetry("Call list_open_tickets before planning.")
        missing = {t["id"] for t in listed[-1]["tickets"]} - {r.ticket_id for r in out.order}
        if missing:
            raise ModelRetry(f"The plan must cover every open ticket. Missing: {sorted(missing)}.")
        if out.today.isoformat() != listed[-1]["today"]:
            raise ModelRetry(f"today must be the desk date {listed[-1]['today']}.")
        return out

    return agent
