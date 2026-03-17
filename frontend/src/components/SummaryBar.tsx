import type { RecordData } from '../types'

interface Props {
  record: RecordData | null
  pickCount: number
  strategyName: string
  onStrategyChange?: (strategy: string) => void
}

export default function SummaryBar({ record, pickCount, strategyName }: Props) {
  return (
    <div className="summary-bar">
      <div className="summary-bar-item">
        <span className="summary-bar-label">Strategy</span>
        <span className="summary-bar-value">{strategyName}</span>
      </div>
      <div className="summary-bar-divider" />
      <div className="summary-bar-item">
        <span className="summary-bar-label">Today</span>
        <span className="summary-bar-value">
          {record ? `${record.wins}-${record.losses}` : '0-0'}
        </span>
      </div>
      <div className="summary-bar-divider" />
      <div className="summary-bar-item">
        <span className="summary-bar-label">ROI</span>
        <span className="summary-bar-value" style={{ color: record && record.roi > 0 ? 'var(--green)' : record && record.roi < 0 ? 'var(--red)' : undefined }}>
          {record ? `${record.roi > 0 ? '+' : ''}${record.roi}%` : '—'}
        </span>
      </div>
      <div className="summary-bar-divider" />
      <div className="summary-bar-item">
        <span className="summary-bar-label">Season</span>
        <span className="summary-bar-value">
          {record ? `${record.wins}-${record.losses}` : '—'}
        </span>
      </div>
      <div className="summary-bar-divider" />
      <div className="summary-bar-item">
        <span className="summary-bar-label">Picks</span>
        <span className="summary-bar-value">{pickCount}</span>
      </div>
    </div>
  )
}
