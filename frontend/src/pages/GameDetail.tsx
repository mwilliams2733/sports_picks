import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import BetModal from '../components/BetModal'
import OddsTile from '../components/OddsTile'
import { GameLines } from '../components/BoardGameCard'
import { useBoard } from '../hooks/useBoard'
import { usePropQuotes } from '../hooks/useQuotes'
import { groupProps, propTarget, startLabel } from '../lib/board'
import type { BetTarget, PropQuote } from '../types'

function PropTile({ gameId, player, line, outcome, q, offline, onPick }: {
  gameId: number; player: string; line: number; outcome: 'Over' | 'Under'; q?: PropQuote;
  offline: boolean; onPick: (t: BetTarget) => void
}) {
  const label = `${player} ${outcome} ${line}`
  const top = `${outcome === 'Over' ? 'O' : 'U'} ${line}`
  if (!q || !q.available) {
    return <OddsTile label={label} top={top} price={null} line={line} offline={offline}
      lockedReason={q && !q.available ? q.message : 'Not offered'} onSelect={() => {}} />
  }
  return <OddsTile label={label} top={top} price={q.odds} line={q.line} offline={offline}
    onSelect={() => onPick(propTarget(gameId, q))} />
}

export default function GameDetail() {
  const id = Number(useParams().id)
  const board = useBoard()
  const game = board.data?.games.find(g => g.id === id)
  const [tab, setTab] = useState<'lines' | 'props'>('lines')
  const props = usePropQuotes(game && tab === 'props' ? id : null)
  const offline = board.isError || props.isError
  const [target, setTarget] = useState<BetTarget | null>(null)

  if (board.isLoading) return <div className="sb-detail"><p className="sb-empty">Loading…</p></div>
  if (!game) {
    return (
      <div className="sb-detail">
        <p className="sb-empty">This game is off the board — it has started or isn't priced in the next 7 days.</p>
        <Link to="/">‹ Back to the lobby</Link>
      </div>
    )
  }
  return (
    <div className="sb-detail">
      <Link to="/">‹ Lobby</Link>
      <h1>{game.away_team} @ {game.home_team}</h1>
      <p className="sb-card-head" style={{ padding: 0 }}>{startLabel(game.start_time)}</p>
      {offline && <div role="alert" className="sb-offline">Board offline — prices unavailable</div>}
      <div className="sb-detail-tabs" role="tablist">
        <button role="tab" className="sb-sport-tab" aria-selected={tab === 'lines'} onClick={() => setTab('lines')}>Game Lines</button>
        <button role="tab" className="sb-sport-tab" aria-selected={tab === 'props'} onClick={() => setTab('props')}>Player Props</button>
      </div>
      {tab === 'lines' && <div className="sb-card"><GameLines game={game} offline={offline} onPick={setTarget} /></div>}
      {tab === 'props' && (
        props.isLoading ? <p className="sb-empty">Loading props…</p> :
        groupProps(props.data?.quotes ?? []).length === 0 ? <p className="sb-empty">No props priced for this game.</p> :
        groupProps(props.data?.quotes ?? []).map(([market, rows]) => (
          <section className="sb-prop-market" key={market}>
            <h2>{market}</h2>
            {rows.map(r => (
              <div className="sb-prop-row" key={`${r.player}|${r.line}`}>
                <span className="sb-team">{r.player}</span>
                <PropTile gameId={id} player={r.player} line={r.line} outcome="Over" q={r.over} offline={offline} onPick={setTarget} />
                <PropTile gameId={id} player={r.player} line={r.line} outcome="Under" q={r.under} offline={offline} onPick={setTarget} />
              </div>
            ))}
          </section>
        ))
      )}
      {target && <BetModal open onClose={() => setTarget(null)} {...target} />}
    </div>
  )
}
