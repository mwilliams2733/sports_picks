import { useState } from 'react'
import { tailBet } from '../lib/tail'
import type { FeedItem } from '../types'

function ago(iso: string, now = Date.now()): string {
  const m = Math.max(0, Math.floor((now - new Date(iso).getTime()) / 60000))
  if (m < 1) return 'now'
  if (m < 60) return `${m}m`
  const h = Math.floor(m / 60)
  return h < 24 ? `${h}h` : `${Math.floor(h / 24)}d`
}

const DOT: Record<string, string> = { pick_won: 'won', pick_lost: 'lost', pick_pushed: 'push', cashed_out: 'push' }

export default function FeedList({ items, meId, limit }: { items: FeedItem[]; meId?: number; limit?: number }) {
  const [tailing, setTailing] = useState<number | null>(null)
  const shown = limit ? items.slice(0, limit) : items
  if (shown.length === 0) return <p className="sb-empty">No activity yet — place a bet to start the feed.</p>
  return (
    <ul className="sb-feed">
      {shown.map(e => {
        const legs = e.event_type === 'pick_placed' && Array.isArray(e.payload?.legs) ? e.payload.legs : []
        const canTail = legs.length > 0 && e.user_id !== meId
        return (
          <li key={e.id} className="sb-feed-item">
            <span className={`sb-dot sb-dot-${DOT[e.event_type] ?? 'pending'}`} />
            <span className="sb-feed-msg">{e.payload?.message ?? ''}</span>
            <span className="sb-feed-time">{ago(e.created_at)}</span>
            {canTail && (
              <button type="button" className="sb-tail" disabled={tailing === e.id}
                onClick={async () => { setTailing(e.id); try { await tailBet(legs) } finally { setTailing(null) } }}>
                {tailing === e.id ? 'Tailing…' : 'Tail'}
              </button>
            )}
          </li>
        )
      })}
    </ul>
  )
}
