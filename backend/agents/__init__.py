"""The Campus Customs agent team: Boss plus four specialists, all on one MCP server."""

from __future__ import annotations

from dataclasses import dataclass

from pydantic_ai import Agent
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.models import Model

from agents import accounting, boss, customer_service, facilities, inventory

ALLOWED_TOOLS = {
    boss.NAME: boss.TOOLS,
    inventory.NAME: inventory.TOOLS,
    accounting.NAME: accounting.TOOLS,
    facilities.NAME: facilities.TOOLS,
    customer_service.NAME: customer_service.TOOLS,
}
HUMAN_ONLY_TOOLS = frozenset({"decide_request", "pay_approved_request", "refresh_working_copy"})
API_ONLY_TOOLS = frozenset({"list_tickets"})  # read tool for the dashboard (backend/main.py), not needed by any agent


@dataclass
class Team:
    boss_triage: Agent
    boss: Agent
    inventory: Agent
    accounting: Agent
    facilities: Agent
    customer_service: Agent


def build_team(model: Model, toolset: MCPToolset) -> Team:
    specialists = {
        inventory.NAME: inventory.build(model, toolset),
        accounting.NAME: accounting.build(model, toolset),
        facilities.NAME: facilities.build(model, toolset),
        customer_service.NAME: customer_service.build(model, toolset),
    }
    return Team(
        boss_triage=boss.build_triage(model, toolset),
        boss=boss.build(model, toolset, specialists),
        inventory=specialists[inventory.NAME],
        accounting=specialists[accounting.NAME],
        facilities=specialists[facilities.NAME],
        customer_service=specialists[customer_service.NAME],
    )
