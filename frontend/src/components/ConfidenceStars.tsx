export default function ConfidenceStars({ rating }: { rating: number }) {
  return (
    <span className="stars" title={`${rating}/5 confidence`}>
      {Array.from({ length: 5 }, (_, i) => (
        <span key={i} className={i < rating ? 'star-filled' : 'star-empty'}>
          {i < rating ? '\u2605' : '\u2606'}
        </span>
      ))}
    </span>
  );
}
