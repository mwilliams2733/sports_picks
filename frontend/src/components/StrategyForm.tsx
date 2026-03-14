import { useState } from 'react';
import type { StrategyData } from '../types';

interface Props {
  editing?: StrategyData | null;
  onSave: (data: { name: string; description: string; config: Record<string, unknown>; sport?: string | null }) => void;
  onCancel: () => void;
}

const STRATEGY_TYPES = ['ensemble', 'recent_form', 'value_only', 'sport_specific', 'prop_value'] as const;
const SPORTS = ['', 'nba', 'nfl', 'ncaab', 'ncaaf'] as const;

const inputStyle: React.CSSProperties = {
  width: '100%', padding: '0.4rem', background: '#2a2a2a', border: '1px solid #444',
  borderRadius: '4px', color: '#e0e0e0', fontSize: '0.9rem',
};
const labelStyle: React.CSSProperties = { display: 'block', marginBottom: '0.3rem', color: '#aaa', fontSize: '0.85rem' };
const sectionStyle: React.CSSProperties = { marginBottom: '0.75rem' };

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

  // Prop-specific config fields
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
        name,
        description,
        sport: sport || null,
        config: {
          recent_weight: recentWeight,
          season_weight: seasonWeight,
          min_edge: propMinEdge,
          lookback: propLookback,
          min_minutes: minMinutes,
          strategy_type: 'prop',
        },
      });
    } else {
      onSave({
        name,
        description,
        sport: sport || null,
        config: {
          min_edge: minEdge,
          k_factor: kFactor,
          lookback,
          weights: { pd: pdWeight, elo: eloWeight, rating: ratingWeight, hca: hcaWeight },
        },
      });
    }
  };

  return (
    <form onSubmit={handleSubmit} style={{ background: '#1e1e1e', padding: '1rem', borderRadius: '8px', border: '1px solid #333' }}>
      <h4 style={{ margin: '0 0 0.75rem', color: '#e0e0e0' }}>{editing ? 'Edit Strategy' : 'New Strategy'}</h4>

      <div style={sectionStyle}>
        <label style={labelStyle}>Strategy Type</label>
        <select value={name} onChange={e => setName(e.target.value)} style={inputStyle} disabled={!!editing}>
          <option value="">Select...</option>
          {STRATEGY_TYPES.map(t => <option key={t} value={t}>{t.replace(/_/g, ' ')}</option>)}
        </select>
      </div>

      <div style={sectionStyle}>
        <label style={labelStyle}>Description</label>
        <input value={description} onChange={e => setDescription(e.target.value)} style={inputStyle} placeholder="Strategy description" />
      </div>

      <div style={sectionStyle}>
        <label style={labelStyle}>Sport (leave empty for global)</label>
        <select value={sport} onChange={e => setSport(e.target.value)} style={inputStyle}>
          {SPORTS.map(s => <option key={s} value={s}>{s || 'All Sports'}</option>)}
        </select>
      </div>

      {isPropValue ? (
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '0.5rem', ...sectionStyle }}>
          <div>
            <label style={labelStyle}>Recent Weight</label>
            <input type="number" step="0.05" min="0" max="1" value={recentWeight} onChange={e => setRecentWeight(+e.target.value)} style={inputStyle} />
          </div>
          <div>
            <label style={labelStyle}>Season Weight</label>
            <input type="number" step="0.05" min="0" max="1" value={seasonWeight} onChange={e => setSeasonWeight(+e.target.value)} style={inputStyle} />
          </div>
          <div>
            <label style={labelStyle}>Min Edge %</label>
            <input type="number" step="0.5" min="0" value={propMinEdge} onChange={e => setPropMinEdge(+e.target.value)} style={inputStyle} />
          </div>
          <div>
            <label style={labelStyle}>Lookback Games</label>
            <input type="number" step="1" min="1" value={propLookback} onChange={e => setPropLookback(+e.target.value)} style={inputStyle} />
          </div>
          <div>
            <label style={labelStyle}>Min Minutes</label>
            <input type="number" step="1" min="0" value={minMinutes} onChange={e => setMinMinutes(+e.target.value)} style={inputStyle} />
          </div>
        </div>
      ) : (
        <>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '0.5rem', ...sectionStyle }}>
            <div>
              <label style={labelStyle}>Min Edge %</label>
              <input type="number" step="0.5" min="0" value={minEdge} onChange={e => setMinEdge(+e.target.value)} style={inputStyle} />
            </div>
            <div>
              <label style={labelStyle}>K-Factor</label>
              <input type="number" step="1" min="1" value={kFactor} onChange={e => setKFactor(+e.target.value)} style={inputStyle} />
            </div>
            <div>
              <label style={labelStyle}>Lookback Games</label>
              <input type="number" step="1" min="1" value={lookback} onChange={e => setLookback(+e.target.value)} style={inputStyle} />
            </div>
          </div>

          <fieldset style={{ border: '1px solid #444', borderRadius: '4px', padding: '0.75rem', marginBottom: '0.75rem' }}>
            <legend style={{ color: '#aaa', fontSize: '0.85rem', padding: '0 0.3rem' }}>Model Weights</legend>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.5rem' }}>
              {[
                { label: 'Point Diff', val: pdWeight, set: setPdWeight },
                { label: 'ELO', val: eloWeight, set: setEloWeight },
                { label: 'Net Rating', val: ratingWeight, set: setRatingWeight },
                { label: 'Home Court', val: hcaWeight, set: setHcaWeight },
              ].map(({ label, val, set }) => (
                <div key={label}>
                  <label style={labelStyle}>{label}: {val.toFixed(2)}</label>
                  <input type="range" min="0" max="0.5" step="0.05" value={val} onChange={e => set(+e.target.value)}
                    style={{ width: '100%' }} />
                </div>
              ))}
            </div>
            <div style={{ textAlign: 'right', color: '#888', fontSize: '0.8rem', marginTop: '0.3rem' }}>
              Total: {(pdWeight + eloWeight + ratingWeight + hcaWeight).toFixed(2)}
            </div>
          </fieldset>
        </>
      )}

      <div style={{ display: 'flex', gap: '0.5rem', justifyContent: 'flex-end' }}>
        <button type="button" onClick={onCancel}
          style={{ padding: '0.4rem 1rem', background: '#333', border: '1px solid #555', borderRadius: '4px', color: '#ccc', cursor: 'pointer' }}>
          Cancel
        </button>
        <button type="submit" disabled={!name}
          style={{ padding: '0.4rem 1rem', background: name ? '#2563eb' : '#444', border: 'none', borderRadius: '4px', color: '#fff', cursor: name ? 'pointer' : 'not-allowed' }}>
          {editing ? 'Update' : 'Create'}
        </button>
      </div>
    </form>
  );
}
