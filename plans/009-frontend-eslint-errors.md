# Plan 009: Clear the frontend ESLint errors

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md`.
>
> **Drift check (run first)**:
> `cd frontend && npx eslint .`
> Expect **6 errors, 2 warnings** at the exact sites listed below. If the
> counts or locations differ, treat it as a STOP condition.

## Status

- **Priority**: P2
- **Effort**: M
- **Risk**: LOW-MED — these are behavior-bearing UI changes, not formatting
- **Depends on**: none
- **Category**: bug / tech-debt
- **Planned at**: commit `db9951b`, 2026-09-16

## Why this matters

`npx eslint .` has been red for the whole audit: **6 errors, 2 warnings**. Four
of the errors are `react-hooks/set-state-in-effect`, which React's own linter
introduced because calling `setState` synchronously inside an effect causes a
second render pass on every change — and, more importantly, usually signals
state that should have been derived rather than stored.

A red lint gate is also a blocked path: the repo has no CI yet (a separate open
finding), and adding one is much easier when `npm run lint` already passes.

**Note that `npm run build` only runs `tsc -b` — lint is not currently a build
gate**, which is why this has stayed red without breaking anything.

## Current state

Verified at `db9951b`:

```
src/components/PerformanceChart.tsx
  9:5   error  Cannot reassign variable after render completes
src/components/Toast.tsx
  16:17 error  react-refresh/only-export-components
src/pages/PaperTrading.tsx
  70:21 error  Calling setState synchronously within an effect
src/pages/PlayerProps.tsx
  34:18 error  Calling setState synchronously within an effect
  35:6  warning react-hooks/exhaustive-deps
  80:21 error  Calling setState synchronously within an effect
src/pages/TodaysPicks.tsx
  26:6  warning react-hooks/exhaustive-deps
  74:21 error  Calling setState synchronously within an effect
