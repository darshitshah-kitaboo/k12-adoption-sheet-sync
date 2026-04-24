"""Coordinator for state adapters.

Runs every registered adapter, writes a fresh snapshot to scraped/<STATE>.json,
and appends a per-run summary to logs/adapter_runs.jsonl. On any adapter
failure, the prior snapshot is retained and the failure is flagged in the log
rather than overwritten with empty data.

The coordinator intentionally does NOT merge into adoption_data.json. That
file is the canonical, human-curated source; scraped data lives alongside it
in scraped/ and is compared by a review step before anything is promoted.
This keeps a bad scrape from corrupting the dashboard.

Exit codes:
    0   at least one adapter produced a non-empty snapshot, no required adapter failed
    1   a required adapter failed or produced zero cycles
    2   unexpected error (missing directories, malformed registry, etc.)

Outputs:
    scraped/<STATE>.json                 latest successful snapshot per state
    scraped/<STATE>.previous.json        one-step-back snapshot (for diffing)
    logs/adapter_runs.jsonl              one JSON line per run with per-state status
    logs/changes/<STATE>-<date>.json     only written when a meaningful field changed

Usage:
    python3 scripts/run_adapters.py                    # run all adapters
    python3 scripts/run_adapters.py --only FL          # run just Florida
    python3 scripts/run_adapters.py --fixture FL=file.html
                                                       # parse local HTML for a state
"""

import argparse
import importlib
import json
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# Allow `python3 scripts/run_adapters.py` from the repo root to import
# `scripts.adapters.*` without requiring PYTHONPATH gymnastics.
sys.path.insert(0, str(ROOT))
SCRAPED_DIR = ROOT / "scraped"
DEBUG_DIR = SCRAPED_DIR / "_debug"
LOGS_DIR = ROOT / "logs"
CHANGES_DIR = LOGS_DIR / "changes"
RUNS_LOG = LOGS_DIR / "adapter_runs.jsonl"

# Max bytes to write into a debug HTML dump. State DOE pages cluster under
# 1 MB; a 2 MB ceiling catches the outliers without letting a runaway
# response bloat the repo.
DEBUG_HTML_MAX_BYTES = 2 * 1024 * 1024

# State code -> adapter module name. As new adapters are written they get
# added here. Required=True means a failure exits nonzero so the workflow
# surfaces the red checkmark; non-required adapters are allowed to fail
# quietly during early rollout.
ADAPTERS = {
    "FL": {"module": "scripts.adapters.fl", "required": True},
    "TX": {"module": "scripts.adapters.tx", "required": True},
    "LA": {"module": "scripts.adapters.la", "required": True},
    "TN": {"module": "scripts.adapters.tn", "required": True},
    "OK": {"module": "scripts.adapters.ok", "required": True},
    # AL, SC, VA are non-required while we debug live page differences.
    # Smoke tests pass on fixtures, but the live pages returned 0 cycles
    # (AL, SC) or 403 Forbidden (VA) in the first CI run. Keeping them in
    # the rotation so changes still show up in the adapter_runs.jsonl log,
    # just not as a workflow failure.
    "AL": {"module": "scripts.adapters.al", "required": False},
    "MS": {"module": "scripts.adapters.ms", "required": True},
    "SC": {"module": "scripts.adapters.sc", "required": False},
    "VA": {"module": "scripts.adapters.va", "required": False},
}

# Fields in a cycle record that count as meaningful when diffing. Changes
# to scraped_at or cycle ordering are ignored; only these fields trigger a
# change-log entry. The set is a union across states: FL cares about bid
# counts and list dates, TX cares about RFIM and rubric PDFs. Records that
# lack a field (e.g. TX has no bid_count) simply compare None to None.
MEANINGFUL_FIELDS = (
    # Florida fields
    "bid_count",
    "latest_list_date",
    "latest_list_url",
    "specifications_url",
    "timeline_url",
    "short_bid_url",
    "detailed_bid_url",
    # Texas fields
    "tier",
    "rfim_url",
    "process_url",
    "suitability_rubric_url",
    "quality_rubric_urls",
    # Louisiana fields
    "rubric_url",
    "weekly_report_url",
    "publisher_guide_url",
    # Tennessee fields
    "commission_meeting",
    "submission_deadline",
    "substitution_template_url",
    "substitution_rule_url",
    "publisher_distr_list_url",
    # Oklahoma fields
    "stc_calendar_url",
    "subject_cycle_calendar_url",
    "data_privacy_form_url",
    "out_of_cycle_flyer_url",
    "supplemental_form_url",
    "substitution_memo_url",
    "substitution_flyer_url",
    "substitution_guidance_url",
    # Alabama fields
    "approved_list_url",
    "approved_board_meeting_date",
    "pending_list_url",
    "pending_board_meeting_date",
    # Mississippi fields
    "adopted_materials_url",
    # South Carolina fields
    "approved_materials_url",
    # Virginia fields
    "current_review_title",
    "current_review_url",
    "current_review_date",
)


