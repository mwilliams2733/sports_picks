import { useEffect, useState } from 'react';
import { api } from '../api/client';

interface CreditData {
  monthly_used: number;
  monthly_limit: number;
  monthly_remaining: number;
  daily_used: number;
  daily_target: number;
  api_requests_remaining: number | null;
}

export default function CreditUsage() {
  const [data, setData] = useState<CreditData | null>(null);

  useEffect(() => {
    api.credits.get().then(setData).catch(() => {});
  }, []);

  if (!data) return null;

  const monthPct = Math.round((data.monthly_used / data.monthly_limit) * 100);

  return (
    <div style={{
      display: 'flex', gap: '1rem', alignItems: 'center',
      padding: '0.5rem 1rem', fontSize: '0.8rem', color: '#94a3b8',
      borderTop: '1px solid #1e293b',
    }}>
      <span title="Monthly API credits used">
        Month: {data.monthly_used.toLocaleString()}/{data.monthly_limit.toLocaleString()} ({monthPct}%)
      </span>
      <span title="Today's API credits used">
        Today: {data.daily_used}/{data.daily_target}
      </span>
      {data.api_requests_remaining !== null && (
        <span title="Credits remaining per Odds API">
          API: {data.api_requests_remaining.toLocaleString()} left
        </span>
      )}
    </div>
  );
}
