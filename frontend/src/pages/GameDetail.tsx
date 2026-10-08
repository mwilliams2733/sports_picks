import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import OddsTile from '../components/OddsTile'
import { GameLines } from '../components/BoardGameCard'
import { useBoard } from '../hooks/useBoard'
import { usePropQuotes } from '../hooks/useQuotes'
import { groupProps, propSelection, startLabel } from '../lib/board'
import { sameLeg } from '../lib/slip'
import { useSlip } from '../stores/slipStore'
import type { BoardGame, PropQuote, SlipSelection } from '../types'

function PropTile({ game, player, line, outcome, q, offline, onPick }: {
  game: BoardGame; player: string; line: number; outcome: 'Over' | 'Under'; q?: PropQuote;
  offline: boolean; onPick: (s: SlipSelection) => void
}) {
  const label = `${player} ${outcome} ${line}`
  const top = `${outcome === 'Over' ? 'O' : 'U'} ${line}`
  const sel = q && q.available ? propSelection(game, q) : null
  const selected = useSlip(s => sel !== null && s.legs.some(l => sameLeg(l.leg, sel.leg)))
  if (!q || !q.available || !sel) {
    return <OddsTile label={label} top={top} price={null} line={line} offline={offline}
      lockedReason={q && !q.available ? q.message : 'Not offered'} onSelect={() => {}} />
  }
  return <OddsTile label={label} top={top} price={q.odds} line={q.line} offline={offline} selected={selected}
    onSelect={() => onPick(sel)} />
}

export default function GameDetail() {
  const id = Number(useParams().id)
  const board = useBoard()
  const game = board.data?.games.find(g => g.id === id)
  const [tab, setTab] = useState<'lines' | 'props'>('lines')
  const props = usePropQuotes(game && tab === 'props' ? id : null)
  const offline = board.isError || props.isError
  const toggle = useSlip(s => s.toggle)

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
      {tab === 'lines' && <div className="sb-card"><GameLines game={game} offline={offline} onPick={toggle} /></div>}
      {tab === 'props' && (
        props.isLoading ? <p className="sb-empty">Loading props…</p> :
        groupProps(props.data?.quotes ?? []).length === 0 ? <p className="sb-empty">No props priced for this game.</p> :
        groupProps(props.data?.quotes ?? []).map(([market, rows]) => (
          <section className="sb-prop-market" key={market}>
            <h2>{market}</h2>
            {rows.map(r => (
              <div className="sb-prop-row" key={`${r.player}|${r.line}`}>
                <span className="sb-team">{r.player}</span>
                <PropTile game={game} player={r.player} line={r.line} outcome="Over" q={r.over} offline={offline} onPick={toggle} />
                <PropTile game={game} player={r.player} line={r.line} outcome="Under" q={r.under} offline={offline} onPick={toggle} />
              </div>
            ))}
          </section>
        ))
      )}
    </div>
  )
}