def load_adapter(module_name):
    """Import an adapter module. Raises ImportError if missing."""
    return importlib.import_module(module_name)


def run_one(state_code, config, fixture_path=None):
    """Run a single adapter.

    Returns (snapshot_dict_or_None, error_or_None, html_or_None).

    The html return lets main() dump the raw HTML on failure so the next
    CI run commits an inspectable copy of exactly what the adapter saw.
    html is None when the failure happens before fetch (import or fetch
    errors) or when a fixture path is in use.
    """
    try:
        mod = load_adapter(config["module"])
    except Exception as e:
        return None, f"import failed: {e}", None

    html = None
    try:
        if fixture_path:
            html = Path(fixture_path).read_text(encoding="utf-8")
        else:
            html = mod.fetch_html()
    except Exception as e:
        # Fetch failed (403, DNS, timeout). No HTML to dump; the error
        # itself is what the user needs to see.
        return None, f"fetch failed: {type(e).__name__}: {e}", None

    try:
        data = mod.parse(html)
    except Exception as e:
        return None, f"{type(e).__name__}: {e}\n{traceback.format_exc()}", html

    if not isinstance(data, dict) or "cycles" not in data:
        return None, "adapter returned malformed data (no cycles field)", html

    # Empty snapshots usually mean the source page changed shape. Treat as
    # a failure so we don't overwrite yesterday's good data with nothing.
    if data.get("cycle_count", 0) == 0:
        return (None,
                "adapter returned zero cycles (page structure likely changed)",
                html)

    return data, None, html


def write_debug_html(state_code, html):
    """Write the fetched HTML to scraped/_debug/<STATE>_latest.html.

    Called on any adapter failure or zero-cycle result that did have a
    successful fetch. The file is overwritten each run so the repo does
    not accumulate HTML history; git history alone preserves the prior
    version if anyone wants to compare. Large responses are truncated at
    DEBUG_HTML_MAX_BYTES so one runaway page cannot blow up the repo.
    """
    if html is None:
        return None
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    path = DEBUG_DIR / f"{state_code}_latest.html"
    payload = html
    if len(payload) > DEBUG_HTML_MAX_BYTES:
        payload = payload[:DEBUG_HTML_MAX_BYTES] + (
            "\n<!-- truncated by run_adapters.py at "
            f"{DEBUG_HTML_MAX_BYTES} bytes -->\n"
        )
    path.write_text(payload, encoding="utf-8")
    return path


def diff_snapshots(old, new):
    """Compare two snapshots on MEANINGFUL_FIELDS. Returns list of change dicts.

    Cycles are matched by (subject, ay_start, ay_end). New and removed cycles
    are reported as well.
    """
    def key(c):
        # Tier distinguishes TX records where the same subject string can
        # appear in multiple tiers (e.g. "K-12 English mathematics" shows
        # up in both Full-subject and Supplemental). FL records have no
        # tier field so the component is None for them.
        return (c.get("subject"), c.get("ay_start"), c.get("ay_end"), c.get("tier"))

    old_by_key = {key(c): c for c in (old or {}).get("cycles", [])}
    new_by_key = {key(c): c for c in (new or {}).get("cycles", [])}

    changes = []
    for k in new_by_key.keys() - old_by_key.keys():
        changes.append({"type": "added", "key": list(k), "cycle": new_by_key[k]})
    for k in old_by_key.keys() - new_by_key.keys():
        changes.append({"type": "removed", "key": list(k), "cycle": old_by_key[k]})
    for k in old_by_key.keys() & new_by_key.keys():
        o, n = old_by_key[k], new_by_key[k]
        field_changes = {}
        for f in MEANINGFUL_FIELDS:
            if o.get(f) != n.get(f):
                field_changes[f] = {"old": o.get(f), "new": n.get(f)}
        if field_changes:
            changes.append({"type": "modified", "key": list(k), "fields": field_changes})
    return changes


def _snapshots_equivalent(a, b):
    """True if two snapshots match on everything except scraped_at.

    A run that only differs in the timestamp shouldn't dirty the file,
    otherwise every scheduled run produces a bot commit for nothing.
    """
    def canon(d):
        return {k: v for k, v in (d or {}).items() if k != "scraped_at"}
    return canon(a) == canon(b)