```

### The four sites, with their actual code

**A. `PerformanceChart.tsx:7-11`** — accumulator reassigned during render:

```tsx
export default function PerformanceChart({ data }: Props) {
  let cumulative = 0;
  const chartData = data.map(d => {
    cumulative += d.profit;
    return { ...d, cumulative: Math.round(cumulative * 100) / 100 };
  });
```

**B. `Toast.tsx:16`** — a hook exported from a file that also exports components:

```tsx
export function useToast() { ... }
export function ToastProvider({ children }: { children: ReactNode }) { ... }
```

`useToast` is imported by four files: `BetModal.tsx`, `Admin.tsx`,
`Backtesting.tsx`, `PaperTrading.tsx` (plus `Toast.tsx` itself).

**C. `TodaysPicks.tsx:74` and `PlayerProps.tsx:80`** — reset a filter when the
sport changes:

```tsx
// TodaysPicks.tsx
useEffect(() => { setTeamFilter(''); }, [sport]);

// PlayerProps.tsx
useEffect(() => { setTeamFilter(''); setMarketFilter(''); }, [sport]);
```

**D. `PlayerProps.tsx:30-35` and `TodaysPicks.tsx:22-26`** — sync URL → store on
mount. These also carry the two `exhaustive-deps` warnings:

```tsx
useEffect(() => {
  const urlSport = searchParams.get('sport');
  const urlConf = searchParams.get('confidence');
  if (urlSport && urlSport !== sport) setSport(urlSport);
  if (urlConf) setMinConfidence(Number(urlConf));
}, []);
```

### E. The one that is not a lint fix

**`PaperTrading.tsx:70`**:

```tsx
useEffect(() => { loadGames(); loadProps(); loadFeed(); }, []);
```

Each of those is an `async` function that calls `api.*` and then `setState`.
This is not a stray effect — it is **hand-rolled server-state fetching on the
app's most complex page**, and it is the only page in the codebase that does
not use React Query (`useTodaysPicks.ts`, `useProps.ts`, `useRecord.ts`,
`useTopProps.ts`, `useLeaderboard.ts` all do).

Fixing the lint error *properly* means migrating those four `useState` +
`useEffect` pairs to `useQuery` and replacing the manual refetch-by-hand calls
with `invalidateQueries` — a separate, M-sized refactor of the parlay builder
and prop search. **That is out of scope here** and gets its own plan.

## Commands you will need

Run from `frontend/`.

| Purpose | Command | Expected |
|---|---|---|
| Lint | `npx eslint .` | 6 errors → **1 error, 0 warnings** after this plan |
| Typecheck | `npx tsc -b --noEmit` | clean, no output |
| Tests | `npx vitest run` | **14 passed** before, ≥14 after |
| Build | `npm run build` | exits 0 |

The backend suite is unaffected; do not run it.

## Scope

**In scope:**
- `src/components/PerformanceChart.tsx`
- `src/components/Toast.tsx` + a new `src/hooks/useToast.ts`
- the four files importing `useToast`
- `src/pages/TodaysPicks.tsx`
- `src/pages/PlayerProps.tsx`
- `src/pages/PaperTrading.tsx` — **only** to add a documented suppression (see
  Step 5). No behavioral change there.
- frontend tests

**Out of scope — do not touch:**
- Any migration of `PaperTrading.tsx` to React Query. That is the separate plan.
- `backend/` — nothing here touches Python.
- Any other lint rule, formatting pass, or dependency bump. Do not run
  `eslint --fix` across the repo; fix exactly these sites.
- `eslint.config.js` — do not weaken or disable a rule globally to make the
  count go down. A global disable would "fix" the number and lose the signal.

## Git workflow

- Branch: `advisor/009-frontend-eslint`
- Conventional commits, one per fix:
  - `fix(frontend): accumulate chart totals with reduce instead of reassigning`
  - `refactor(frontend): move useToast into its own module for fast refresh`
  - `fix(frontend): reset filters during render instead of in an effect`
  - `fix(frontend): read URL params in a lazy initializer`

## Steps

### Step 1: Baseline

Run all four commands above and record the output. Expect 6 errors / 2 warnings,
tsc clean, 14 tests passing. If lint shows different sites, STOP.

### Step 2: PerformanceChart — reduce instead of reassign

Replace the `let cumulative` accumulator with a `reduce` that carries the
running total in the accumulator, so nothing outside the callback is reassigned
during render.

Preserve the existing rounding exactly: `Math.round(cumulative * 100) / 100`
applied to the running total, not to each `profit`. Rounding each step
separately changes the numbers.

**Verify**: `npx eslint src/components/PerformanceChart.tsx` → clean.
**Also verify the output is unchanged**: add a small test asserting that
`[{profit: 10.005}, {profit: 10.005}]` produces the same `cumulative` sequence
as the current implementation. Compute the expected values by hand from the
*current* code before you change it, and put both in your report.

### Step 3: Toast — move the hook out

Create `src/hooks/useToast.ts` exporting `useToast`. Keep the context object
wherever it needs to live so both files can reach it without a circular import —
if the context must move too, move it to the hook file and have `Toast.tsx`
import it.

Update all four importers. Do not change any call site's behavior.

**Verify**: `npx eslint src/components/Toast.tsx` → clean;
`npx tsc -b --noEmit` → clean; `npx vitest run` → still 14 passing.
`grep -rn "useToast" src/` → every import resolves to the new module.

### Step 4: The filter resets and the URL sync

Two different patterns; do not conflate them.

**C — reset-on-change (`TodaysPicks.tsx:74`, `PlayerProps.tsx:80`).** Use
React's documented "adjusting state when a prop changes" pattern: track the
previous value and reset *during render*, which the rule permits:

```tsx
const [prevSport, setPrevSport] = useState(sport);
if (sport !== prevSport) {
  setPrevSport(sport);
  setTeamFilter('');
}
```

This must go **before** any early return, as the current effect's comment notes.

**D — URL → store on mount (`PlayerProps.tsx:34`, and the two
`exhaustive-deps` warnings).** For purely local state (`minConfidence`), move
the initial read into a `useState` lazy initializer:
`useState(() => Number(searchParams.get('confidence')) || DEFAULT)`.

`setSport` writes to the Zustand store, which is external state — a lazy
initializer cannot do that. If you cannot remove that effect without changing
behavior, **leave it and suppress it narrowly** with a one-line
`// eslint-disable-next-line react-hooks/set-state-in-effect` plus a comment
explaining that it syncs an external store from the URL on mount. Say in your
report which sites you genuinely fixed and which you suppressed, and why.

**Do not** silence the `exhaustive-deps` warnings by adding the missing deps if
that would make the effect re-run on every sport change — that would reintroduce
a URL→store→URL loop. If adding deps changes behavior, suppress with a reason
instead.

**Verify**: `npx vitest run` → still passing; manually confirm in your report
that changing the sport still clears the team filter (reason through the code;
you do not need a browser).

### Step 5: PaperTrading — suppress with a pointer, do not refactor

Add a narrowly scoped disable at `PaperTrading.tsx:70` with a comment naming
why: this is hand-rolled server state that belongs in React Query, tracked as a
separate refactor, and fixing it here would mean rewriting the page.

A suppression with a documented reason is honest. Migrating the most complex
page in the app as a side effect of a lint sweep is not.

**Verify**: `npx eslint .` → **0 errors**. If you cannot reach 0 without
touching something out of scope, report the residual instead of forcing it.

### Step 6: Full gates

**Verify all four**:
- `npx eslint .` → 0 errors, 0 warnings (or the residual you reported)
- `npx tsc -b --noEmit` → clean
- `npx vitest run` → ≥14 passing, 0 failed
- `npm run build` → exits 0

Then `git diff --name-only` → only in-scope files; nothing under `backend/`.

## Test plan

- One new test for `PerformanceChart`'s cumulative sequence (Step 2), with the
  expected values computed from the *pre-change* implementation.
- Existing 14 tests must keep passing — `BetModal.test.tsx` and
  `PicksTable.test.tsx` both touch components in the `useToast` import graph, so
  they are the real regression signal for Step 3.

## Done criteria

- [ ] `npx eslint .` → 0 errors (or a reported, justified residual)
- [ ] `npx tsc -b --noEmit` clean
- [ ] `npx vitest run` ≥14 passing, 0 failed
- [ ] `npm run build` exits 0
- [ ] `eslint.config.js` unchanged — `git diff --stat` proves it
- [ ] No file under `backend/` modified
- [ ] Every suppression has a one-line reason naming what would fix it properly
- [ ] `plans/README.md` status row updated

## STOP conditions

Stop and report (do not improvise) if:

- The baseline lint output differs from the sites listed above.
- A fix changes rendered output or user-visible behavior in any way you cannot
  fully explain. These are UI files with only 14 tests covering them — the suite
  will not catch a visual regression, so caution beats cleverness.
- You find yourself editing `eslint.config.js`, adding a file-level
  `/* eslint-disable */`, or running `eslint --fix` broadly. All three make the
  number go down without fixing anything.
- Removing an effect appears to require migrating a page to React Query. That is
  Step 5's situation and the answer is a documented suppression, not a refactor.
- `npx vitest run` drops below 14 passing at any point.

## Maintenance notes

- After this lands, `npm run lint` is a viable CI gate — worth wiring in when CI
  is added, so this cannot silently go red again.
- The `PaperTrading.tsx` suppression is a marker, not a resolution. Whoever does
  the React Query migration should delete it as part of that work; if the
  suppression outlives the refactor, the comment is lying.
- `react-hooks/set-state-in-effect` is new-ish and will flag more sites as the
  app grows. The reset-during-render pattern in Step 4 is the idiomatic answer
  and is worth knowing before writing the next filter.
