"""Customer Service agent: drafts replies to customers. Drafts stay on the board; nothing is ever sent."""

from __future__ import annotations

from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.models import Model

from agents.base import load_prompt, model_settings, tool_results, tools_for
from models import CustomerDraft, TeamDeps

NAME = "Customer Service"
TOOLS = frozenset({"get_ticket", "check_stock", "check_vendor_status", "get_board", "draft_customer_message"})


def build(model: Model, toolset: MCPToolset) -> Agent[TeamDeps, CustomerDraft]:
    agent = Agent(
        model,
        name=NAME,
        deps_type=TeamDeps,
        output_type=CustomerDraft,
        instructions=load_prompt("customer_service"),
        toolsets=[tools_for(toolset, TOOLS)],
        model_settings=model_settings(),
        retries=2,
    )

    @agent.output_validator
    def draft_is_the_saved_one(ctx: RunContext[TeamDeps], out: CustomerDraft) -> CustomerDraft:
        saved = {r["board_id"]: r for r in tool_results(ctx, "draft_customer_message")
                 if isinstance(r, dict) and r.get("board_id") is not None}
        if out.board_id not in saved:
            raise ModelRetry("Save the message with draft_customer_message and report the board_id it returned.")
        draft = saved[out.board_id]
        if (out.to, out.subject, out.body) != (draft["to"], draft["subject"], draft["body"]):
            raise ModelRetry("to / subject / body must be exactly what draft_customer_message saved.")
        return out

    return agent
