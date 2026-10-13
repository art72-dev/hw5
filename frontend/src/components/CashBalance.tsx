import { useEffect, useRef, useState } from 'react'
import type { Cash } from '../api'

export const dollars = (n: number) =>
  n.toLocaleString('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: 2 })

/** Counts from the old value to the new one, so a payment is felt, not just shown. */
function useCountTo(target: number | undefined, ms = 900): number | undefined {
  const [shown, setShown] = useState(target)
  const from = useRef(target)
  useEffect(() => {
    if (target === undefined) return
    const start = from.current ?? target
    from.current = target
    if (start === target) {
      setShown(target)
      return
    }
    const t0 = performance.now()
    let frame = 0
    const tick = (now: number) => {
      const p = Math.min(1, (now - t0) / ms)
      const eased = 1 - (1 - p) ** 3
      setShown(start + (target - start) * eased)
      if (p < 1) frame = requestAnimationFrame(tick)
    }
    frame = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(frame)
  }, [target, ms])
  return shown
}

export function CashBalance({ cash }: { cash: Cash | null }) {
  const shown = useCountTo(cash?.balance)
  const [drop, setDrop] = useState<number | null>(null)
  const last = useRef<number | undefined>(undefined)

  useEffect(() => {
    if (cash === null) return
    if (last.current !== undefined && cash.balance < last.current) {
      setDrop(last.current - cash.balance)
      const timer = setTimeout(() => setDrop(null), 4000)
      last.current = cash.balance
      return () => clearTimeout(timer)
    }
    last.current = cash.balance
  }, [cash])

  return (
    <div className="cash" aria-live="polite">
      <div className="cash-label">Checking</div>
      <div className="cash-amount" title="cash_accounts.balance (checking)">
        {shown === undefined ? '—' : dollars(shown)}
        {drop !== null && <span className="cash-drop">−{dollars(drop)}</span>}
      </div>
      {cash && (
        <div className="cash-sub">
          {cash.pending_requests > 0
            ? <>after waiting approvals <b>{dollars(cash.cash_after_pending)}</b></>
            : <>nothing waiting · as of {cash.as_of}</>}
        </div>
      )}
    </div>
  )
}
