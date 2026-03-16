import type { RecordData } from '../types';

interface Props {
  record: RecordData | null;
  pickCount: number;
  strategyName: string;
}

export default function SummaryCards({ record, pickCount, strategyName }: Props) {
  return (
    <div className="card-grid">
      <div className="stat-card">
        <div className="stat-label">Active Strategy</div>
        <div className="stat-value">{strategyName}</div>
        <div className="stat-sub text-green">
          {record ? `${record.win_rate}% Win Rate` : '\u2014'}
        </div>
      </div>
      <div className="stat-card">
        <div className="stat-label">Today's Picks</div>
        <div className="stat-value">{pickCount}</div>
        <div className="stat-sub text-muted">
          {pickCount} games analyzed
        </div>
      </div>
      <div className="stat-card">
        <div className="stat-label">Season ROI</div>
        <div className="stat-value" style={{ color: record && record.roi > 0 ? 'var(--green)' : record && record.roi < 0 ? 'var(--red)' : undefined }}>
          {record ? `${record.roi > 0 ? '+' : ''}${record.roi}%` : '\u2014'}
        </div>
        <div className="stat-sub text-muted">
          {record ? `${record.wins}-${record.losses} Record` : '\u2014'}
        </div>
      </div>
    </div>
  );
}
