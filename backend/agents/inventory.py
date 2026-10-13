"""Inventory agent: stock by SKU and size, shortfalls, which vendor restocks it, and draft vendor orders."""

from __future__ import annotations

from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.models import Model

from agents.base import load_prompt, model_settings, tool_results, tools_for
from models import InventoryReport, TeamDeps

NAME = "Inventory"
TOOLS = frozenset({"get_ticket", "check_stock", "check_vendor_status", "draft_vendor_order"})


def build(model: Model, toolset: MCPToolset) -> Agent[TeamDeps, InventoryReport]:
    agent = Agent(
        model,
        name=NAME,
        deps_type=TeamDeps,
        output_type=InventoryReport,
        instructions=load_prompt("inventory"),
        toolsets=[tools_for(toolset, TOOLS)],
        model_settings=model_settings(),
        retries=2,
    )

    @agent.output_validator
    def numbers_come_from_tools(ctx: RunContext[TeamDeps], out: InventoryReport) -> InventoryReport:
        stock = [r for r in tool_results(ctx, "check_stock")
                 if isinstance(r, dict) and r.get("sku") == out.sku and r.get("size") == out.size]
        if not stock:
            raise ModelRetry(f"Call check_stock for {out.sku} {out.size} before reporting stock.")
        s = stock[-1]
        if (out.on_hand, out.short_by, out.qty_needed) != (s["on_hand"], s["short_by"], s["qty_needed"]):
            raise ModelRetry(f"on_hand / short_by / qty_needed must match check_stock: "
                             f"{s['on_hand']} / {s['short_by']} / {s['qty_needed']}.")
        if out.vendor is not None:
            vendors = {v["vendor_id"]: v for r in tool_results(ctx, "check_vendor_status") if isinstance(r, dict)
                       for v in r.get("vendors", [])}
            v = vendors.get(out.vendor.vendor_id)
            if v is None:
                raise ModelRetry("The vendor must come from a check_vendor_status result.")
            if (out.vendor.can_ship, out.vendor.lead_days) != (v["can_ship"], v["lead_days"]):
                raise ModelRetry(f"Vendor can_ship / lead_days must match check_vendor_status: {v['can_ship']} / {v['lead_days']}.")
        if out.vendor_draft_board_id is not None:
            ids = {r.get("board_id") for r in tool_results(ctx, "draft_vendor_order") if isinstance(r, dict)}
            if out.vendor_draft_board_id not in ids:
                raise ModelRetry("vendor_draft_board_id must be a board_id that draft_vendor_order returned.")
        return out

    return agent
