import { useState } from 'react'
import BoardGameCard from '../components/BoardGameCard'
import ModelPicksStrip from '../components/ModelPicksStrip'
import { useBoard } from '../hooks/useBoard'
import { DATE_CHIPS, dateChips, etToday, filterByChip, groupByDay, sportTabs, type DateChip } from '../lib/board'
import { useSlip } from '../stores/slipStore'
import FeedTicker from '../components/FeedTicker'

export default function Lobby() {
  const board = useBoard()
  const games = board.data?.games ?? []
  // Any failed fetch locks the board, even with old data still on screen:
  // a price the server can't confirm must not be tappable.
  const offline = board.isError
  const tabs = sportTabs(games)
  const [chosen, setChosen] = useState<string | null>(null)
  const [chip, setChip] = useState<DateChip>('all')
  const sport = chosen && tabs.includes(chosen) ? chosen : tabs[0] ?? null
  const today = etToday(new Date())
  const ofSport = games.filter(g => g.sport === sport)
  const chips = dateChips(ofSport, today)
  const active = chips.includes(chip) ? chip : 'all'
  const shown = filterByChip(ofSport, active, today)
  const toggle = useSlip(s => s.toggle)

  return (
    <div className="sb-lobby">
      <FeedTicker />
      {offline && <div role="alert" className="sb-offline">Board offline — prices unavailable</div>}
      {board.isLoading && <p className="sb-empty">Loading the board…</p>}
      {board.isSuccess && games.length === 0 &&
        <p className="sb-empty">No games on the board in the next 14 days.</p>}
      {tabs.length > 0 && (
        <div className="sb-sport-tabs" role="tablist" aria-label="Sports">
          {tabs.map(s => (
            <button key={s} role="tab" className="sb-sport-tab" aria-selected={s === sport}
              onClick={() => { setChosen(s); setChip('all') }}>{s.toUpperCase()}</button>
          ))}
        </div>
      )}
      {chips.length > 1 && (
        <div className="sb-filter sb-date-chips" role="group" aria-label="When">
          {chips.map(c => (
            <button key={c} type="button" aria-pressed={c === active} onClick={() => setChip(c)}>{DATE_CHIPS[c]}</button>
          ))}
        </div>
      )}
      <ModelPicksStrip games={shown} offline={offline} onPick={toggle} />
      {groupByDay(shown, today).map(day => (
        <section key={day.date} aria-label={day.label}>
          <h2 className="sb-day">{day.label} · {day.games.length} {day.games.length === 1 ? 'game' : 'games'}</h2>
          {day.games.map(game => <BoardGameCard key={game.id} game={game} offline={offline} onPick={toggle} />)}
        </section>
      ))}
    </div>
  )
}
