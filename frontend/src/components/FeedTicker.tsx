import { Link } from 'react-router-dom'
import { useFeed } from '../hooks/useFeed'

/** The Lobby's one-line ticker: the latest league activity, to /leaders. */
export default function FeedTicker() {
  const feed = useFeed(5)
  const latest = (feed.data ?? []).find(e => typeof e.payload?.message === 'string' && e.payload.message)
  if (!latest) return null
  return (
    <Link to="/leaders" className="sb-ticker">
      <span className="sb-ticker-tag">LEAGUE</span>
      <span className="sb-ticker-msg">{latest.payload.message}</span>
    </Link>
  )
}
