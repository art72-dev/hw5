// Who each agent is on screen, and how raw audit events become a readable feed.

import type { AgentEvent } from './api'

export interface AgentLook {
  name: string
  monogram: string
  role: string
  ink: string   // main colour: monogram stamp, name, rule
  wash: string  // pale background for its notes
}

export const AGENTS: Record<string, AgentLook> = {
  Boss: { name: 'Boss', monogram: 'B', role: 'reads every ticket · makes the call', ink: '#4a3b2c', wash: '#efe7dc' },
  Inventory: { name: 'Inventory', monogram: 'In', role: 'counts the shelves', ink: '#5d6a40', wash: '#e8eadd' },
  Accounting: { name: 'Accounting', monogram: 'Ac', role: 'watches every dollar', ink: '#8c6a1f', wash: '#f2ead6' },
  Facilities: { name: 'Facilities', monogram: 'Fa', role: 'minds the lease', ink: '#55666b', wash: '#e3e7e6' },
  'Customer Service': { name: 'Customer Service', monogram: 'CS', role: 'writes to customers', ink: '#a2552f', wash: '#f4e4d9' },
  runner: { name: 'Back office', monogram: '·', role: 'system', ink: '#8b8377', wash: '#f0ece5' },
}

export const lookOf = (agent: string): AgentLook => AGENTS[agent] ?? AGENTS.runner

const TOOL_VERBS: Record<string, string> = {
  list_open_tickets: 'read every open ticket',
  get_ticket: 'read the ticket',
  check_stock: 'checked the shelf',
  check_vendor_status: 'checked the vendors',
  check_rent_due: 'checked the lease',
  get_cash_position: 'checked the cash',
  check_margin: 'worked out the margin',
  get_board: 'looked at the board',
  request_payment: 'asked a person to approve a payment',
  request_price_override: 'asked a person to approve a price',
  draft_vendor_order: 'drafted a restock order',
  draft_customer_message: 'drafted a reply',
  update_ticket: 'recorded the final call',
}

export type FeedItem =
  | { kind: 'system'; id: string; text: string }
  | { kind: 'brief'; id: string; to: string; text: string }
  | { kind: 'action'; id: string; agent: string; tool: string; verb: string; args: string }
  | { kind: 'result'; id: string; agent: string; tool: string; text: string; raw: string }
  | { kind: 'guardrail'; id: string; agent: string; tool: string; text: string }
  | { kind: 'say'; id: string; agent: string; text: string }
  | { kind: 'report'; id: string; agent: string; text: string; final: boolean }
  | { kind: 'stopped'; id: string; agent: string; text: string }

const hidden = (tool: string) => tool.startsWith('ask_') || tool.startsWith('final_result')

function parse(raw: string | undefined): unknown {
  if (!raw) return null
  try {
    return JSON.parse(raw)
  } catch {
    return null
  }
}

const clip = (text: string, n: number) => (text.length > n ? `${text.slice(0, n - 1)}…` : text)
const money = (n: unknown) => (typeof n === 'number' ? `$${n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : '')

function argLine(raw: string): string {
  const data = parse(raw)
  if (!data || typeof data !== 'object') return clip(raw, 90)
  return Object.entries(data as Record<string, unknown>)
    .filter(([key]) => !['reason', 'body', 'note', 'brief'].includes(key))
    .map(([, value]) => String(value))
    .join(' · ')
}

/** Read one field from a result even when the audit trail cut the JSON short (results are capped at 400 chars). */
function pick(raw: string, key: string): string | null {
  const m = raw.match(new RegExp(`"${key}":\\s*(?:"([^"]*)"|(-?[\\d.]+|true|false|null))`))
  return m ? (m[1] ?? m[2]) : null
}
const count = (raw: string, pattern: string) => raw.split(pattern).length - 1

