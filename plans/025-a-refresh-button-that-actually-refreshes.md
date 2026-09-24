# Plan 025: A refresh button that actually refreshes

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md` — unless a reviewer dispatched you and told you they
> maintain the index.
>
> **Drift check (run first)**:
> `git diff --stat bb03f78..HEAD -- frontend/src/pages/TodaysPicks.tsx frontend/src/api/client.ts backend/api/pipeline_api.py`
> Expected: empty. On a mismatch, treat it as a STOP condition.

## Status

- **Priority**: P2
- **Effort**: S
- **Risk**: MED — the button spends real Odds API credits on every press. The code is small; the risk is an unguarded control that costs money.
- **Depends on**: none
- **Category**: dx
- **Planned at**: commit `bb03f78`, 2026-09-23
- **Executor model**: `sonnet` (Agent tool `model` value). Small diff, but it is React Query wiring plus tests written from prose, and the guard behaviour matters more than the markup. Mid-tier is the floor. Not `haiku`.

## Why this matters

The owner wants a control in the app to pull fresh data, and is preparing to
show the app to other people.

**The backend and the API client already exist; nothing in the UI calls
them.** `POST /pipeline/run` is implemented at
`backend/api/pipeline_api.py:23`, and `frontend/src/api/client.ts:132-139`
already exposes `api.pipeline.run(sport?)` with a fully typed response. A
repo-wide search finds no caller. This is the same dead-wiring shape recorded
in `plans/README.md` more than once: machinery that runs, returns, and is
wired to nothing.

So this plan is mostly connection, not construction. What it must get right
is the guarding, because **each press spends Odds API credits against a
monthly budget** (`odds_budget` in `config.yaml`: 20,000/month, 600/day
target). An un-disabled button that someone can hold down is a bill.

## Current state

- `backend/api/pipeline_api.py:23` — `POST /pipeline/run`, optional `?sport=`.
  Returns `{status, active_sports, games_stored, odds_stored, props_stored,
  props_analyzed, picks_generated, credits_used, credits_remaining_today,
  credits_remaining_month}`. Returns **429** with a JSON body on
  `BudgetExhaustedError` (line 118) and **500** on anything else (line 130).
  **Do not modify this file.**
- `frontend/src/api/client.ts:132-139` — `api.pipeline.run(sport?)` already
  typed to that response. **Do not modify this file.**
- `frontend/src/pages/TodaysPicks.tsx` — the index route (`App.tsx:31`). It
  already imports `CreditUsage` and renders `SummaryBar`, so the header area
  is where a refresh control belongs. It reads data through
  `useTodaysPicks(sport)`, `useTopProps(sport)`.
- `frontend/src/hooks/useToast.ts` — `const { toast } = useToast()`, the
  established way to surface a result. Used by `Admin.tsx:11`,
  `Backtesting.tsx:33`, `PaperTrading.tsx`.
- **Query keys to invalidate** (read `useTodaysPicks.ts`, `useProps.ts`,
  `useRecord.ts` yourself and match exactly what they declare — these are the
  ones observed):
  - `['games', 'today', <sport|undefined>]`
  - `['props', 'today', <sport|undefined>, <market|undefined>]`
  - `['record', <sport|undefined>]`
  Invalidating the **prefix** (`{queryKey: ['games']}`) is the safe form and
  is what `usePaperTrading.test.tsx:79` does.
- React Query is the repo convention for server state; `PaperTrading.tsx` was
  migrated to it specifically so no page hand-rolls `useState`/`useEffect`
  for server data. Follow that.

**Frontend toolchain** (from `plans/README.md` and the repo's memory):
`npm ci` **fails** on a pre-existing peer conflict — use
`npm ci --legacy-peer-deps`. Lint is not a build gate, so check it explicitly.

## Commands you will need

Run all of these from `frontend/`.

| Purpose | Command | Expected on success |
|---|---|---|
| Install | `npm ci --legacy-peer-deps` | exit 0 (several minutes in a fresh worktree) |
| Typecheck | `npx tsc -b --noEmit` | exit 0, no errors |
| Unit tests | `npx vitest run` | all pass (20+ baseline) |
| Lint | `npx eslint .` | **0 errors, 0 warnings** |

The Python suite is untouched by this plan, but run it once at the end
anyway: `<python> -m pytest backend/tests -q` → 1502 baseline.

## Scope

**In scope**:
- `frontend/src/pages/TodaysPicks.tsx`
- `frontend/src/hooks/useRefreshData.ts` (create)
- `frontend/src/hooks/useRefreshData.test.tsx` (create)

**Out of scope** — do not touch:
- `backend/` **entirely**. The endpoint works; this is a UI change.
- `frontend/src/api/client.ts` — the method already exists and is correct.
- Any other page, `Layout.tsx`, or `BottomNav.tsx`. One button, on the index
  route.
- Authentication. The endpoint is unauthenticated like every other route in
  this app; that is a known, separately-tracked finding and is **not** this
  plan's job. Do not add auth, and do not add a client-side gate pretending
  to be auth.

## Git workflow

- Conventional commit, e.g. `feat(ui): a refresh button wired to the pipeline`
- Do NOT push or open a PR.
- End commit messages with:
  `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`

## Steps

### Step 1: The mutation hook

Create `frontend/src/hooks/useRefreshData.ts`. A `useMutation` over
`api.pipeline.run`, with a docstring comment explaining that the endpoint
spends Odds API credits and that the button must stay disabled while it is in
flight.

Requirements:
- `mutationFn: (sport?: string) => api.pipeline.run(sport === 'all' ? undefined : sport)`
  — the page's sport store uses `'all'` as its no-filter value while the API
  expects the parameter omitted.
- `onSuccess`: invalidate `['games']`, `['props']` and `['record']` by prefix,
  then `toast` a summary naming `picks_generated` and `credits_used`.
- `onError`: distinguish the budget case. A 429 must read as budget
  exhausted, not as a generic failure — an operator who sees "something went
  wrong" will press it again, which is the worst response to a spent budget.
  Read how `post` surfaces a non-2xx in `client.ts` and branch on what is
  actually available; if the status code is not reachable, match on the
  message rather than inventing a new error type in the client.
- Return the mutation object so the page can read `isPending`.

**Verify**: `npx tsc -b --noEmit` → exit 0.

### Step 2: The button

In `TodaysPicks.tsx`, add a refresh control in the header area, beside the
existing `CreditUsage` render.

Requirements:
- Label "Refresh data" at rest; something explicit like "Refreshing…" while
  pending.
- `disabled={isPending}` — this is the money guard, not a nicety.
- Passes the current `sport` from the store.
- Styling consistent with the controls already on that page. Read the
  surrounding JSX and match it; do not introduce a new component library or a
  new colour.
- Accessible: a real `<button>` with a discernible name, not a clickable div.

**Verify**: `npx tsc -b --noEmit` → exit 0; `npx eslint .` → 0 errors, 0 warnings.

### Step 3: Tests

Create `frontend/src/hooks/useRefreshData.test.tsx`, modeled on
`frontend/src/hooks/usePaperTrading.test.tsx` (read it first — it shows the
QueryClient wrapper and the `invalidateQueries` assertion pattern).

Tests (names as given):

1. `test: calls the pipeline endpoint with the selected sport` — mock
   `api.pipeline.run`; trigger with `'mlb'`; assert it was called with
   `'mlb'`.
2. `test: sends no sport when the filter is all` — trigger with `'all'`;
   assert it was called with `undefined`. (The API treats an omitted sport as
   every in-season sport; passing the literal string `'all'` would ask for a
   sport that does not exist.)
3. `test: invalidates the picks, props and record queries on success` — spy on
   `queryClient.invalidateQueries`; assert all three prefixes were
   invalidated. **This is the test that stops it becoming another dead
   wire** — say so in the test's comment.
4. `test: a budget-exhausted response reads as budget exhausted` — make the
   mocked call reject with the 429 shape; assert the toast message mentions
   the budget rather than a generic failure.
5. `test: does not invalidate anything when the call fails` — reject;
   assert `invalidateQueries` was not called.

**Mutation check, required** (repo rule: a test that still passes when the
implementation is broken is not a test): delete the `invalidateQueries` calls
from `onSuccess`; test 3 must fail. Restore and report.

**Verify**: `npx vitest run` → all pass, five more than the baseline.

### Step 4: See it work

Start the backend and the frontend and press the button once. This spends a
small number of real credits, which is expected and is the point of the
check.

- Backend: `<python> -m uvicorn backend.api.main:app --port 8000` from the
  repo root, pointed at the **worktree's own** database — copy one with
  `sqlite3 .backup` or let it create an empty file. **Do not point it at the
  main checkout's `sports_picks.db`.**
- Frontend: `npm run dev` from `frontend/`.

Confirm and report: the button disables while running, a toast appears naming
picks generated and credits used, and the picks list re-renders without a
manual page reload.

If the pipeline errors for an environmental reason (no API key in the
worktree, network blocked), say so plainly and report what you did see —
the disable-and-toast behaviour is still observable on the error path.

**Verify**: state in your report what you observed, including whether the list updated without a reload.

### Step 5: Full checks

**Verify**: from `frontend/`: `npx tsc -b --noEmit` exit 0, `npx vitest run` all pass, `npx eslint .` 0 errors and 0 warnings. From the repo root: `<python> -m pytest backend/tests -q` → 1502 passed.

## Done criteria

- [ ] `npx tsc -b --noEmit` exits 0
- [ ] `npx eslint .` reports 0 errors and 0 warnings
- [ ] `npx vitest run` passes with five new tests
- [ ] `<python> -m pytest backend/tests -q` → 1502 passed
- [ ] `git diff --stat bb03f78..HEAD -- backend/ frontend/src/api/client.ts` is empty
- [ ] `grep -rn "pipeline.run" frontend/src` shows the client method and the new hook, and nothing else
- [ ] The button is `disabled` while the mutation is pending (assert by reading the JSX in your report)
- [ ] Mutation check performed, failing test named
- [ ] Step 4 observations reported
- [ ] No files outside the in-scope list are modified (`git status`)

## STOP conditions

Stop and report back (do not improvise) if:

- `api.pipeline.run` is absent from `client.ts` or its signature differs.
- `npm ci --legacy-peer-deps` fails — report the error rather than trying
  other install flags.
- `npx eslint .` was already red before your change (check first, on a clean
  tree) — that is a pre-existing condition and you should report it rather
  than fix it.
- The query keys in the hooks do not match the three listed in "Current
  state".
- You find yourself editing anything under `backend/`.
- You are tempted to add authentication, a password prompt, or a
  confirmation dialog that pretends to be a security control.

## Maintenance notes

- **This button is unauthenticated, like the whole API.** That is fine while
  the app runs on localhost and is a real problem the moment it is deployed,
  because this specific endpoint spends money. Before any deploy, either put
  auth in front of the API or remove this control from the shipped build. The
  owner has been told; it is tracked in `plans/README.md` under the security
  findings that were never planned.
- A per-press credit cost is visible in the toast. If the button is ever used
  often, consider showing the remaining monthly budget beside it — the
  `CreditUsage` component on the same page already fetches it.
- If a second refresh control is ever added elsewhere, both should share
  `useRefreshData` rather than duplicating the invalidation list, which is
  the part that silently rots.
