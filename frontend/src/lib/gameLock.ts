import { startInstant } from './slip'

export type GameLockState = 'open' | 'locked' | 'final';

// 'in_progress' comes from the live_scores job; a 'scheduled' game whose
// start time has passed is locked too (started, not yet updated). Start times
// from /games/today are naive UTC, so they go through startInstant.
export function getGameLockState(status: string, startTime: string | null): GameLockState {
  if (status === 'final') return 'final';
  if (status === 'in_progress') return 'locked';
  if (startTime && startInstant(startTime) <= Date.now()) return 'locked';
  return 'open';
}

export function lockStateLabel(state: GameLockState): string {
  if (state === 'final') return 'Final';
  if (state === 'locked') return 'Locked';
  return 'Bet This';
}

export function lockStateTooltip(state: GameLockState): string | undefined {
  if (state === 'final') return 'This game has ended — betting closed.';
  if (state === 'locked') return 'This game has started — betting locked.';
  return undefined;
}
