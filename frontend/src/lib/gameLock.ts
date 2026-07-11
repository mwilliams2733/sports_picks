export type GameLockState = 'open' | 'locked' | 'final';

// Backend only tracks 'scheduled' and 'final' game status — there's no
// live/in-progress state. A 'scheduled' game whose start_time has already
// passed is treated as locked (started but not yet graded).
export function getGameLockState(status: string, startTime: string | null): GameLockState {
  if (status === 'final') return 'final';
  if (startTime && new Date(startTime).getTime() <= Date.now()) return 'locked';
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