function cutShortLine(tool: string, raw: string): string {
  switch (tool) {
    case 'get_ticket':
      return `${pick(raw, 'type') ?? 'ticket'} from ${pick(raw, 'requester') ?? '?'}${pick(raw, 'invoice_id') && pick(raw, 'invoice_id') !== 'null' ? ` · linked invoice #${pick(raw, 'invoice_id')}` : ''}`
    case 'check_stock':
      return `${pick(raw, 'on_hand')} on hand · short ${pick(raw, 'short_by')} · ${pick(raw, 'location')}`
    case 'check_vendor_status': {
      const blocked = count(raw, '"can_ship": false')
      const name = pick(raw, 'name')
      return blocked ? `${name ?? 'Vendor'}${count(raw, '"vendor_id"') > 1 ? ' and others' : ''}: ${blocked} blocked by an open invoice` : `${name ?? 'Vendors'} can ship`
    }
    case 'get_cash_position':
      return `Cash and every open bill checked`
    case 'list_open_tickets':
      return `${pick(raw, 'count') ?? 'All'} open tickets`
    case 'get_board':
      return `${pick(raw, 'count') ?? 'Some'} items on the board`
    default: {
      const title = pick(raw, 'title')
      if (title) return `${(pick(raw, 'board_status') ?? pick(raw, 'status') ?? '').replace('_', ' ')}: ${title}`
      return clip(raw.replace(/[{}"]/g, ''), 110)
    }
  }
}

function resultLine(tool: string, raw: string): string {
  const d = parse(raw) as Record<string, any> | null
  if (!d || typeof d !== 'object') return cutShortLine(tool, raw)
  if (d.status === 'refused') return `Refused: ${d.reason}`
  if (d.status === 'already_on_board') return `Already on the board (#${d.board_id}), not posted twice`
  switch (tool) {
    case 'check_stock':
      return `${d.on_hand} on hand · short ${d.short_by} · ${d.location}`
    case 'check_rent_due':
      return `${money(d.monthly_rent)} ${d.status} (next due ${d.next_due})`
    case 'check_margin':
      return `${money(d.proposed_unit_price)} each = ${d.discount_pct}% off · ${money(d.margin_per_unit)} margin per unit`
    case 'request_payment':
      return `On the board for approval: ${money(d.amount)} · nothing paid yet`
    case 'request_price_override':
      return `On the board for approval: ${d.title ?? 'discount'}`
    case 'draft_vendor_order':
      return `${d.board_status === 'on_hold' ? 'On hold' : 'Draft'}: ${d.title}`
    case 'draft_customer_message':
      return `Draft saved for ${d.to}, not sent`
    case 'update_ticket':
      return `Ticket ${d.ticket_id}: ${d.from} → ${d.to}`
    case 'get_cash_position':
      return d.totals ? `Cash ${money(d.totals.cash)} · after waiting approvals ${money(d.totals.cash_after_pending)}` : 'Cash checked'
    default:
      return clip(JSON.stringify(d).replace(/[{}"]/g, ''), 150)
  }
}

/** Turn one run's audit events (oldest first) into what the dashboard shows. */
export function toFeed(events: AgentEvent[]): FeedItem[] {
  const feed: FeedItem[] = []
  events.forEach((e, i) => {
    const id = `${e.timestamp}-${i}`
    switch (e.event) {
      case 'refresh':
        feed.push({ kind: 'system', id, text: 'Working database reset to the original before this run (rule 8).' })
        break
      case 'ticket_start':
        feed.push({ kind: 'system', id, text: `Ticket ${e.ticket_id} handed to the Boss.` })
        break
      case 'agent_start': {
        const brief = e.said?.split('Brief from the Boss:')[1]
        if (e.agent !== 'Boss' && brief) feed.push({ kind: 'brief', id, to: e.agent, text: brief.trim() })
        break
      }
      case 'model_response':
        e.tool_calls.filter((c) => !hidden(c.tool)).forEach((c, j) =>
          feed.push({ kind: 'action', id: `${id}-${j}`, agent: e.agent, tool: c.tool, verb: TOOL_VERBS[c.tool] ?? c.tool, args: argLine(c.args) }))
        if (e.said) feed.push({ kind: 'say', id: `${id}-say`, agent: e.agent, text: e.said })
        break
      case 'tool_results':
        e.tool_results.filter((r) => !hidden(r.tool)).forEach((r, j) => {
          if (r.error) {
            feed.push({ kind: 'guardrail', id: `${id}-${j}`, agent: e.agent, tool: r.tool, text: clip(r.error, 220) })
          } else if (r.result) {
            feed.push({ kind: 'result', id: `${id}-${j}`, agent: e.agent, tool: r.tool, text: resultLine(r.tool, r.result), raw: r.result })
          }
        })
        e.tool_results.filter((r) => r.error && r.tool.startsWith('final_result')).forEach((r, j) =>
          feed.push({ kind: 'guardrail', id: `${id}-out-${j}`, agent: e.agent, tool: 'answer check', text: clip(r.error ?? '', 220) }))
        break
      case 'agent_end':
        if (e.said) feed.push({ kind: 'report', id, agent: e.agent, text: e.said.replace(/^\[\w+\]\s*/, ''), final: e.agent === 'Boss' })
        break
      case 'agent_stopped':
        feed.push({ kind: 'stopped', id, agent: e.agent, text: e.said ?? 'Stopped.' })
        break
      case 'ticket_end': {
        const tokens = e.tokens?.ticket_total
        feed.push({ kind: 'system', id, text: `Run finished${tokens ? ` · ${tokens.toLocaleString()} tokens` : ''}.` })
        break
      }
    }
  })
  return feed
}

/** The agent working right now: started, not yet finished. */
export function workingAgent(events: AgentEvent[]): string | null {
  const open: string[] = []
  for (const e of events) {
    if (e.event === 'agent_start') open.push(e.agent)
    if (e.event === 'agent_end' || e.event === 'agent_stopped') open.splice(open.lastIndexOf(e.agent), 1)
  }
  return open.at(-1) ?? null
}
