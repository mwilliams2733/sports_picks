import { useState, useEffect } from 'react';
import { api } from '../api/client';
import type { StrategyData, DailyData, BacktestResult } from '../types';
import StrategyList from '../components/StrategyList';
import PerformanceChart from '../components/PerformanceChart';
import StrategyForm from '../components/StrategyForm';

export default function Backtesting() {
  const [strategies, setStrategies] = useState<StrategyData[]>([]);
  const [daily, setDaily] = useState<DailyData[]>([]);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [editing, setEditing] = useState<StrategyData | null>(null);
  const [pipelineRunning, setPipelineRunning] = useState(false);
  const [backtestRunning, setBacktestRunning] = useState(false);
  const [backtestResult, setBacktestResult] = useState<BacktestResult | null>(null);

  const load = () => {
    setLoading(true);
    Promise.all([api.backtest.strategies(), api.stats.daily()])
      .then(([s, d]) => { setStrategies(s); setDaily(d); })
      .finally(() => setLoading(false));
  };
  useEffect(load, []);

  const handlePromote = async (id: number) => { await api.backtest.promote(id); load(); };

  const handleSave = async (data: { name: string; description: string; config: Record<string, unknown>; sport?: string | null }) => {
    if (editing) {
      await api.backtest.update(editing.id, { description: data.description, config: data.config });
    } else {
      await api.backtest.create({ name: data.name, description: data.description, config: data.config });
    }
    setShowForm(false);
    setEditing(null);
    load();
  };

  const handleEdit = (strategy: StrategyData) => {
    setEditing(strategy);
    setShowForm(true);
  };

  const handleRunPipeline = async () => {
    setPipelineRunning(true);
    try {
      const result = await api.pipeline.run();
      alert(`Pipeline complete: ${result.stats_fetched} stats, ${result.picks_generated} picks generated`);
      load();
    } catch (e: any) { alert(`Error: ${e.message}`); }
    finally { setPipelineRunning(false); }
  };

  const handleRunBacktest = async () => {
    const propStrategy = strategies.find(s => s.strategy_type === 'prop');
    if (!propStrategy) { alert('Create a prop_value strategy first'); return; }
    setBacktestRunning(true);
    try {
      const result = await api.backtest.run({
        strategy_id: propStrategy.id,
        start_date: '2026-02-01',
        end_date: '2026-03-14',
      });
      setBacktestResult(result);
    } catch (e: any) { alert(`Error: ${e.message}`); }
    finally { setBacktestRunning(false); }
  };

  if (loading) return <p>Loading...</p>;
  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1rem' }}>
        <h2 style={{ margin: 0 }}>Backtesting</h2>
        <div style={{ display: 'flex', gap: '0.5rem' }}>
          <button onClick={handleRunPipeline} disabled={pipelineRunning}
            style={{ padding: '0.4rem 1rem', background: '#059669', border: 'none',
                     borderRadius: '4px', color: '#fff', cursor: 'pointer', opacity: pipelineRunning ? 0.5 : 1 }}>
            {pipelineRunning ? 'Running...' : 'Run Pipeline'}
          </button>
          <button onClick={handleRunBacktest} disabled={backtestRunning}
            style={{ padding: '0.4rem 1rem', background: '#7c3aed', border: 'none',
                     borderRadius: '4px', color: '#fff', cursor: 'pointer', opacity: backtestRunning ? 0.5 : 1 }}>
            {backtestRunning ? 'Running...' : 'Run Backtest'}
          </button>
          {!showForm && (
            <button onClick={() => { setEditing(null); setShowForm(true); }}
              style={{ padding: '0.4rem 1rem', background: '#2563eb', border: 'none', borderRadius: '4px', color: '#fff', cursor: 'pointer' }}>
              + New Strategy
            </button>
          )}
        </div>
      </div>
      {showForm && (
        <div style={{ marginBottom: '1rem' }}>
          <StrategyForm editing={editing} onSave={handleSave} onCancel={() => { setShowForm(false); setEditing(null); }} />
        </div>
      )}
      <div style={{ display: 'flex', gap: '1.5rem' }}>
        <div style={{ flex: 1 }}>
          <h3 style={{ marginBottom: '0.5rem' }}>Strategy Variants</h3>
          <StrategyList strategies={strategies} onPromote={handlePromote} onEdit={handleEdit} />
        </div>
        <div style={{ flex: 2 }}>
          <h3 style={{ marginBottom: '0.5rem' }}>Performance Over Time</h3>
          <PerformanceChart data={daily} />
        </div>
      </div>

      {backtestResult && (
        <div style={{ marginTop: '1rem', padding: '1rem', background: '#1e293b', borderRadius: '8px' }}>
          <h4 style={{ margin: '0 0 0.5rem' }}>Prop Backtest Results</h4>
          <p>Record: {backtestResult.wins}-{backtestResult.losses} ({backtestResult.hit_rate}%)</p>
          <p>ROI: {backtestResult.roi}%</p>
          {backtestResult.by_market && (
            <div>
              <h5 style={{ margin: '0.5rem 0 0.3rem' }}>By Market</h5>
              {Object.entries(backtestResult.by_market).map(([mkt, r]) => (
                <p key={mkt} style={{ margin: '0.2rem 0', fontSize: '0.85rem' }}>
                  {mkt}: {r.wins}-{r.losses} ({r.hit_rate}%)
                </p>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
