// Talks to the FastAPI backend (backend/main.py). Every shop fact behind these routes comes from the MCP server.

export const API_URL = (import.meta.env.VITE_API_URL as string | undefined) ?? 'http://localhost:8000'

export interface RunBrief {
  run_id: string
  status: 'running' | 'done' | 'failed'
  started_at: string
  finished_at: string | null
}

export interface Ticket {
  id: number
  type: string
  requester: string
  subject: string
  sku: string | null
  size: string | null
  qty: number | null
  lease_id: number | null
  invoice_id: number | null
  status: string
  notes: string | null
  created_at: string
  state: 'open' | 'resolved'
  last_run: RunBrief | null
}

export interface HumanAction {
  kind: string
  board_id: number | null
  what: string
  amount: number | null
}

export interface BossDecision {
  ticket_id: number
  ticket_type: string
  worked_by: string[]
  findings: string[]
  final_call: string
  status: string
  needs_human: HumanAction[]
  customer_draft_board_id: number | null
  next_steps: string[]
}

export interface Run extends RunBrief {
  ticket_id: number
  refresh: boolean
  error: string | null
  result: {
    total_tokens: number
    stopped_early: string | null
    outcomes: { ticket_id: number; ok: boolean; decision: BossDecision | null; error: string | null; tokens: number }[]
  } | null
}

export interface AgentEvent {
  timestamp: string
  run_id: string
  ticket_id: number | null
  agent: string
  step: number
  event: string
  said: string | null
  tools_used: string[]
  tool_calls: { tool: string; args: string }[]
  tool_results: { tool: string; result?: string; error?: string }[]
  tokens: Record<string, number> | null
  detail: Record<string, unknown> | null
}

export interface BoardItem {
  id: number
  kind: 'payment_request' | 'price_override' | 'customer_draft' | 'vendor_draft' | 'boss_decision'
  ticket_id?: number
  status: string
  created_by: string
  created_at: string
  desk_date: string
  title: string
  details: Record<string, unknown>
  amount?: number
  ref_kind?: string
  ref_id?: number
  account?: string
  decided_by?: string
  decided_at?: string
  payment_id?: number
}

export interface Cash {
  account: string
  balance: number
  as_of: string
  desk_date: string
  pending_requests: number
  cash_after_pending: number
}

export interface ApproveResult {
  board_id: number
  approved: { status: string; kind: string; title?: string }
  payment?: { status: string; payment: { id: number; kind: string; amount: number; approved_by: string } }
  cash_before?: number
  cash: Cash
}

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(`${API_URL}${path}`, {
      ...init,
      headers: init?.body ? { 'Content-Type': 'application/json' } : undefined,
    })
  } catch {
    throw new ApiError(0, `Can't reach the back office at ${API_URL}. Is the backend running (uvicorn main:app --reload --port 8000)?`)
  }
  const body = await response.json().catch(() => null)
  if (!response.ok) {
    const detail = body?.detail
    const message = typeof detail === 'string' ? detail : detail?.reason ?? detail?.[0]?.msg ?? `Request failed (${response.status})`
    throw new ApiError(response.status, message)
  }
  return body as T
}

export const getTickets = () => request<{ today: string; tickets: Ticket[] }>('/api/tickets')
export const startRun = (ticketId: number) => request<Run>(`/api/tickets/${ticketId}/run`, { method: 'POST' })
export const getRun = (runId: string) => request<Run>(`/api/runs/${runId}`)
export const getBoard = () => request<{ count: number; items: BoardItem[] }>('/api/board')
export const getCash = () => request<Cash>('/api/cash')
export const reset = () => request<{ refreshed: { identical_to_original: boolean }; cash: Cash }>('/api/reset', { method: 'POST' })
export const approve = (boardId: number, approvedBy: string) =>
  request<ApproveResult>(`/api/board/${boardId}/approve`, { method: 'POST', body: JSON.stringify({ approved_by: approvedBy }) })

/** Events for one run, oldest first (the API returns newest first). */
export async function getEvents(runId: string): Promise<AgentEvent[]> {
  const data = await request<{ events: AgentEvent[] }>(`/api/events?run_id=${encodeURIComponent(runId)}&limit=500`)
  return data.events.slice().reverse()
}
