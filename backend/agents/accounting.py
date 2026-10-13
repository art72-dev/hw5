"""Accounting agent: cash, invoices, margins, payment requests and price overrides that need human approval."""

from __future__ import annotations

from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.models import Model

from agents.base import load_prompt, model_settings, tool_results, tools_for
from models import AccountingReport, TeamDeps

NAME = "Accounting"
TOOLS = frozenset({
    "get_ticket", "get_cash_position", "check_vendor_status", "check_rent_due", "check_margin",
    "get_board", "request_payment", "request_price_override",
})


def build(model: Model, toolset: MCPToolset) -> Agent[TeamDeps, AccountingReport]:
    agent = Agent(
        model,
        name=NAME,
        deps_type=TeamDeps,
        output_type=AccountingReport,
        instructions=load_prompt("accounting"),
        toolsets=[tools_for(toolset, TOOLS)],
        model_settings=model_settings(),
        retries=2,
    )

    @agent.output_validator
    def money_comes_from_tools(ctx: RunContext[TeamDeps], out: AccountingReport) -> AccountingReport:
        positions = [r for r in tool_results(ctx, "get_cash_position") if isinstance(r, dict)]
        if not positions:
            raise ModelRetry("Call get_cash_position before reporting cash.")
        if abs(out.cash_balance - positions[0]["totals"]["cash"]) > 0.005:
            raise ModelRetry(f"cash_balance must be {positions[0]['totals']['cash']} (get_cash_position).")

        posted = {r["board_id"]: r for r in tool_results(ctx, "request_payment")
                  if isinstance(r, dict) and r.get("board_id") is not None}
        on_board = {p["id"]: p for pos in positions for p in pos.get("pending_payment_requests", [])}
        for req in out.payment_requests:
            source = posted.get(req.board_id) or on_board.get(req.board_id)
            if source is None:
                raise ModelRetry(f"Payment request {req.board_id} wasn't returned by request_payment or get_cash_position.")
            if abs(req.amount - source["amount"]) > 0.005:
                raise ModelRetry(f"Payment request {req.board_id} amount must be {source['amount']}, as the tool returned.")

        if out.price_review and out.price_review.override_board_id is not None:
            ids = {r.get("board_id") for r in tool_results(ctx, "request_price_override") if isinstance(r, dict)}
            if out.price_review.override_board_id not in ids:
                raise ModelRetry("override_board_id must be a board_id that request_price_override returned.")
        return out

    return agent
