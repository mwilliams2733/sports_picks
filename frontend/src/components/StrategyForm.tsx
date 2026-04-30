import { useState } from 'react';
import type { StrategyData } from '../types';

interface Props {
  editing?: StrategyData | null;
  onSave: (data: { name: string; description: string; config: Record<string, unknown>; sport?: string | null }) => void;
  onCancel: () => void;
}

const STRATEGY_TYPES = ['ensemble', 'recent_form', 'value_only', 'sport_specific', 'prop_value'] as const;
const SPORTS = ['', 'nba', 'nfl', 'ncaab', 'ncaaf', 'mlb', 'boxing', 'mma'] as const;

export default function StrategyForm({ editing, onSave, onCancel }: Props) {
  const config = editing?.config ?? {};
  const [name, setName] = useState(editing?.name ?? '');
  const [description, setDescription] = useState(editing?.description ?? '');
  const [sport, setSport] = useState(editing?.sport ?? '');
  const [minEdge, setMinEdge] = useState(Number(config.min_edge ?? 5));
  const [kFactor, setKFactor] = useState(Number(config.k_factor ?? 20));
  const [lookback, setLookback] = useState(Number(config.lookback ?? 10));
  const [pdWeight, setPdWeight] = useState(Number((config.weights as Record<string, number>)?.pd ?? 0.3));
  const [eloWeight, setEloWeight] = useState(Number((config.weights as Record<string, number>)?.elo ?? 0.35));
  const [ratingWeight, setRatingWeight] = useState(Number((config.weights as Record<string, number>)?.rating ?? 0.25));
  const [hcaWeight, setHcaWeight] = useState(Number((config.weights as Record<string, number>)?.hca ?? 0.1));
  const [recentWeight, setRecentWeight] = useState(Number(config.recent_weight ?? 0.6));
  const [seasonWeight, setSeasonWeight] = useState(Number(config.season_weight ?? 0.4));
  const [propMinEdge, setPropMinEdge] = useState(Number(config.min_edge ?? 5));
  const [propLookback, setPropLookback] = useState(Number(config.lookback ?? 10));
  const [minMinutes, setMinMinutes] = useState(Number(config.min_minutes ?? 15));

  const isPropValue = name === 'prop_value';

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (isPropValue) {
      onSave({
        name, description, sport: sport || null,
        config: { recent_weight: recentWeight, season_weight: seasonWeight, min_edge: propMinEdge, lookback: propLookback, min_minutes: minMinutes, strategy_type: 'prop' },
      });
    } else {
      onSave({
        name, description, sport: sport || null,
        config: { min_edge: minEdge, k_factor: kFactor, lookback, weights: { pd: pdWeight, elo: eloWeight, rating: ratingWeight, hca: hcaWeight } },
      });
    }
  };

  const total = pdWeight + eloWeight + ratingWeight + hcaWeight;

  return (
    <form onSubmit={handleSubmit} className="form-card">
      <h4 style={{ marginBottom: '1rem', color: 'var(--text-primary)' }}>
        {editing ? 'Edit Strategy' : 'New Strategy'}
      </h4>

      <div className="form-grid" style={{ marginBottom: '0.75rem' }}>
        <div>
          <label className="input-label">Strategy Type</label>
          <select className="select" value={name} onChange={e => setName(e.target.value)} disabled={!!editing} style={{ width: '100%' }}>
            <option value="">Select...</option>
            {STRATEGY_TYPES.map(t => <option key={t} value={t}>{t.replace(/_/g, ' ')}</option>)}
          </select>
        </div>
        <div>
          <label className="input-label">Description</label>
          <input className="input" value={description} onChange={e => setDescription(e.target.value)} placeholder="Strategy description" />
        </div>
        <div>
          <label className="input-label">Sport</label>
          <select className="select" value={sport} onChange={e => setSport(e.target.value)} style={{ width: '100%' }}>
            {SPORTS.map(s => <option key={s} value={s}>{s || 'All Sports'}</option>)}
          </select>
        </div>
      </div>

      {isPropValue ? (
        <div className="form-grid" style={{ gridTemplateColumns: 'repeat(5, 1fr)', marginBottom: '0.75rem' }}>
          {[
            { label: 'Recent Weight', val: recentWeight, set: setRecentWeight, step: '0.05', min: '0', max: '1' },
            { label: 'Season Weight', val: seasonWeight, set: setSeasonWeight, step: '0.05', min: '0', max: '1' },
            { label: 'Min Edge %', val: propMinEdge, set: setPropMinEdge, step: '0.5', min: '0' },
            { label: 'Lookback', val: propLookback, set: setPropLookback, step: '1', min: '1' },
            { label: 'Min Minutes', val: minMinutes, set: setMinMinutes, step: '1', min: '0' },
          ].map(({ label, val, set, ...rest }) => (
            <div key={label}>
              <label className="input-label">{label}</label>
              <input className="input" type="number" value={val} onChange={e => set(+e.target.value)} {...rest} />
            </div>
          ))}
        </div>
      ) : (
        <>
          <div className="form-grid" style={{ marginBottom: '0.75rem' }}>
            <div>
              <label className="input-label">Min Edge %</label>
              <input className="input" type="number" step="0.5" min="0" value={minEdge} onChange={e => setMinEdge(+e.target.value)} />
            </div>
            <div>
              <label className="input-label">K-Factor</label>
              <input className="input" type="number" step="1" min="1" value={kFactor} onChange={e => setKFactor(+e.target.value)} />
            </div>
            <div>
              <label className="input-label">Lookback</label>
              <input className="input" type="number" step="1" min="1" value={lookback} onChange={e => setLookback(+e.target.value)} />
            </div>
          </div>

          <fieldset style={{ border: '1px solid var(--border-default)', borderRadius: 'var(--radius-md)', padding: '0.75rem', marginBottom: '0.75rem' }}>
            <legend style={{ color: 'var(--text-muted)', fontSize: '0.75rem', fontWeight: 600, padding: '0 0.3rem', textTransform: 'uppercase', letterSpacing: '0.04em' }}>
              Model Weights
            </legend>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.75rem' }}>
              {[
                { label: 'Point Diff', val: pdWeight, set: setPdWeight },
                { label: 'ELO', val: eloWeight, set: setEloWeight },
                { label: 'Net Rating', val: ratingWeight, set: setRatingWeight },
                { label: 'Home Court', val: hcaWeight, set: setHcaWeight },
              ].map(({ label, val, set }) => (
                <div key={label}>
                  <label className="input-label">{label}: {val.toFixed(2)}</label>
                  <input type="range" min="0" max="0.5" step="0.05" value={val} onChange={e => set(+e.target.value)} style={{ width: '100%', accentColor: 'var(--accent)' }} />
                </div>
              ))}
            </div>
            <div style={{ textAlign: 'right', color: total === 1 ? 'var(--green)' : 'var(--yellow)', fontSize: '0.75rem', marginTop: '0.3rem', fontWeight: 600 }}>
              Total: {total.toFixed(2)}
            </div>
          </fieldset>
        </>
      )}

      <div className="form-actions">
        <button type="button" className="btn btn-ghost" onClick={onCancel}>Cancel</button>
        <button type="submit" className="btn btn-primary" disabled={!name}>
          {editing ? 'Update' : 'Create'}
        </button>
      </div>
    </form>
  );
}
