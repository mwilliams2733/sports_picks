# Plan 005: Keep the Odds API key out of logs and responses, and restore the budget alarm

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md`.
>
> **Drift check (run first)**:
> `git diff --stat 5c2e0d0..HEAD -- backend/collectors/ backend/pipeline/full_pipeline.py backend/api/pipeline_api.py`
> If any in-scope file changed since this plan was written, compare the
> "Current state" excerpts against the live code before proceeding; on a
> mismatch, treat it as a STOP condition.

## Status

- **Priority**: P1
- **Effort**: S
- **Risk**: LOW
- **Depends on**: none
- **Category**: security
- **Planned at**: commit `5c2e0d0`, 2026-09-16

## ⚠️ Operator action required before or alongside this plan

**Rotate the Odds API key.** A live key is currently sitting in plaintext in a
local log file at `uvicorn.log:78`, written there by an exception handler that
formatted a failed request URL. The file is correctly git-ignored
(`.gitignore:13`) and was **never committed** (`git ls-files` confirms it is
untracked, and `git log --all -- .env` is empty) — so this is not a repository
leak. But the key has existed in cleartext on disk and, in the fly.io
deployment, the same code path writes it into Fly's log stream.

Rotate at the-odds-api.com, update the secret via
`flyctl secrets set ODDS_API_KEY=...` (per the note at `fly.toml:19-20`), and
update the local `.secrets`/`.env` entry. **Do not paste the old or new key
into any file, commit message, plan, or chat.**

Deleting `uvicorn.log` does not un-burn the key; rotation is the fix. This plan
stops the *recurrence*.

## Why this matters

Two related defects in the same error-handling paths:

1. **The API key is logged and returned to clients.** The Odds API accepts its
   key only as an `apiKey` **query parameter**, so httpx embeds it in the
   request URL — and `HTTPStatusError`/`HTTPError` string representations
   include the full URL. Two handlers format the raw exception into the log with
   `f"...: {e}"`, and one API handler returns `str(e)` straight to the HTTP
   client in a 500 body. Combined, an upstream 429 or 5xx can surface the key
   to an unauthenticated caller.

2. **The budget alarm never fires.** `BudgetExhaustedError` is raised *inside*
   a `try` block whose `except Exception` catches it and downgrades it to a
   warning. The `except BudgetExhaustedError` branch in the API that returns
   HTTP 429 with a credit summary is therefore **dead code** — callers always
   get `{"status": "completed"}`.

   To be precise about impact: the guard **does** still prevent spending (the
   raise happens before `fetch_odds`, and each subsequent sport re-checks and
   re-raises). What is lost is the *signal*. A run that hits the monthly ceiling
   reports success, the frontend credit UI shows a normal response, and the
   operator loses the one alarm that says spend is out of control.

## Current state

### How the key travels

`backend/collectors/odds_api.py:27-32` — the key is a query parameter (the
provider offers no header alternative, so it must stay a parameter):

```python
        params = {
            "apiKey": self.api_key,
            "regions": "us",
            "markets": markets,
            "oddsFormat": "american",
        }
        response = await self.client.get(url, params=params)
        response.raise_for_status()
```

The same shape appears at `odds_api.py:83-84` (`fetch_events`) and
`odds_api.py:99-105` (`fetch_player_props`).

### Where it leaks

`backend/pipeline/full_pipeline.py:60-61`:

```python
            except Exception as e:
                logger.warning(f"Odds API fetch failed for {sport}: {e}")
```

`backend/pipeline/full_pipeline.py:115-116`:

```python
            except Exception as e:
                logger.warning(f"Props fetch failed for {sport}: {e}")
```

`backend/api/pipeline_api.py:121-126` — returns the exception text to the client:

```python
    except Exception as e:
        logger.error(f"Pipeline error: {e}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"status": "error", "message": str(e)},
        )
```

Evidence of a real leak: `uvicorn.log:78` contains a line beginning
`Props fetch failed for ncaab: Client error '429 Too Many Requests' for url
'https://api.the-odds-api.com/v4/sports/ba...` — with the key in the query
string. Credential type: **The Odds API key**. (Do not open that file to read
the value; it is not needed for this work.)

### Where the budget alarm is swallowed

