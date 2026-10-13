"""Data types for the Campus Customs agent team.

Every agent returns one of these models, so its answer is structured and
checkable instead of free text. Field descriptions are sent to the model as
part of the output schema, so they double as instructions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field

AgentName = Literal["Boss", "Inventory", "Accounting", "Facilities", "Customer Service"]
SpecialistName = Literal["Inventory", "Accounting", "Facilities", "Customer Service"]
TicketStatus = Literal["open", "in_progress", "waiting_on_human", "blocked", "resolved"]
HumanActionKind = Literal[
    "approve_payment", "approve_price_override", "review_customer_draft", "review_vendor_draft", "decide"
]


class Handoff(BaseModel):
    """A specialist telling the Boss who should act next. The Boss decides whether to follow it."""

    to: AgentName = Field(description="Agent who should act next")
    reason: str = Field(description="What they need to do and why, in one sentence")


# --------------------------------------------------------------------------- Boss: triage


class TicketRoute(BaseModel):
    ticket_id: int
    ticket_type: str = Field(description="tickets.type, copied exactly")
    requester: str = Field(description="tickets.requester, copied exactly")
    agents: list[SpecialistName] = Field(description="Specialists who should work the ticket, in working order")
    linked: list[str] = Field(description="Records the ticket touches, e.g. 'invoice 501', 'lease 1', 'CC-TEE-WHITE S x1'")
    why: str = Field(description="Why these agents, in this order, in one or two sentences")


class TriagePlan(BaseModel):
    """The Boss's read of every open ticket before any work starts."""

    today: date = Field(description="desk.date_today from the tools")
    order: list[TicketRoute] = Field(description="Every open ticket, in the order it should be worked")
    shared_blockers: list[str] = Field(description="Problems that hold up more than one ticket, e.g. one unpaid invoice")
    cash_note: str = Field(description="One sentence on cash vs. the bills the tickets involve")


# --------------------------------------------------------------------------- Inventory


class VendorPick(BaseModel):
    vendor_id: int
    name: str
    specialty: str = Field(description="vendors.specialty; the only way to match a product to a vendor")
    lead_days: int = Field(description="vendors.lead_days (rule 2)")
    can_ship: bool = Field(description="False if the vendor has any open invoice (rule 3)")
    blocked_by_invoices: list[int] = Field(description="Open invoice ids stopping this vendor; empty if none")
    arrives_by: date | None = Field(description="desk date + lead_days if the vendor can ship today, else null")
    why_this_vendor: str


class InventoryReport(BaseModel):
    ticket_id: int
    sku: str
    size: str
    product_name: str
    location: str = Field(description="Aisle, from inventory.location")
    qty_needed: int
    on_hand: int
    short_by: int
    can_fill_from_stock: bool
    other_sizes_in_stock: dict[str, int] = Field(description="Other sizes of the same SKU with stock, size -> qty")
    already_on_order: str | None = Field(
        description="An open invoice that already covers this SKU and size (e.g. 'invoice 501: Rush reprint CC-TEE-WHITE S'), else null")
    vendor: VendorPick | None = Field(description="Vendor to restock from if stock is short, else null")
    vendor_draft_board_id: int | None = Field(description="Board id from draft_vendor_order, or null if no order was drafted")
    handoffs: list[Handoff]
    summary: str = Field(description="Two or three plain sentences for the Boss")


# --------------------------------------------------------------------------- Accounting


class PaymentRequestRef(BaseModel):
    board_id: int = Field(description="Board id returned by request_payment")
    kind: Literal["invoice", "rent"]
    ref_id: int = Field(description="Invoice id or lease id")
    amount: float = Field(description="Amount the tool returned; never typed in by the agent")
    status: str = Field(description="Board status, e.g. pending_approval")


class PriceReview(BaseModel):
    sku: str
    qty: int
    list_price: float
    unit_cost: float
    proposed_unit_price: float
    discount_pct: float
    margin_per_unit: float
    quote_total: float
    override_board_id: int | None = Field(description="Board id from request_price_override, or null if refused / not posted")
    reasoning: str = Field(description="Why this price; there is no discount policy in the data, so a human decides")


class AccountingReport(BaseModel):
    ticket_id: int
    cash_balance: float = Field(description="Total cash from get_cash_position")
    pending_before: float = Field(description="Payment requests already waiting on the board before this ticket")
    payment_requests: list[PaymentRequestRef] = Field(description="Requests posted or already on the board for this work")
    price_review: PriceReview | None
    cash_after_pending: float = Field(description="Cash left if every waiting payment request is paid")
    refused: list[str] = Field(description="Anything a tool refused, with its reason, word for word")
    risks: list[str] = Field(description="Money risks the human should see, e.g. a restock that cash can't cover")
    handoffs: list[Handoff]
    summary: str = Field(description="Two or three plain sentences for the Boss")


