import { useState } from 'react'
import type { ApproveResult, BoardItem } from '../api'
import { dollars } from './CashBalance'

/** Board items a person has to act on. 'approved' but unpaid (cash was short) can be retried. */
export function needsAction(item: BoardItem): boolean {
  if (item.kind === 'payment_request') return item.status === 'pending_approval' || item.status === 'approved'
  if (item.kind === 'price_override') return item.status === 'pending_approval'
  if (item.kind === 'vendor_draft') return ['draft', 'on_hold', 'approved'].includes(item.status)
  return false
}

/** Finished decisions: paid, rejected, or an approved price (which never moves cash). */
const isDone = (item: BoardItem) =>
  item.status === 'paid' || item.status === 'rejected' || (item.kind === 'price_override' && item.status === 'approved')

const KIND_LABEL: Record<string, string> = {
  payment_request: 'Payment',
  vendor_draft: 'Purchase',
  price_override: 'Discount',
}

function detailLine(item: BoardItem): string | null {
  const d = item.details as Record<string, any>
  if (item.kind === 'payment_request' && item.ref_kind === 'invoice')
    return `${d.vendor} · due ${d.due_date}${d.days_overdue ? ` · ${d.days_overdue} days overdue` : ''}`
  if (item.kind === 'payment_request') return `${d.landlord} · due ${d.next_due} (in ${d.days_until_due} days)`
  if (item.kind === 'vendor_draft')
    return `est. ${dollars(d.est_cost ?? 0)} · ${d.lead_days} lead days${item.status === 'on_hold' ? ' · on hold: vendor has an open invoice' : ''}`
  if (item.kind === 'price_override')
    return `${d.discount_pct}% off list · ${dollars(d.margin_per_unit)} margin each · quote ${dollars(d.quote_total)}`
  return null
}

function amountOf(item: BoardItem): number | null {
  if (item.kind === 'vendor_draft') return (item.details as Record<string, any>).est_cost ?? null
  return item.amount ?? null
}

interface Props {
  board: BoardItem[]
  onApprove: (item: BoardItem, approvedBy: string) => Promise<ApproveResult>
}

export function Approvals({ board, onApprove }: Props) {
  const [name, setName] = useState(() => localStorage.getItem('approver') ?? '')
  const [working, setWorking] = useState<number | null>(null)
  const [messages, setMessages] = useState<Record<number, { ok: boolean; text: string }>>({})
  const [receipt, setReceipt] = useState<string | null>(null)

  const waiting = board.filter(needsAction)
  const drafts = board.filter((b) => b.kind === 'customer_draft' && b.status === 'draft')
  const done = board.filter(isDone)

  async function approve(item: BoardItem) {
    const who = name.trim()
    if (who.length < 2) {
      setMessages((m) => ({ ...m, [item.id]: { ok: false, text: 'Type your name above first. Approvals are signed.' } }))
      return
    }
    localStorage.setItem('approver', who)
    setWorking(item.id)
    try {
      const res = await onApprove(item, who)
      const paid = res.payment?.payment
      setReceipt(paid
        ? `Paid ${dollars(paid.amount)} for “${item.title.split(':')[0]}” · checking ${dollars(res.cash_before ?? 0)} → ${dollars(res.cash.balance)} · signed ${who}`
        : `Approved “${item.title.split(':')[0]}” · no money moved · signed ${who}`)
    } catch (e) {
      setMessages((m) => ({ ...m, [item.id]: { ok: false, text: (e as Error).message } }))
    } finally {
      setWorking(null)
    }
  }

  return (
    <section className="panel approvals" aria-labelledby="approvals-h">
      <h2 id="approvals-h">Waiting on you</h2>
      <p className="panel-sub">Agents only prepare. Nothing is paid until a person approves here.</p>
      <label className="approver">
        <span>Approving as</span>
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Your name" autoComplete="name" />
      </label>

      {receipt && (
        <div className="receipt" role="status">
          <span>{receipt}</span>
          <button onClick={() => setReceipt(null)} aria-label="Dismiss">×</button>
        </div>
      )}
      {waiting.length === 0 && <p className="empty">Nothing to approve right now.</p>}
      <ul className="approval-list">
        {waiting.map((item) => {
          const amount = amountOf(item)
          const msg = messages[item.id]
          return (
            <li key={item.id} className={`approval kind-${item.kind}`}>
              <div className="approval-top">
                <span className="approval-kind">{KIND_LABEL[item.kind]}</span>
                <span className="approval-ticket">ticket #{item.ticket_id} · by {item.created_by}</span>
              </div>
              <div className="approval-title">{item.title}</div>
              {detailLine(item) && <div className="approval-detail">{detailLine(item)}</div>}
              <div className="approval-row">
                {amount !== null && <span className="approval-amount">{dollars(amount)}</span>}
                <button className="btn btn-approve" disabled={working !== null} onClick={() => approve(item)}>
                  {working === item.id ? 'Approving…' : item.kind === 'price_override' ? 'Approve price' : 'Approve & pay'}
                </button>
              </div>
              {msg && <div className={msg.ok ? 'approval-ok' : 'approval-err'} role="status">{msg.text}</div>}
            </li>
          )
        })}
      </ul>

      {drafts.length > 0 && (
        <>
          <h3>Draft replies, never sent</h3>
          <ul className="draft-list">
            {drafts.map((d) => {
              const det = d.details as Record<string, string>
              return (
                <li key={d.id} className="draft">
                  <div className="draft-to">To {det.to} · ticket #{d.ticket_id}</div>
                  <div className="draft-subject">{det.subject}</div>
                  <p className="draft-body">{det.body}</p>
                </li>
              )
            })}
          </ul>
        </>
      )}

      {done.length > 0 && (
        <>
          <h3>Done</h3>
          <ul className="done-list">
            {done.map((d) => (
              <li key={d.id}>
                <span className={`done-mark status-${d.status}`}>{d.status}</span> {d.title}
                {d.decided_by && <span className="done-by"> · {d.decided_by}</span>}
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  )
}