`backend/pipeline/full_pipeline.py:44-61`:

```python
        for sport in sports:
            try:
                if budget:
                    status = check_budget(session, budget)
                    if status == BudgetStatus.MONTHLY_EXHAUSTED:
                        from backend.collectors.budget import get_credit_summary
                        summary = get_credit_summary(session, budget)
                        raise BudgetExhaustedError(summary["monthly_used"], budget["monthly_limit"], summary["daily_used"])
                odds_data = await collector.fetch_odds(sport)
                record_api_call(session, "odds", sport, collector.requests_remaining)
                for event in odds_data:
                    _ensure_game_from_odds(session, sport, event)
                stored = _store_odds(session, sport, odds_data)
                total += stored
                logger.info(f"Stored odds for {stored} {sport} events (remaining: {collector.requests_remaining})")
            except Exception as e:
                logger.warning(f"Odds API fetch failed for {sport}: {e}")
```

`BudgetExhaustedError` subclasses `Exception` (`backend/exceptions.py:1`), so
line 60 catches the error raised on line 51. The identical pattern is at
`full_pipeline.py:83-116` for props (raise at :89, swallowed at :115).

The dead handler, `backend/api/pipeline_api.py:115-120`:

```python
    except BudgetExhaustedError as e:
        summary = get_credit_summary(session, budget)
        return JSONResponse(
            status_code=429,
            content={"status": "budget_exhausted", "message": str(e), **summary},
        )
```

### Budget statuses available

`backend/collectors/budget.py:13-17`:

```python
class BudgetStatus(enum.Enum):
    OK = "ok"
    DAILY_SOFT_LIMIT = "daily_soft_limit"
    RESERVE_EXHAUSTED = "reserve_exhausted"
    MONTHLY_EXHAUSTED = "monthly_exhausted"
```

Note the asymmetry: the props path already honors `RESERVE_EXHAUSTED`
(`full_pipeline.py:103-107`, which `break`s out of the per-event loop), but the
odds path at :48 checks only `MONTHLY_EXHAUSTED`. `DAILY_SOFT_LIMIT` is
computed but acted on nowhere in `fetch_and_store_odds`.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Full suite | `.venv/Scripts/python.exe -m pytest backend/tests -q` | 0 failed |
| Budget tests | `.venv/Scripts/python.exe -m pytest backend/tests/test_budget.py backend/tests/test_pipeline_api.py -q` | all pass |
| Leak grep | see done criteria | no matches |

Run from repo root.

## Scope

**In scope**:
- `backend/collectors/odds_api.py` (add a redaction helper here, or in a small
  new module — your choice, but keep it next to the code that owns the key)
- `backend/pipeline/full_pipeline.py`
- `backend/api/pipeline_api.py`
- New/updated tests under `backend/tests/`

**Out of scope** (do NOT touch, even though they look related):
- `.env`, `.env.example`, `.gitignore`, `fly.toml` — the credential
  *configuration* posture is already correct: `.env` is ignored and was never
  committed, `.dockerignore:20-22` excludes it from the image, and `fly.toml`
  correctly documents `flyctl secrets set`. Nothing to fix there.
- `uvicorn.log` — do **not** delete, rewrite, or commit it. It is untracked and
  ignored. Rotation is the remedy; the operator decides what to do with the
  file.
- Adding authentication to `POST /pipeline/run` — separate, larger security
  finding. This plan does not gate the endpoint.
- The rest of `pipeline_api.py`'s response shape — the frontend
  (`frontend/src/api/client.ts:141-147`) depends on it.

## Git workflow

- Branch: `advisor/005-credential-scrub-and-budget-alarm`
- Conventional commits:
  - `fix(security): redact the Odds API key before logging request errors`
  - `fix(api): stop returning raw exception text to clients`
  - `fix(budget): let BudgetExhaustedError reach the 429 handler`

## Steps

### Step 1: Baseline

**Verify**: `.venv/Scripts/python.exe -m pytest backend/tests -q` → 0 failed.
Record the count.

### Step 2: Add a redaction helper

Add a module-level function in `backend/collectors/odds_api.py`:

```python
_APIKEY_RE = re.compile(r"(apiKey=)[^&\s'\"]+", re.IGNORECASE)


def redact_api_key(text: str) -> str:
    """Replace any apiKey query-parameter value with a placeholder.

    The Odds API only accepts its key as a query parameter, so the key ends up
    inside httpx exception strings (which embed the request URL). Any text
    derived from such an exception must pass through here before being logged
    or returned.
    """
    return _APIKEY_RE.sub(r"\1<redacted>", text)
```

Do not attempt to match the key's literal value — matching the *parameter* is
robust across rotations and doesn't require the secret to appear in code.

**Verify** — add `backend/tests/test_odds_api_redaction.py` asserting:
- a string containing `apiKey=abc123&regions=us` becomes
  `apiKey=<redacted>&regions=us`
- the substring `abc123` is **not** present in the output
- case-insensitive (`apikey=`) is also redacted
- a string with no `apiKey` is returned unchanged

`.venv/Scripts/python.exe -m pytest backend/tests/test_odds_api_redaction.py -q`
→ all pass.

### Step 3: Use it at the two logging sites

In `backend/pipeline/full_pipeline.py`, change both handlers so they neither
interpolate the raw exception nor lose useful diagnostics. Log the exception
**type** plus a redacted message:

```python
            except Exception as e:
                logger.warning(
                    "Odds API fetch failed for %s: %s: %s",
                    sport, type(e).__name__, redact_api_key(str(e)),
                )
```

Apply the same at the props handler (`:115-116`).

Note the repo mixes f-string and `%s` logging; prefer `%s` here so the
formatting is deferred and the redaction is explicit.

**Verify** — add a test that uses `caplog` to capture log records while forcing
a simulated httpx error whose message contains an `apiKey=` URL, and assert:
- the fake key value does **not** appear in `caplog.text`
- `"<redacted>"` does appear
- the sport name and exception type still appear (diagnostics preserved)

### Step 4: Stop returning exception text to clients

In `backend/api/pipeline_api.py:121-126`, keep the full detail server-side
(`logger.error(..., exc_info=True)` is fine — but pass the message through
`redact_api_key`) and return a **static** message to the client plus a
correlation id so the two can be matched:

```python
    except Exception as e:
        error_id = uuid.uuid4().hex[:12]
        logger.error(
            "Pipeline error [%s]: %s: %s",
            error_id, type(e).__name__, redact_api_key(str(e)),
            exc_info=True,
        )
        return JSONResponse(
            status_code=500,
            content={"status": "error", "message": "Pipeline run failed", "error_id": error_id},
        )
```

**Note**: `exc_info=True` writes the traceback, which can itself contain the
URL. Check whether your logging config renders the traceback to the same sink;
if it does, either drop `exc_info=True` here or add a logging filter. Prefer
dropping it at this site — the type name plus the redacted message is enough,
and the traceback is the part that leaks.

**Verify**: add a test that patches the pipeline call to raise an exception
whose message embeds an `apiKey=` URL, calls `POST /pipeline/run`, and asserts
the response body contains neither the fake key nor the string `apiKey`, and
does contain an `error_id`.

### Step 5: Let the budget error escape to the 429 handler

In `backend/pipeline/full_pipeline.py`, add an explicit re-raise immediately
before **each** broad handler, so the budget error passes through while genuine
fetch failures are still tolerated per-sport:

```python
            except BudgetExhaustedError:
                raise
            except Exception as e:
                logger.warning(...)
```

Apply at both `:60` (odds) and `:115` (props). `BudgetExhaustedError` is
already imported in that module.

While here, make the odds path honor `RESERVE_EXHAUSTED` the way the props path
already does at `:103-107` — treat it as a stop condition for further odds
fetching rather than ignoring it. Do **not** change `DAILY_SOFT_LIMIT`
behavior; making the daily target hard-blocking is a policy change, not a bug
fix, and belongs to the operator.

**Verify** — this is the load-bearing test. Add a test that:
1. stubs `check_budget` to return `BudgetStatus.MONTHLY_EXHAUSTED`
2. calls `POST /pipeline/run`
3. asserts `response.status_code == 429` and
   `response.json()["status"] == "budget_exhausted"`

Before this change the same test yields `200 {"status": "completed"}`.