# --------------------------------------------------------------------------- Facilities


class FacilitiesReport(BaseModel):
    ticket_id: int
    lease_id: int
    space_name: str
    landlord: str
    monthly_rent: float
    next_due: date
    days_until_due: int = Field(description="From check_rent_due; negative means overdue")
    overdue: bool
    notice_matches_records: bool = Field(description="Does what the ticket claims match the leases table?")
    discrepancy: str | None = Field(description="What doesn't match, or null")
    cash_covers_rent: bool
    recommendation: str = Field(description="What should happen and by when, e.g. 'pay before 2026-09-02'")
    handoffs: list[Handoff]
    summary: str = Field(description="Two or three plain sentences for the Boss")


# --------------------------------------------------------------------------- Customer Service


class CustomerDraft(BaseModel):
    ticket_id: int
    to: str = Field(description="The ticket's requester, as returned by draft_customer_message")
    subject: str
    body: str = Field(description="Exactly the body that draft_customer_message saved")
    board_id: int = Field(description="Board id returned by draft_customer_message")
    facts_used: list[str] = Field(description="Each fact the message relies on and the tool it came from")
    left_out: list[str] = Field(description="Internal facts deliberately kept out of the message")
    summary: str


class SpecialistFailure(BaseModel):
    """Returned to the Boss when a specialist couldn't finish (token budget, tool failure, model error)."""

    agent: SpecialistName
    reason: str


# --------------------------------------------------------------------------- Boss: final call


class HumanAction(BaseModel):
    kind: HumanActionKind
    board_id: int | None = Field(description="Board id the human acts on, if there is one")
    what: str = Field(description="What the human needs to decide, in one sentence")
    amount: float | None = Field(description="Dollar amount involved, if any, exactly as a tool returned it")


class BossDecision(BaseModel):
    """The Boss's final call on one ticket."""

    ticket_id: int
    ticket_type: str
    worked_by: list[SpecialistName] = Field(description="Specialists who actually worked the ticket, in order")
    findings: list[str] = Field(description="One line per specialist: what they found, with numbers from the tools")
    final_call: str = Field(description="The decision in two or three plain sentences")
    status: TicketStatus = Field(description="Must match the status passed to update_ticket")
    needs_human: list[HumanAction] = Field(description="Every approval or review waiting on a person")
    customer_draft_board_id: int | None
    next_steps: list[str] = Field(description="What happens after the human acts")


# --------------------------------------------------------------------------- runtime + audit


@dataclass
class TeamDeps:
    """Shared by every agent in one run: run id, audit log and per-ticket counters. Never shop data; shop facts only come from MCP tools."""

    run_id: str
    audit: Any
    ticket_id: int | None = None  # None during triage
    delegations: dict[str, int] = field(default_factory=dict)  # specialist -> times asked on this ticket
    specialist_tokens: int = 0  # tokens used by specialists on this ticket


AuditEventName = Literal[
    "run_start", "refresh", "ticket_start", "agent_start", "model_response", "tool_results",
    "agent_end", "agent_stopped", "ticket_end", "run_end",
    "human_decision", "reset",  # written by the API when a person approves or resets
]


class AuditEvent(BaseModel):
    """One step of the agent loop. Appended to output/audit_trail.json, never rewritten or removed."""

    timestamp: str = Field(description="Real clock, ISO 8601 with offset")
    run_id: str = Field(description="Groups every step of one team run")
    ticket_id: int | None
    agent: str = Field(description="Boss, a specialist, or 'runner' for run-level events")
    step: int = Field(description="Order of this step within the agent's run, from 0")
    event: AuditEventName
    tool_calls: list[dict] = Field(default_factory=list, description="Tools the model asked for, with arguments")
    tool_results: list[dict] = Field(default_factory=list, description="Shortened tool results or errors")
    text: str | None = Field(default=None, description="Prompt, model text or output summary (shortened, redacted)")
    tokens: dict | None = Field(default=None, description="This request's tokens and the running totals")
    detail: dict | None = None


class TicketOutcome(BaseModel):
    ticket_id: int
    ok: bool
    decision: BossDecision | None = None
    error: str | None = None
    tokens: int = 0


class TeamRunReport(BaseModel):
    run_id: str
    refreshed: dict | None = Field(description="refresh_working_copy result, or null if this run didn't refresh")
    plan: TriagePlan | None
    outcomes: list[TicketOutcome]
    total_tokens: int
    stopped_early: str | None = None
