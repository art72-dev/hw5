import type { Run } from '../api'
import { lookOf } from '../agents'

function duration(run: Run): string {
  if (!run.finished_at) return ''
  const secs = Math.round((new Date(run.finished_at).getTime() - new Date(run.started_at).getTime()) / 1000)
  return secs < 60 ? `${secs}s` : `${Math.floor(secs / 60)}m ${secs % 60}s`
}

export function RunSummary({ run }: { run: Run | null }) {
  if (!run || run.status === 'running') return null
  const outcome = run.result?.outcomes[0]
  const decision = outcome?.decision

  return (
    <section className="panel summary" aria-labelledby="summary-h">
      <h2 id="summary-h">What the team did</h2>
      {!decision && <p className="summary-error">{run.error ?? outcome?.error ?? 'The run ended without a decision.'}</p>}
      {decision && (
        <>
          <p className="summary-call">{decision.final_call}</p>
          <div className="summary-crew" aria-label="Agents who worked the ticket">
            <span className="chip" style={{ color: lookOf('Boss').ink, borderColor: lookOf('Boss').ink }}>Boss</span>
            {decision.worked_by.map((a) => (
              <span key={a} className="chip" style={{ color: lookOf(a).ink, borderColor: lookOf(a).ink }}>{a}</span>
            ))}
          </div>
          <ul className="summary-findings">
            {decision.findings.map((f, i) => <li key={i}>{f}</li>)}
          </ul>
          {decision.next_steps.length > 0 && (
            <>
              <h3>Next</h3>
              <ul className="summary-next">{decision.next_steps.map((s, i) => <li key={i}>{s}</li>)}</ul>
            </>
          )}
          <div className="summary-meta">
            {duration(run)} · {(outcome?.tokens ?? run.result?.total_tokens ?? 0).toLocaleString()} tokens · ticket marked <b>{decision.status}</b>
          </div>
        </>
      )}
    </section>
  )
}