**Prove the guard is real**: remove the `except BudgetExhaustedError: raise`
lines, confirm the test fails with 200, then restore them.

### Step 6: Full suite and leak sweep

**Verify**:
- `.venv/Scripts/python.exe -m pytest backend/tests -q` → 0 failed
- `grep -rn 'failed for {sport}: {e}' backend/` → no matches
- `grep -rn '"message": str(e)' backend/api/` → no matches
- `git diff --name-only` → only in-scope files; in particular **no**
  `.env*`, `uvicorn.log`, `fly.toml`, or `.gitignore`

## Test plan

New tests:
1. `backend/tests/test_odds_api_redaction.py` — four cases for
   `redact_api_key` (step 2)
2. Log-capture test: the key never reaches a log record (step 3)
3. API test: 500 responses contain no key and no `apiKey` substring, and do
   carry an `error_id` (step 4)
4. **`test_pipeline_run_returns_429_when_budget_exhausted`** (step 5) — the
   load-bearing test; must be shown to fail without the re-raise

Use synthetic fake key values in tests (e.g. `"FAKEKEY123"`). **Never** put a
real key in a test fixture.

Pattern to follow: `backend/tests/test_api_picks.py` for API tests;
pytest's built-in `caplog` fixture for the log assertions.

## Done criteria

ALL must hold:

- [ ] `.venv/Scripts/python.exe -m pytest backend/tests -q` exits 0, 0 failed
- [ ] `grep -rn 'failed for {sport}: {e}' backend/` returns no matches
- [ ] `grep -rn '"message": str(e)' backend/api/` returns **at most one** match:
      the `except BudgetExhaustedError` handler in `pipeline_api.py`. That one
      is safe and must be LEFT ALONE — `BudgetExhaustedError.__str__` is
      `"Budget exhausted: X/Y monthly, Z today"`, which carries no credential,
      and the frontend depends on that response shape. The criterion is that no
      **credential-bearing** `str(e)` reaches a client.
- [ ] `redact_api_key` exists and is applied at all three sites
      (`full_pipeline.py` ×2, `pipeline_api.py` ×1)
- [ ] The 429 test passes, and was demonstrated to fail without the re-raise
- [ ] No real credential value appears anywhere in the diff
      (`git diff | grep -i apikey` shows only the regex, the placeholder, and
      test fakes)
- [ ] `git diff --name-only` contains no `.env*`, `uvicorn.log`, `.gitignore`,
      or `fly.toml`
- [ ] Operator has been reminded to rotate the key (state this in your report)
- [ ] `plans/README.md` status row for 005 updated

## STOP conditions

Stop and report back (do not improvise) if:

- The baseline suite doesn't pass cleanly.
- You find a **second** credential type in the logs or code beyond the Odds API
  key. Report its `file:line` and type — **never** its value — and stop.
- Adding `except BudgetExhaustedError: raise` causes an existing pipeline test
  to fail. That likely means a test depended on the swallowed behavior; report
  which test and what it asserted rather than re-raising conditionally.
- `redact_api_key` cannot be imported into `pipeline_api.py` without a circular
  import. Report the cycle; do not duplicate the regex into a second module
  (that is exactly the duplication this repo's conventions forbid).
- You are tempted to delete or rewrite `uvicorn.log`. Don't — report instead.

## Maintenance notes

- **Any new outbound API client that takes a key as a query parameter needs the
  same treatment.** The rule to enforce in review: an exception derived from an
  httpx request must never be interpolated into a log or a response without
  passing through `redact_api_key`.
- `redact_api_key` matches the *parameter name*, not a specific secret, so it
  keeps working after rotation and across keys. If a provider is ever added
  that uses a different parameter name (`api_key`, `token`, `key`), extend the
  regex rather than adding a second function.
- The 429 path is now reachable for the first time. Its response body
  (`{"status": "budget_exhausted", **summary}`) has never been exercised by the
  frontend; check that `frontend/src/api/client.ts`'s error handling renders it
  sensibly the first time it fires.
- Deliberately deferred: `POST /pipeline/run` is still unauthenticated, so an
  anonymous caller can still drive API spend up to the budget ceiling. Gating
  it is the separate auth work, and it is the larger of the two problems.
