import { useEffect, useMemo, useRef } from 'react'
import type { AgentEvent, Run, Ticket } from '../api'
import { lookOf, toFeed, workingAgent, type FeedItem } from '../agents'

function Monogram({ agent }: { agent: string }) {
  const look = lookOf(agent)
  return (
    <span className="monogram" style={{ color: look.ink, borderColor: look.ink }} aria-hidden="true">
      {look.monogram}
    </span>
  )
}

function Who({ agent }: { agent: string }) {
  const look = lookOf(agent)
  return (
    <span className="who">
      <b style={{ color: look.ink }}>{look.name}</b>
      <span className="who-role">{look.role}</span>
    </span>
  )
}

function Item({ item }: { item: FeedItem }) {
  switch (item.kind) {
    case 'system':
      return <li className="feed-system">{item.text}</li>
    case 'brief':
      return (
        <li className="feed-brief" style={{ ['--agent' as string]: lookOf(item.to).ink }}>
          <Monogram agent="Boss" />
          <div>
            <div className="brief-line"><b>Boss</b> <span className="arrow">→</span> <b style={{ color: lookOf(item.to).ink }}>{item.to}</b></div>
            <p>{item.text}</p>
          </div>
        </li>
      )
    case 'action':
      return (
        <li className="feed-action" style={{ ['--agent' as string]: lookOf(item.agent).ink }}>
          <span className="dot" />
          <span><b style={{ color: lookOf(item.agent).ink }}>{lookOf(item.agent).name}</b> {item.verb}</span>
          {item.args && <code>{item.args}</code>}
        </li>
      )
    case 'result':
      return (
        <li className="feed-result">
          <details>
            <summary><span className="tool">{item.tool}</span> {item.text}</summary>
            <pre>{item.raw}</pre>
          </details>
        </li>
      )
    case 'guardrail':
      return (
        <li className="feed-guardrail">
          <span className="guard-tag">Guardrail</span>
          <span><b>{lookOf(item.agent).name}</b> was sent back ({item.tool}): {item.text}</span>
        </li>
      )
    case 'say':
      return (
        <li className="feed-say" style={{ background: lookOf(item.agent).wash }}>
          <Monogram agent={item.agent} />
          <div><Who agent={item.agent} /><p>{item.text}</p></div>
        </li>
      )
    case 'report': {
      const look = lookOf(item.agent)
      return (
        <li className={`feed-report${item.final ? ' final' : ''} agent-${look.monogram.toLowerCase()}`}
            style={{ background: look.wash, ['--agent' as string]: look.ink }}>
          <Monogram agent={item.agent} />
          <div>
            <Who agent={item.agent} />
            {item.final && <div className="final-label">Final call</div>}
            <p>{item.text}</p>
          </div>
        </li>
      )
    }
    case 'stopped':
      return <li className="feed-stopped"><b>{lookOf(item.agent).name}</b> stopped: {item.text}</li>
  }
}

interface Props {
  ticket: Ticket | null
  run: Run | null
  events: AgentEvent[]
}

export function ActivityFeed({ ticket, run, events }: Props) {
  const feed = useMemo(() => toFeed(events), [events])
  const working = run?.status === 'running' ? workingAgent(events) ?? 'Boss' : null
  const end = useRef<HTMLLIElement>(null)

  useEffect(() => {
    if (run?.status === 'running') end.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
  }, [feed.length, run?.status])

  return (
    <section className="panel feed" aria-labelledby="feed-h">
      <h2 id="feed-h">{ticket ? `Ticket ${ticket.id} · the team at work` : 'The team at work'}</h2>
      <p className="panel-sub">
        {!ticket && 'Pick a ticket on the left.'}
        {ticket && !run && 'No run yet. Press “Start the team” and every step shows up here as it happens.'}
        {run?.status === 'running' && 'Live: each agent’s words and tools appear as they work.'}
        {run?.status === 'done' && `Finished ${run.finished_at ? new Date(run.finished_at).toLocaleTimeString() : ''}.`}
        {run?.status === 'failed' && `The run stopped: ${run.error ?? 'unknown error'}`}
      </p>
      <ol className="feed-list" aria-live="polite" aria-relevant="additions">
        {feed.map((item) => <Item key={item.id} item={item} />)}
        {working && (
          <li className="feed-working" ref={end}>
            <Monogram agent={working} />
            <span><b style={{ color: lookOf(working).ink }}>{lookOf(working).name}</b> is working<span className="ellipsis" /></span>
          </li>
        )}
        {!working && <li ref={end} className="feed-end" aria-hidden="true" />}
      </ol>
    </section>
  )
}
