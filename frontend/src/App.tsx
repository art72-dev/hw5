import { useCallback, useEffect, useRef, useState } from 'react'
import * as api from './api'
import type { AgentEvent, BoardItem, Cash, Run, Ticket } from './api'
import { ActivityFeed } from './components/ActivityFeed'
import { Approvals } from './components/Approvals'
import { CashBalance } from './components/CashBalance'
import { RunSummary } from './components/RunSummary'
import { TicketList } from './components/TicketList'

const POLL_MS = 1000

function prettyDate(iso?: string) {
  if (!iso) return ''
  return new Date(`${iso}T12:00:00`).toLocaleDateString('en-US', { month: 'long', day: 'numeric', year: 'numeric' })
}

export default function App() {
  const [tickets, setTickets] = useState<Ticket[]>([])
  const [board, setBoard] = useState<BoardItem[]>([])
  const [cash, setCash] = useState<Cash | null>(null)
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [run, setRun] = useState<Run | null>(null)
  const [events, setEvents] = useState<AgentEvent[]>([])
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [starting, setStarting] = useState(false)

  const fail = useCallback((e: unknown) => setError(e instanceof Error ? e.message : String(e)), [])

  const refreshAll = useCallback(async () => {
    const [t, b, c] = await Promise.all([api.getTickets(), api.getBoard(), api.getCash()])
    setTickets(t.tickets)
    setBoard(b.items)
    setCash(c)
    setError(null)
    return t.tickets
  }, [])

  // First load: pick a running ticket if there is one, else the first ticket.
  useEffect(() => {
    refreshAll()
      .then((ts) => setSelectedId((ts.find((t) => t.last_run?.status === 'running') ?? ts[0])?.id ?? null))
      .catch(fail)
  }, [refreshAll, fail])

  // Show the selected ticket's latest run (and its events).
  const selected = tickets.find((t) => t.id === selectedId) ?? null
  const latestRunId = selected?.last_run?.run_id
  useEffect(() => {
    if (!latestRunId) {
      setRun(null)
      setEvents([])
      return
    }
    let cancelled = false
    Promise.all([api.getRun(latestRunId), api.getEvents(latestRunId)])
      .then(([r, e]) => {
        if (!cancelled) {
          setRun(r)
          setEvents(e)
        }
      })
      .catch(fail)
    return () => { cancelled = true }
  }, [latestRunId, fail])

  // While a run is going (on any ticket), poll it. If it's the run on screen, stream its events too.
  const activeRunId = tickets.find((t) => t.last_run?.status === 'running')?.last_run?.run_id ?? null
  const shownRunId = useRef<string | undefined>(undefined)
  shownRunId.current = latestRunId
  useEffect(() => {
    if (!activeRunId) return
    const timer = setInterval(async () => {
      try {
        const r = await api.getRun(activeRunId)
        if (shownRunId.current === activeRunId) {
          setEvents(await api.getEvents(activeRunId))
          setRun(r)
        }
        if (r.status !== 'running') await refreshAll() // ticket flips to resolved; board and cash catch up
      } catch (err) {
        fail(err)
      }
    }, POLL_MS)
    return () => clearInterval(timer)
  }, [activeRunId, refreshAll, fail])

  async function startRun(ticketId: number) {
    setStarting(true)
    setNotice(null)
    try {
      const r = await api.startRun(ticketId)
      setRun(r)
      setEvents([])
      setTickets((ts) => ts.map((t) => (t.id === ticketId
        ? { ...t, last_run: { run_id: r.run_id, status: 'running', started_at: r.started_at, finished_at: null } }
        : t)))
      setError(null)
    } catch (e) {
      fail(e)
    } finally {
      setStarting(false)
    }
  }

  async function approve(item: BoardItem, approvedBy: string) {
    try {
      const result = await api.approve(item.id, approvedBy)
      setCash(result.cash)
      return result
    } finally {
      setBoard((await api.getBoard()).items)
      setCash(await api.getCash())
    }
  }

  async function reset() {
    if (!window.confirm('Reset the shop to the original data? This clears the board, payments and every ticket status.')) return
    try {
      await api.reset()
      setRun(null)
      setEvents([])
      await refreshAll()
      setNotice('Reset to the original data. Checking is back to its starting balance.')
    } catch (e) {
      fail(e)
    }
  }

  const anyRunning = starting || activeRunId !== null

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">CC</span>
          <div>
            <h1>Campus Customs <span>back office</span></h1>
            <div className="brand-sub">Agent team on gpt-6-luna · desk date {prettyDate(cash?.desk_date)}</div>
          </div>
        </div>
        <CashBalance cash={cash} />
        <button className="btn btn-quiet" onClick={reset} disabled={anyRunning} title="Reset the working database to the original values">
          Reset data
        </button>
      </header>

      {error && <div className="banner banner-error" role="alert">{error} <button onClick={() => setError(null)} aria-label="Dismiss">×</button></div>}
      {notice && <div className="banner banner-note" role="status">{notice} <button onClick={() => setNotice(null)} aria-label="Dismiss">×</button></div>}

      <main className="layout">
        <TicketList tickets={tickets} board={board} selectedId={selectedId} busy={anyRunning}
                    onSelect={setSelectedId} onRun={startRun} />
        <ActivityFeed ticket={selected} run={run} events={events} />
        <div className="side">
          <RunSummary run={run} />
          <Approvals board={board} onApprove={approve} />
        </div>
      </main>
    </div>
  )
}
