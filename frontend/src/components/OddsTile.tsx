import { useEffect, useRef, useState } from 'react'
import { formatOdds } from '../lib/quotes'
import { priceMove, type PriceMove } from '../lib/board'

const OFFLINE = 'Board offline — prices unavailable'

interface Props {
  /** Accessible name: the side as a person reads it ("Cowboys +3"). */
  label: string
  top: string | null
  /** null = the server refused this side: the tile is locked. */
  price: number | null
  line: number | null
  lockedReason?: string
  offline?: boolean
  onSelect: () => void
}

export default function OddsTile({ label, top, price, line, lockedReason, offline = false, onSelect }: Props) {
  const locked = offline || price === null
  const prev = useRef<{ odds: number; line: number | null } | null>(null)
  const [flash, setFlash] = useState<PriceMove>(null)

  useEffect(() => {
    const next = price === null ? null : { odds: price, line }
    const move = priceMove(prev.current, next)
    prev.current = next
    if (!move) return
    setFlash(move)
    const t = setTimeout(() => setFlash(null), 1000)
    return () => clearTimeout(t)
  }, [price, line])

  const cls = ['sb-tile', locked && 'sb-tile-locked', flash && `sb-flash-${flash}`].filter(Boolean).join(' ')
  return (
    <button
      type="button"
      className={cls}
      disabled={locked}
      title={offline ? OFFLINE : locked ? lockedReason : undefined}
      aria-label={locked ? `${label} locked` : `${label} ${formatOdds(price as number)}`}
      onClick={onSelect}
    >
      {top && <span className="sb-tile-top">{top}</span>}
      <span className="sb-tile-price">{locked ? '🔒' : formatOdds(price as number)}</span>
    </button>
  )
}
