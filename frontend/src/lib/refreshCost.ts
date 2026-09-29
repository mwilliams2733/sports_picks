// Remembers what the last pipeline run actually cost in Odds API credits,
// so SpendConfirmButton can show a real number before the next spend.
// Storage can throw (private mode, blocked site data), so every access is
// guarded — same style as lib/secrets.ts.
export const LAST_REFRESH_KEY = 'sp.lastRefreshCredits'

export function rememberRefreshCost(credits: number): void {
  try { localStorage.setItem(LAST_REFRESH_KEY, String(credits)) } catch { /* storage unavailable */ }
}

// A negative, non-numeric, or otherwise non-finite stored value (junk from
// a stale format, manual tampering, or a partial write) must read as
// "unknown", never as NaN or a nonsense number rendered into the UI.
export function lastRefreshCost(): number | null {
  try {
    const v = localStorage.getItem(LAST_REFRESH_KEY)
    if (v == null) return null
    const n = Number(v)
    return Number.isFinite(n) && n >= 0 ? n : null
  } catch { return null }
}
