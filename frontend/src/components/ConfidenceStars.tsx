export default function ConfidenceStars({ rating }: { rating: number }) {
  return <span title={`${rating}/5 confidence`}>{'★'.repeat(rating)}{'☆'.repeat(5 - rating)}</span>;
}
