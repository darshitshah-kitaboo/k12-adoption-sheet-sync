# K12 Adoption Intelligence Pipeline

Nightly refresh of US K12 instructional materials adoption cycles, pushed to a Google Sheet that feeds the Kitaboo Adoption Intelligence dashboard.

## What this does

Every night, a GitHub Actions workflow reads `adoption_data.json`, validates it, and writes the rows to 7 tabs in a Google Sheet. The dashboard reads from the sheet, so the front-end countdown widgets (`14d`, `28d`, etc.) stay accurate as deadlines approach.

## Current state

As of April 2026, the pipeline is push-only. `adoption_data.json` is the source of truth and is updated by hand. The GitHub Actions workflow pushes whatever is in the file to the sheet.

Discovery and refresh layers are being built in phases. See `docs/PIPELINE.md` once it exists.

## Repository layout

```
adoption_data.json              Source of truth for all 51 states + DC
scripts/
  validate.py                   Schema + format + reference checks, runs before every push
  refresh_and_push.py           Writes rows to the Google Sheet
.github/workflows/
  nightly-push.yml              Cron 20:30 UTC + push-to-main trigger
GITHUB_SETUP.md                 One-time repo and secrets setup
.gitignore                      Excludes service account JSON and config
```

## How the nightly push works

1. Cron fires at 20:30 UTC (2 am IST the next morning).
2. The workflow checks out the repo, installs dependencies, runs `validate.py`.
3. If validation passes, `refresh_and_push.py` recomputes deadline fields against today's date, then clears and rewrites Summary, States, Cycles, Timeline, Sources, Tips, Enrollment.
4. The sheet is live within about 30 seconds.

If validation fails, the push does not run. Bad data never reaches the front-end.

## Secrets

Two GitHub repository secrets drive the push:

- `ADOPTION_SHEET_ID` is the Google Sheet ID.
- `ADOPTION_SA_JSON` is the service account JSON key, pasted whole.

Setup steps live in `GITHUB_SETUP.md`.

## What's coming next

The pipeline is being extended with a discovery and refresh layer so `adoption_data.json` updates automatically from public sources instead of by hand. Planned phases:

- Phase 1. State source registry (DOE pages, state board meetings, procurement portals)
- Phase 2. Coded adapters for the 19 active adoption states
- Phase 3. LLM extraction for unstructured sources
- Phase 4. Corroboration and merge layer with a High/Medium/Low confidence gate
- Phase 5. A second GitHub Actions workflow that refreshes the JSON before the push runs
- Phase 6. Email alerts on stale states and count anomalies

## Running locally

Push to Google Sheet from your machine (useful for ad-hoc updates):

```
pip install google-api-python-client google-auth
python scripts/validate.py && python scripts/refresh_and_push.py
```

Requires `.sheet_config.json` and `.adoption-sheets-sa.json` at the repo root (both gitignored).

## Disabling the nightly push

In the GitHub repo, Actions tab, click `nightly-push` on the left, three-dot menu on the right, Disable workflow.
