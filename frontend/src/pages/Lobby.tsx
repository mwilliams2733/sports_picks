import { useState } from 'react'
import BoardGameCard from '../components/BoardGameCard'
import ModelPicksStrip from '../components/ModelPicksStrip'
import { useBoard } from '../hooks/useBoard'
import { etToday, groupByDay, sportTabs } from '../lib/board'
import { useSlip } from '../stores/slipStore'

export default function Lobby() {
  const board = useBoard()
  const games = board.data?.games ?? []
  // Any failed fetch locks the board, even with old data still on screen:
  // a price the server can't confirm must not be tappable.
  const offline = board.isError
  const tabs = sportTabs(games)
  const [chosen, setChosen] = useState<string | null>(null)
  const sport = chosen && tabs.includes(chosen) ? chosen : tabs[0] ?? null
  const shown = games.filter(g => g.sport === sport)
  const toggle = useSlip(s => s.toggle)

  return (
    <div className="sb-lobby">
      {offline && <div role="alert" className="sb-offline">Board offline — prices unavailable</div>}
      {board.isLoading && <p className="sb-empty">Loading the board…</p>}
      {board.isSuccess && games.length === 0 &&
        <p className="sb-empty">No games on the board in the next 7 days.</p>}
      {tabs.length > 0 && (
        <div className="sb-sport-tabs" role="tablist" aria-label="Sports">
          {tabs.map(s => (
            <button key={s} role="tab" className="sb-sport-tab" aria-selected={s === sport}
              onClick={() => setChosen(s)}>{s.toUpperCase()}</button>
          ))}
        </div>
      )}
      <ModelPicksStrip games={shown} offline={offline} onPick={toggle} />
      {groupByDay(shown, etToday(new Date())).map(day => (
        <section key={day.date} aria-label={day.label}>
          <h2 className="sb-day">{day.label}</h2>
          {day.games.map(game => <BoardGameCard key={game.id} game={game} offline={offline} onPick={toggle} />)}
        </section>
      ))}
    </div>
  )
}
