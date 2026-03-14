import { useState, useEffect } from 'react';
import { api } from '../api/client';
import type { StrategyData, DailyData } from '../types';
import StrategyList from '../components/StrategyList';
import PerformanceChart from '../components/PerformanceChart';

export default function Backtesting() {
  const [strategies, setStrategies] = useState<StrategyData[]>([]);
  const [daily, setDaily] = useState<DailyData[]>([]);
  const [loading, setLoading] = useState(true);

  const load = () => {
    setLoading(true);
    Promise.all([api.backtest.strategies(), api.stats.daily()])
      .then(([s, d]) => { setStrategies(s); setDaily(d); })
      .finally(() => setLoading(false));
  };
  useEffect(load, []);

  const handlePromote = async (id: number) => { await api.backtest.promote(id); load(); };

  if (loading) return <p>Loading...</p>;
  return (
    <div>
      <h2 style={{ marginBottom: '1rem' }}>Backtesting</h2>
      <div style={{ display: 'flex', gap: '1.5rem' }}>
        <div style={{ flex: 1 }}>
          <h3 style={{ marginBottom: '0.5rem' }}>Strategy Variants</h3>
          <StrategyList strategies={strategies} onPromote={handlePromote} />
        </div>
        <div style={{ flex: 2 }}>
          <h3 style={{ marginBottom: '0.5rem' }}>Performance Over Time</h3>
          <PerformanceChart data={daily} />
        </div>
      </div>
    </div>
  );
}
