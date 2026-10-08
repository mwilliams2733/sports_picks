import { Link } from 'react-router-dom'
import OddsTile from './OddsTile'
import { findGameQuote, gameSelection, startLabel, tileTop } from '../lib/board'
import { sameLeg } from '../lib/slip'
import { useSlip } from '../stores/slipStore'
import { resolveQuoteLabel } from '../lib/quotes'
import type { BoardGame, GamePickType, GameSide, SlipSelection } from '../types'

interface RowProps {
  game: BoardGame; team: string; side: 'HOME' | 'AWAY'; totalSide: 'Over' | 'Under';
  offline: boolean; onPick: (s: SlipSelection) => void;
}

function Tile({ game, pickType, side, offline, onPick }: {
  game: BoardGame; pickType: GamePickType; side: GameSide; offline: boolean; onPick: (s: SlipSelection) => void
}) {
  const q = findGameQuote(game.quotes, pickType, side)
  const sel = q && q.available ? gameSelection(game, q) : null
  const selected = useSlip(s => sel !== null && s.legs.some(l => sameLeg(l.leg, sel.leg)))
  const team = side === 'HOME' ? game.home_team : side === 'AWAY' ? game.away_team : ''
  if (!q || !q.available || !sel) {
    const name = pickType === 'over_under' ? `${side === 'Over' ? game.away_team : game.home_team} ${side}` : team
    return <OddsTile label={name} top={null} price={null} line={null} offline={offline}
      lockedReason={q && !q.available ? q.message : 'Not offered'} onSelect={() => {}} />
  }
  return <OddsTile label={pickType === 'moneyline' ? `${team} ML` : resolveQuoteLabel(q, game.home_team, game.away_team)}
    top={tileTop(q)} price={q.odds} line={q.line} offline={offline} selected={selected}
    onSelect={() => onPick(sel)} />
}

export function TeamRow({ game, team, side, totalSide, offline, onPick }: RowProps) {
  return (
    <div className="sb-card-row" data-testid="team-row" style={{ display: 'contents' }}>
      <span className="sb-team">{team}</span>
      <Tile game={game} pickType="spread" side={side} offline={offline} onPick={onPick} />
      <Tile game={game} pickType="moneyline" side={side} offline={offline} onPick={onPick} />
      <Tile game={game} pickType="over_under" side={totalSide} offline={offline} onPick={onPick} />
    </div>
  )
}

export function GameLines({ game, offline, onPick }: { game: BoardGame; offline: boolean; onPick: (s: SlipSelection) => void }) {
  return (
    <div className="sb-card-grid">
      <span />
      <span className="sb-col-head">Spread</span>
      <span className="sb-col-head">Money</span>
      <span className="sb-col-head">Total</span>
      <TeamRow game={game} team={game.away_team} side="AWAY" totalSide="Over" offline={offline} onPick={onPick} />
      <TeamRow game={game} team={game.home_team} side="HOME" totalSide="Under" offline={offline} onPick={onPick} />
    </div>
  )
}

export default function BoardGameCard({ game, offline, onPick }: { game: BoardGame; offline: boolean; onPick: (s: SlipSelection) => void }) {
  return (
    <article className="sb-card">
      <header className="sb-card-head">
        <span>{startLabel(game.start_time)}</span>
        <Link to={`/game/${game.id}`}>More ›</Link>
      </header>
      <GameLines game={game} offline={offline} onPick={onPick} />
      <Link className="sb-card-foot" to={`/game/${game.id}`}>
        {game.prop_count ? `+${game.prop_count} props ›` : 'Props ›'}
      </Link>
    </article>
  )
}
