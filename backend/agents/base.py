"""Shared pieces for every agent: the model, the MCP connection, token limits and the logged agent loop.

Shop facts only ever come from the campus-customs MCP server. There is no
other shop-tools layer: each agent gets a filtered view of the same MCP
toolset, limited to the tools its job needs.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastmcp import Client
from fastmcp.client.transports import StdioTransport
from pydantic_ai import Agent, RunContext
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models import Model
from pydantic_ai.models.openai import OpenAIResponsesModel, OpenAIResponsesModelSettings
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.usage import RunUsage, UsageLimits

from models import TeamDeps

BACKEND = Path(__file__).resolve().parent.parent
HW = BACKEND.parent
PROMPTS = BACKEND / "prompts"
MCP_CONFIG = HW / ".mcp.json"

os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")
load_dotenv(HW / ".env")
load_dotenv(HW.parent / ".env")  # workspace root, where PORTKEY_API_KEY lives

MODEL_NAME = "gpt-6-luna"  # the only model used in this homework
DEFAULT_PORTKEY_URL = "https://api.portkey.ai/v1"

# ---- token guardrails (see output/harness.md, "Safety") ----
MAX_OUTPUT_TOKENS = 3_000          # per model response
TICKET_TOKEN_BUDGET = 150_000      # whole team, one ticket
RUN_TOKEN_BUDGET = 450_000         # whole run; no new ticket starts past this
MIN_TOKENS_TO_DELEGATE = 10_000    # don't start a specialist with less than this left
MAX_ASKS_PER_SPECIALIST = 2        # per ticket; stops Boss <-> specialist ping-pong
BOSS_LIMITS = UsageLimits(request_limit=10, tool_calls_limit=14, total_tokens_limit=60_000)
TRIAGE_LIMITS = UsageLimits(request_limit=4, tool_calls_limit=6, total_tokens_limit=30_000)
SPECIALIST_REQUESTS, SPECIALIST_TOOL_CALLS, SPECIALIST_TOKENS = 6, 8, 40_000


@lru_cache(maxsize=1)
def get_model() -> Model:
    api_key = os.getenv("PORTKEY_API_KEY")
    if not api_key:
        raise RuntimeError("PORTKEY_API_KEY is missing from the workspace-root .env")
    provider = OpenAIProvider(api_key=api_key, base_url=os.getenv("PORTKEY_BASE_URL", DEFAULT_PORTKEY_URL))
    return OpenAIResponsesModel(MODEL_NAME, provider=provider)


def model_settings(*, parallel_tools: bool = True) -> OpenAIResponsesModelSettings:
    return OpenAIResponsesModelSettings(max_tokens=MAX_OUTPUT_TOKENS, parallel_tool_calls=parallel_tools, timeout=90)


def load_prompt(name: str) -> str:
    return (PROMPTS / f"{name}.md").read_text()


def mcp_client() -> Client:
    """A client for the campus-customs MCP server, started with the same command Claude Code uses (.mcp.json)."""
    entry = json.loads(MCP_CONFIG.read_text())["mcpServers"]["campus-customs"]
    command, args = resolve_entry(entry)
    work_dir = os.environ.get("CAMPUS_CUSTOMS_WORK_DIR")  # set only by tests and demos
    env = {"CAMPUS_CUSTOMS_WORK_DIR": work_dir} if work_dir else None
    return Client(StdioTransport(command=command, args=args, env=env))


def resolve_entry(entry: dict) -> tuple[str, list[str]]:
    """.mcp.json uses paths relative to the hw5 folder; make them absolute so any working directory works."""
    def absolute(part: str) -> str:
        return str(HW / part) if ("/" in part and not Path(part).is_absolute()) else part
    return absolute(entry["command"]), [absolute(a) for a in entry["args"]]


def mcp_toolset() -> MCPToolset:
    return MCPToolset(mcp_client(), id="campus-customs", tool_error_behavior="retry", max_retries=3)


def tools_for(toolset: MCPToolset, allowed: frozenset[str]):
    """The slice of the MCP server one agent may use. Human-only tools are never in any allowlist."""
    return toolset.filtered(lambda ctx, tool_def: tool_def.name in allowed)


def specialist_limits(remaining_tokens: int) -> UsageLimits:
    return UsageLimits(request_limit=SPECIALIST_REQUESTS, tool_calls_limit=SPECIALIST_TOOL_CALLS,
                       total_tokens_limit=max(1, min(SPECIALIST_TOKENS, remaining_tokens)))


# ---------------------------------------------------------------- reading tool results back (for output validators)


def _as_data(content: Any) -> Any:
    if isinstance(content, str):
        try:
            return json.loads(content)
        except ValueError:
            return content
    return content


def tool_results(ctx: RunContext[Any], tool_name: str) -> list[Any]:
    """Every successful result this agent got from `tool_name` during the current run."""
    found = []
    for msg in ctx.messages:
        if isinstance(msg, ModelRequest):
            for part in msg.parts:
                if isinstance(part, ToolReturnPart) and part.tool_name == tool_name:
                    found.append(_as_data(part.content))
    return found


def tool_calls(ctx: RunContext[Any], tool_name: str) -> list[dict]:
    """The arguments of every call this agent made to `tool_name` during the current run."""
    return [part.args_as_dict() for msg in ctx.messages if isinstance(msg, ModelResponse)
            for part in msg.parts if isinstance(part, ToolCallPart) and part.tool_name == tool_name]


# ---------------------------------------------------------------- the logged agent loop


def _summary(output: Any) -> str:
    if hasattr(output, "summary"):
        return output.summary
    if hasattr(output, "final_call"):
        return f"[{output.status}] {output.final_call}"
    return output.model_dump_json() if hasattr(output, "model_dump_json") else str(output)


async def run_logged(agent: Agent, name: str, prompt: str, deps: TeamDeps, *, limits: UsageLimits,
                     usage: RunUsage | None = None) -> Any:
    """Run one agent step by step, appending every loop step to the audit trail as it happens.

    Pass in `usage` to read the tokens spent even if the run fails partway.
    """
    audit, run_id, ticket_id = deps.audit, deps.run_id, deps.ticket_id
    usage = usage if usage is not None else RunUsage()
    step = 0
    audit.record(run_id=run_id, ticket_id=ticket_id, agent=name, step=step, event="agent_start", text=prompt,
                 detail={"limits": {"requests": limits.request_limit, "tool_calls": limits.tool_calls_limit,
                                    "tokens": limits.total_tokens_limit}})
    try:
        async with agent.iter(prompt, deps=deps, usage=usage, usage_limits=limits) as run:
            async for node in run:
                if Agent.is_call_tools_node(node):
                    step += 1
                    resp = node.model_response
                    calls = [{"tool": p.tool_name, "args": p.args_as_dict()} for p in resp.parts
                             if isinstance(p, ToolCallPart)]
                    text = " ".join(p.content for p in resp.parts if isinstance(p, TextPart)).strip() or None
                    audit.record(run_id=run_id, ticket_id=ticket_id, agent=name, step=step, event="model_response",
                                 tool_calls=calls, text=text,
                                 tokens={"input": resp.usage.input_tokens, "output": resp.usage.output_tokens,
                                         "agent_total": usage.total_tokens, "requests": usage.requests})
                elif Agent.is_model_request_node(node):
                    results = []
                    for part in node.request.parts:
                        if isinstance(part, ToolReturnPart):
                            results.append({"tool": part.tool_name, "result": _as_data(part.content)})
                        elif isinstance(part, RetryPromptPart):
                            results.append({"tool": part.tool_name or "output", "error": part.model_response()})
                    if results:
                        step += 1
                        audit.record(run_id=run_id, ticket_id=ticket_id, agent=name, step=step,
                                     event="tool_results", tool_results=results)
        output = run.result.output
    except Exception as exc:
        audit.record(run_id=run_id, ticket_id=ticket_id, agent=name, step=step + 1, event="agent_stopped",
                     text=str(exc), tokens={"agent_total": usage.total_tokens, "requests": usage.requests},
                     detail={"error": type(exc).__name__})
        raise
    audit.record(run_id=run_id, ticket_id=ticket_id, agent=name, step=step + 1, event="agent_end",
                 text=_summary(output),
                 tokens={"agent_total": usage.total_tokens, "requests": usage.requests, "tool_calls": usage.tool_calls})
    return output
