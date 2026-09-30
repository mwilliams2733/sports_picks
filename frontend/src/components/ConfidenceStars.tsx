import { CONFIDENCE_TOOLTIP } from '../constants/tooltips';
import { SHOW_STARS } from '../lib/display';

export default function ConfidenceStars({ rating }: { rating: number }) {
  if (!SHOW_STARS) return null;
  return (
    <span className="stars" title={`${rating}/5 confidence — ${CONFIDENCE_TOOLTIP}`}>
      {Array.from({ length: 5 }, (_, i) => (
        <span key={i} className={i < rating ? 'star-filled' : 'star-empty'}>
          {i < rating ? '★' : '☆'}
        </span>
      ))}
    </span>
  );
}
