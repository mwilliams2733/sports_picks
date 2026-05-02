import { useCallback, useState } from 'react';

const STORAGE_KEY = 'sports_picks.hidden_games';

function loadFromStorage(): Set<number> {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return new Set();
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return new Set();
    return new Set(parsed.filter((n): n is number => typeof n === 'number'));
  } catch {
    return new Set();
  }
}

function saveToStorage(ids: Set<number>) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(Array.from(ids)));
  } catch {
    // localStorage full or unavailable; non-fatal
  }
}

/**
 * Per-device hidden-games list. The user can dismiss any Today's Picks
 * card they don't trust (e.g. a stale playoff matchup the bookmakers
 * are still pricing). Persists to localStorage so dismissals survive
 * page reloads.
 */
export function useHiddenGames() {
  const [hidden, setHidden] = useState<Set<number>>(loadFromStorage);

  const hide = useCallback((id: number) => {
    setHidden(prev => {
      if (prev.has(id)) return prev;
      const next = new Set(prev);
      next.add(id);
      saveToStorage(next);
      return next;
    });
  }, []);

  const showAll = useCallback(() => {
    saveToStorage(new Set());
    setHidden(new Set());
  }, []);

  return { hidden, hide, showAll };
}
