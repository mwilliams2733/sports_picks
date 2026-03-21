import { useState, useEffect } from 'react';
import { api } from '../api/client';
import type { DailyData, RunAllResult, VariantResult } from '../types';
import PerformanceChart from '../components/PerformanceChart';
import { useToast } from '../components/Toast';

const SPORTS = ['nba', 'nfl', 'ncaab', 'ncaaf'] as const;

const VARIANT_LABELS: Record<string, string> = {
  ensemble: 'Ensemble',
  recent_form: 'Recent Form',
  value_only: 'Value Only',
  sport_specific: 'Sport Specific',
  prop_value: 'Player Props',
};

const VARIANT_DESCRIPTIONS: Record<string, string> = {
  ensemble: 'Blends ELO, point diff, net rating, and home court with calibrated logistic regression',
  recent_form: 'Weights recent games heavily using EWMA momentum scoring',
  value_only: 'Conservative picks only when edge is very high (10%+)',
  sport_specific: 'Per-sport weight profiles with rest, venue, and conference adjustments',
  prop_value: 'Player prop predictions using season + recent averages with distribution analysis',
};

const ALL_VARIANTS = ['ensemble', 'recent_form', 'value_only', 'sport_specific', 'prop_value'];

export default function Backtesting() {
  const [daily, setDaily] = useState<DailyData[]>([]);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [pipelineRunning, setPipelineRunning] = useState(false);
  const [pipelineLastRun, setPipelineLastRun] = useState<string | null>(null);
  const { toast } = useToast();

  // Filters
  const [sport, setSport] = useState<string>('nba');
  const [selectedVariant, setSelectedVariant] = useState<string>('ensemble');
  const [marketFilter, setMarketFilter] = useState<string>('');

  // Date range — default to today through 10 days from now
  const [startDate, setStartDate] = useState(() => new Date().toISOString().slice(0, 10));
  const [endDate, setEndDate] = useState(() => {
    const d = new Date();
    d.setDate(d.getDate() + 10);
    return d.toISOString().slice(0, 10);
  });

  // Results
  const [results, setResults] = useState<RunAllResult | null>(null);

  // Run history
  const [runHistory, setRunHistory] = useState<{ timestamp: string; results: RunAllResult }[]>([]);
  const [selectedRunIndex, setSelectedRunIndex] = useState<number>(-1);

  useEffect(() => {
    api.stats.daily().then(setDaily).finally(() => setLoading(false));
  }, []);

  const handleRunAll = async () => {
    setRunning(true);
    setResults(null);
    setSelectedRunIndex(-1);
    try {
      const res = await api.backtest.runAll({ sport, start_date: startDate, end_date: endDate });
      setResults(res);
      const timestamp = new Date().toLocaleString();
      setRunHistory(prev => [{ timestamp, results: res }, ...prev]);
      setSelectedRunIndex(-1);
      const variantCount = Object.keys(res.variants).length;
      toast(`Backtest complete: ${variantCount} variants tested across ${res.games_count} games`, 'success');
    } catch (e: any) {
      toast(`Backtest error: ${e.message}`, 'error');
    } finally {
      setRunning(false);
    }
  };

  const handleRunPipeline = async () => {
    setPipelineRunning(true);
    try {
      const result = await api.pipeline.run();
      setPipelineLastRun(new Date().toLocaleString());
      toast(`Pipeline: ${result.games_stored} games, ${result.odds_stored} odds, ${result.props_stored} props`, 'success');
    } catch (e: any) {
      toast(`Pipeline error: ${e.message}`, 'error');
    } finally {
      setPipelineRunning(false);
    }
  };

  const handleSelectRun = (index: number) => {
    if (index === -1) {
      // Current run (latest)
      if (runHistory.length > 0) {
        setResults(runHistory[0].results);
      }
    } else {
      setResults(runHistory[index].results);
    }
    setSelectedRunIndex(index);
  };

  // Get current variant result
  const currentResult: VariantResult | null = results?.variants?.[selectedVariant] ?? null;
  const isProp = selectedVariant === 'prop_value';
  const sportMarkets = results?.sport_markets ?? [];

  // Filter prop results by market
  const getFilteredMarketResults = () => {
    if (!currentResult?.by_market) return {};
    if (!marketFilter) return currentResult.by_market;
    return Object.fromEntries(
      Object.entries(currentResult.by_market).filter(([k]) => k === marketFilter)
    );
  };

  // Summary row for all variants comparison
  const getVariantSummaries = () => {
    if (!results) return [];
    return ALL_VARIANTS
      .filter(v => results.variants[v])
      .map(v => {
        const r = results.variants[v];
        return {
          name: v,
          label: VARIANT_LABELS[v],
          wins: r.wins,
          losses: r.losses,
          total: r.total,
          winRate: r.win_rate ?? r.hit_rate ?? 0,
          roi: r.roi,
          profit: r.total_profit,
        };
      });
  };

  if (loading) return <div className="loading"><div className="spinner" /> Loading...</div>;

  return (
    <div>
      <div className="page-header">
        <h2 className="page-title">Backtesting</h2>
        <div style={{ display: 'flex', gap: '0.5rem' }}>
          <button className="btn btn-primary" onClick={handleRunAll} disabled={running}>
            {running ? <><div className="spinner" style={{ width: 14, height: 14, borderWidth: 2, borderTopColor: '#fff' }} /> Running All...</> : 'Run All Variants'}
          </button>
        </div>
      </div>

      {/* Pipeline Status */}
      <div className="pipeline-status">
        <span className="pipeline-status-dot" style={{ background: pipelineRunning ? 'var(--yellow)' : 'var(--green)' }} />
        <div>
          <div style={{ fontWeight: 600, fontSize: '0.85rem' }}>Pipeline Status</div>
          <div className="pipeline-status-label">
            {pipelineLastRun ? `Last run: ${pipelineLastRun}` : 'No pipeline runs this session'}
          </div>
        </div>
        <div style={{ marginLeft: 'auto' }}>
          <button className="btn btn-success" onClick={handleRunPipeline} disabled={pipelineRunning}>
            {pipelineRunning ? <><div className="spinner" style={{ width: 14, height: 14, borderWidth: 2 }} /> Running...</> : 'Run Now'}
          </button>
        </div>
      </div>

      {/* Controls Bar */}
      <div className="card" style={{ marginBottom: '1.25rem', display: 'flex', gap: '0.75rem', alignItems: 'end', flexWrap: 'wrap' }}>
        <div>
          <div className="input-label">Sport</div>
          <select className="input" value={sport} onChange={e => { setSport(e.target.value); setMarketFilter(''); }}>
            {SPORTS.map(s => <option key={s} value={s}>{s.toUpperCase()}</option>)}
          </select>
        </div>
        <div>
          <div className="input-label">Start Date</div>
          <input className="input" type="date" value={startDate} onChange={e => setStartDate(e.target.value)} />
        </div>
        <div>
          <div className="input-label">End Date</div>
          <input className="input" type="date" value={endDate} onChange={e => setEndDate(e.target.value)} />
        </div>
        {results && isProp && sportMarkets.length > 0 && (
          <div>
            <div className="input-label">Market</div>
            <select className="input" value={marketFilter} onChange={e => setMarketFilter(e.target.value)}>
              <option value="">All Markets</option>
              {sportMarkets.map(m => <option key={m.key} value={m.key}>{m.label}</option>)}
            </select>
          </div>
        )}
        {runHistory.length > 0 && (
          <div>
            <div className="input-label">Run History</div>
            <select
              className="input run-history-select"
              value={selectedRunIndex}
              onChange={e => handleSelectRun(Number(e.target.value))}
            >
              <option value={-1}>Latest Run</option>
              {runHistory.map((run, i) => (
                <option key={i} value={i}>
                  {run.timestamp} ({Object.keys(run.results.variants).length} variants)
                </option>
              ))}
            </select>
          </div>
        )}
      </div>

      {/* Progress Indicator */}
      {running && (
        <div className="progress-bar">
          <div className="progress-text">Running backtest across all variants...</div>
        </div>
      )}

      {/* Performance Chart */}
      <div className="section-header">Performance Over Time <span className="section-divider" /></div>
      <div className="card" style={{ marginBottom: '1.25rem' }}>
        <PerformanceChart data={daily} />
      </div>

      {/* Results Section */}
      {results && (
        <>
          {/* Variant Comparison Table */}
          <div className="section-header">
            All Variants — {sport.toUpperCase()}
            <span className="badge badge-default" style={{ marginLeft: '0.5rem' }}>{results.games_count} games</span>
            <span className="section-divider" />
          </div>
          <div className="table-wrap" style={{ marginBottom: '1.25rem' }}>
            <table className="table">
              <thead>
                <tr>
                  <th>Variant</th>
                  <th>Record</th>
                  <th>Total</th>
                  <th>Win %</th>
                  <th>ROI</th>
                  <th>Profit</th>
                </tr>
              </thead>
              <tbody>
                {getVariantSummaries().map(v => (
                  <tr
                    key={v.name}
                    onClick={() => setSelectedVariant(v.name)}
                    style={{
                      cursor: 'pointer',
                      background: selectedVariant === v.name ? 'var(--card-hover)' : undefined,
                    }}
                  >
                    <td className="font-medium">
                      {v.label}
                      {selectedVariant === v.name && <span className="badge badge-blue" style={{ marginLeft: '0.5rem' }}>Selected</span>}
                    </td>
                    <td className="mono">{v.wins}-{v.losses}</td>
                    <td className="mono text-muted">{v.total}</td>
                    <td className="mono" style={{ color: v.winRate >= 55 ? 'var(--green)' : v.winRate > 0 ? undefined : 'var(--text-muted)' }}>
                      {v.total > 0 ? `${v.winRate}%` : '\u2014'}
                    </td>
                    <td className="mono" style={{ color: v.roi > 0 ? 'var(--green)' : v.roi < 0 ? 'var(--red)' : undefined }}>
                      {v.total > 0 ? `${v.roi > 0 ? '+' : ''}${v.roi}%` : '\u2014'}
                    </td>
                    <td className="mono" style={{ color: v.profit > 0 ? 'var(--green)' : v.profit < 0 ? 'var(--red)' : undefined }}>
                      {v.total > 0 ? `${v.profit > 0 ? '+' : ''}${v.profit.toFixed(2)}u` : '\u2014'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Selected Variant Detail */}
          {currentResult && (
            <>
              <div className="section-header">
                {VARIANT_LABELS[selectedVariant]} Detail
                <span className="section-divider" />
              </div>

              <div style={{ fontSize: '0.85rem', color: 'var(--text-muted)', marginBottom: '0.75rem' }}>
                {VARIANT_DESCRIPTIONS[selectedVariant]}
              </div>

              {/* Stat Cards */}
              <div className="results-grid" style={{ marginBottom: '1rem' }}>
                <div className="stat-card" style={{ padding: '0.75rem' }}>
                  <div className="stat-label">Record</div>
                  <div className="stat-value" style={{ fontSize: '1.25rem' }}>
                    {currentResult.wins}-{currentResult.losses}
                    {(currentResult.pushes ?? 0) > 0 && `-${currentResult.pushes}`}
                  </div>
                </div>
                <div className="stat-card" style={{ padding: '0.75rem' }}>
                  <div className="stat-label">{isProp ? 'Hit Rate' : 'Win Rate'}</div>
                  <div className="stat-value" style={{
                    fontSize: '1.25rem',
                    color: (currentResult.win_rate ?? currentResult.hit_rate ?? 0) >= 55 ? 'var(--green)' : undefined
                  }}>
                    {currentResult.win_rate ?? currentResult.hit_rate ?? 0}%
                  </div>
                </div>
                <div className="stat-card" style={{ padding: '0.75rem' }}>
                  <div className="stat-label">ROI</div>
                  <div className="stat-value" style={{
                    fontSize: '1.25rem',
                    color: currentResult.roi > 0 ? 'var(--green)' : currentResult.roi < 0 ? 'var(--red)' : undefined
                  }}>
                    {currentResult.roi > 0 ? '+' : ''}{currentResult.roi}%
                  </div>
                </div>
                <div className="stat-card" style={{ padding: '0.75rem' }}>
                  <div className="stat-label">Profit</div>
                  <div className="stat-value" style={{
                    fontSize: '1.25rem',
                    color: currentResult.total_profit > 0 ? 'var(--green)' : currentResult.total_profit < 0 ? 'var(--red)' : undefined
                  }}>
                    {currentResult.total_profit > 0 ? '+' : ''}{currentResult.total_profit.toFixed(2)}u
                  </div>
                </div>
              </div>

              {/* Prop Market Breakdown */}
              {isProp && currentResult.by_market && (
                <div style={{ marginBottom: '1rem' }}>
                  <div className="input-label" style={{ marginBottom: '0.5rem' }}>By Market</div>
                  <div className="table-wrap">
                    <table className="table">
                      <thead>
                        <tr>
                          <th>Market</th>
                          <th>Record</th>
                          <th>Total</th>
                          <th>Hit Rate</th>
                        </tr>
                      </thead>
                      <tbody>
                        {Object.entries(getFilteredMarketResults()).map(([mkt, r]) => {
                          const total = r.wins + r.losses;
                          const rate = total > 0 ? ((r.wins / total) * 100).toFixed(1) : '0';
                          const label = sportMarkets.find(m => m.key === mkt)?.label ?? mkt;
                          return (
                            <tr key={mkt}>
                              <td className="font-medium">{label}</td>
                              <td className="mono">{r.wins}-{r.losses}</td>
                              <td className="mono text-muted">{total}</td>
                              <td className="mono" style={{ color: Number(rate) >= 55 ? 'var(--green)' : undefined }}>
                                {total > 0 ? `${rate}%` : '\u2014'}
                              </td>
                            </tr>
                          );
                        })}
                        {Object.keys(getFilteredMarketResults()).length === 0 && (
                          <tr><td colSpan={4} className="text-muted" style={{ textAlign: 'center' }}>No data for this market</td></tr>
                        )}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}

              {/* Prop Confidence Breakdown */}
              {isProp && currentResult.by_confidence && (
                <div>
                  <div className="input-label" style={{ marginBottom: '0.5rem' }}>By Confidence</div>
                  <div className="table-wrap">
                    <table className="table">
                      <thead>
                        <tr>
                          <th>Confidence</th>
                          <th>Record</th>
                          <th>Total</th>
                          <th>Hit Rate</th>
                        </tr>
                      </thead>
                      <tbody>
                        {Object.entries(currentResult.by_confidence)
                          .filter(([, r]) => r.wins + r.losses > 0)
                          .sort(([a], [b]) => Number(b) - Number(a))
                          .map(([conf, r]) => {
                            const total = r.wins + r.losses;
                            const rate = total > 0 ? ((r.wins / total) * 100).toFixed(1) : '0';
                            return (
                              <tr key={conf}>
                                <td className="font-medium">{'*'.repeat(Number(conf))} ({conf} star{Number(conf) !== 1 ? 's' : ''})</td>
                                <td className="mono">{r.wins}-{r.losses}</td>
                                <td className="mono text-muted">{total}</td>
                                <td className="mono" style={{ color: Number(rate) >= 55 ? 'var(--green)' : undefined }}>
                                  {total > 0 ? `${rate}%` : '\u2014'}
                                </td>
                              </tr>
                            );
                          })}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}
            </>
          )}
        </>
      )}

      {/* Empty state */}
      {!results && !running && (
        <div className="empty-state">
          <div className="empty-state-title">Select a sport and date range, then click "Run All Variants"</div>
          <div className="empty-state-sub">
            This will backtest all 5 strategy variants (Ensemble, Recent Form, Value Only, Sport Specific, Player Props) against historical data.
          </div>
        </div>
      )}
    </div>
  );
}
