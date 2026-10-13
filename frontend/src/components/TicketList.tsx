import type { BoardItem, Ticket } from '../api'
import { needsAction } from './Approvals'

const TYPE_LABEL: Record<string, string> = {
  customer_order: 'Customer order',
  rent_notice: 'Rent notice',
  price_override: 'Discount request',
}

export function ticketStage(ticket: Ticket): 'open' | 'working' | 'resolved' | 'blocked' | 'failed' {
  if (ticket.last_run?.status === 'running') return 'working'
  if (ticket.state === 'resolved') return 'resolved'
  if (ticket.status === 'blocked') return 'blocked'
  if (ticket.last_run?.status === 'failed') return 'failed'
  return 'open'
}

const STAGE_TEXT = { open: 'Open', working: 'Team working…', resolved: 'Resolved', blocked: 'Blocked', failed: 'Run failed' }

interface Props {
  tickets: Ticket[]
  board: BoardItem[]
  selectedId: number | null
  busy: boolean
  onSelect: (id: number) => void
  onRun: (id: number) => void
}

export function TicketList({ tickets, board, selectedId, busy, onSelect, onRun }: Props) {
  return (
    <section className="panel tickets" aria-labelledby="tickets-h">
      <h2 id="tickets-h">Tickets</h2>
      <p className="panel-sub">Pick one and hand it to the team.</p>
      <ol className="ticket-list">
        {tickets.map((t) => {
          const stage = ticketStage(t)
          const waiting = board.filter((b) => b.ticket_id === t.id && needsAction(b)).length
          const selected = t.id === selectedId
          return (
            <li key={t.id}>
              <article
                className={`ticket-card stage-${stage}${selected ? ' selected' : ''}`}
                onClick={() => onSelect(t.id)}
                onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && (e.preventDefault(), onSelect(t.id))}
                tabIndex={0}
                aria-current={selected ? 'true' : undefined}
                aria-label={`Ticket ${t.id}, ${t.subject}, ${STAGE_TEXT[stage]}`}
              >
                <div className="ticket-top">
                  <span className="ticket-no">#{t.id}</span>
                  <span className={`pill pill-${stage}`}>{STAGE_TEXT[stage]}</span>
                </div>
                <h3>{t.subject}</h3>
                <div className="ticket-meta">{TYPE_LABEL[t.type] ?? t.type} · {t.requester}</div>
                {t.notes && <p className="ticket-note">“{t.notes}”</p>}
                {waiting > 0 && <div className="ticket-waiting">{waiting} waiting on you</div>}
                {stage === 'resolved' && <span className="stamp" aria-hidden="true">Resolved</span>}
                {stage === 'blocked' && <span className="stamp stamp-blocked" aria-hidden="true">Blocked</span>}
                {selected && stage !== 'resolved' && stage !== 'working' && (
                  <button
                    className="btn btn-primary run-btn"
                    disabled={busy}
                    onClick={(e) => { e.stopPropagation(); onRun(t.id) }}
                    title={busy ? 'Another run is going. One at a time.' : 'Start the agent team on this ticket'}
                  >
                    Start the team
                  </button>
                )}
              </article>
            </li>
          )
        })}
      </ol>
    </section>
  )
}