def write_snapshot(state_code, data):
    """Write a fresh snapshot only if meaningful content moved.

    Returns the prior snapshot for the caller to diff against. If the new
    payload is equivalent to the existing file on every field except
    scraped_at, the file is NOT rotated or rewritten. That way a run on
    a quiet day only touches logs/adapter_runs.jsonl (+1 audit line) and
    doesn't trigger a bot commit for the snapshot itself.
    """
    SCRAPED_DIR.mkdir(parents=True, exist_ok=True)
    current = SCRAPED_DIR / f"{state_code}.json"
    previous = SCRAPED_DIR / f"{state_code}.previous.json"

    old_data = None
    if current.exists():
        try:
            old_data = json.loads(current.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            old_data = None

    if old_data and _snapshots_equivalent(old_data, data):
        return old_data

    if current.exists():
        current.replace(previous)
    current.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return old_data


def write_changes(state_code, changes):
    """Persist a changes record if there were any, named by UTC date."""
    if not changes:
        return None
    CHANGES_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).date().isoformat()
    path = CHANGES_DIR / f"{state_code}-{today}.json"
    # If multiple runs land on the same day, append a counter to avoid losing data.
    counter = 1
    while path.exists():
        counter += 1
        path = CHANGES_DIR / f"{state_code}-{today}-{counter}.json"
    path.write_text(json.dumps({"state": state_code, "date": today,
                                "change_count": len(changes),
                                "changes": changes}, indent=2),
                    encoding="utf-8")
    return path


def append_run_log(entry):
    """One JSON line per run. Cheap to tail, easy to ingest later."""
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    with RUNS_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", action="append",
                    help="Run only these state codes (repeatable)")
    ap.add_argument("--fixture", action="append", default=[],
                    help="STATE=PATH to parse a local HTML fixture instead of fetching")
    args = ap.parse_args()

    # Parse --fixture STATE=PATH pairs.
    fixtures = {}
    for spec in args.fixture:
        if "=" not in spec:
            print(f"FATAL: --fixture expects STATE=PATH, got {spec!r}", file=sys.stderr)
            sys.exit(2)
        state, path = spec.split("=", 1)
        fixtures[state.upper()] = path

    selected = set(c.upper() for c in args.only) if args.only else set(ADAPTERS)
    unknown = selected - set(ADAPTERS)
    if unknown:
        print(f"FATAL: unknown state codes: {sorted(unknown)}", file=sys.stderr)
        sys.exit(2)

    started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    per_state = {}
    any_failed_required = False
    any_success = False

    print(f"Coordinator started {started_at}")
    print(f"Running {len(selected)} adapter(s): {sorted(selected)}")

    for code in sorted(selected):
        cfg = ADAPTERS[code]
        fixture = fixtures.get(code)
        data, err, html = run_one(code, cfg, fixture_path=fixture)

        if err:
            # Adapter failed. Keep yesterday's snapshot untouched and log the
            # failure. Required adapters bubble up as a workflow failure.
            # If we have HTML (parse or zero-cycle failure), dump it to
            # scraped/_debug/ so CI commits it and we can inspect what the
            # adapter actually saw. Fixture runs skip the dump because the
            # input file already exists.
            debug_path = None
            if html is not None and not fixture:
                debug_path = write_debug_html(code, html)
            per_state[code] = {
                "status": "failed",
                "required": cfg["required"],
                "error": err.splitlines()[0],
                "cycle_count": 0,
                "change_count": 0,
                "debug_html": (str(debug_path.relative_to(ROOT))
                               if debug_path else None),
            }
            if cfg["required"]:
                any_failed_required = True
            debug_note = f"  [html dumped to {debug_path.relative_to(ROOT)}]" if debug_path else ""
            print(f"  {code}  FAILED  {err.splitlines()[0]}{debug_note}")
            continue

        old = write_snapshot(code, data)
        changes = diff_snapshots(old, data)
        changes_path = write_changes(code, changes)
        per_state[code] = {
            "status": "ok",
            "required": cfg["required"],
            "cycle_count": data["cycle_count"],
            "change_count": len(changes),
            "changes_file": str(changes_path.relative_to(ROOT)) if changes_path else None,
            "scraped_at": data["scraped_at"],
        }
        any_success = True
        tag = f"{len(changes)} change(s)" if changes else "no changes"
        print(f"  {code}  OK      {data['cycle_count']} cycles, {tag}")

    finished_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    entry = {
        "started_at": started_at,
        "finished_at": finished_at,
        "selected": sorted(selected),
        "per_state": per_state,
    }
    append_run_log(entry)

    print(f"\nRun log appended to {RUNS_LOG.relative_to(ROOT)}")
    if any_failed_required:
        print("\nOne or more REQUIRED adapters failed. Exiting 1.", file=sys.stderr)
        sys.exit(1)
    if not any_success:
        print("\nNo adapter produced a snapshot. Exiting 1.", file=sys.stderr)
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
