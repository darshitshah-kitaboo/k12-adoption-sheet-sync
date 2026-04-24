"""Promote scraped findings into adoption_data.json.

Runs after the coordinator in refresh-data.yml. Reads the fresh
scraped/<STATE>.json snapshots and applies a narrow set of safe
edits to adoption_data.json so the nightly push to Google Sheets
surfaces current data without a human in the loop.

Promotion rules (conservative by design):

1. When a state's scraper returned a non-empty snapshot, treat the
   state's DOE page as verified today. Update:
     - state.last_verified  = today
     - every cycle.v        = today
   Rationale: the scraper loaded the page, found the expected
   structural anchors, and emitted cycles. That is a genuine freshness
   signal even if the scraper did not verify every field on every cycle.

2. If a cycle's `src` is null, empty, or a "TBD" placeholder, fill it
   in with the scraped snapshot's `source_url`. Does NOT overwrite an
   existing src value. Safe because we only write where there is no
   curated value to lose.

3. If a cycle's `ac` (Active Call Open) is False but the scraped
   snapshot reports an active cycle (`has_active_cycle`,
   `has_active_review`, or any cycle-level invitation/call URL), flip
   `ac` to True. Does NOT flip an Active Call from True to False;
   adapters can miss a Call for Bids that exists on a subpage, so the
   False->True direction is the only safe automation.

4. Anything that would be an overwrite (scraped src differs from
   adoption_data src, scraped AY bounds disagree, etc.) is NOT applied.
   Instead it is appended to logs/pending_review.json with enough
   context for the user to resolve manually.

Outputs:
    adoption_data.json                 updated in-place (only when fields
                                       actually changed)
    logs/pending_review.json           review queue (only written when
                                       there is at least one conflict)

Usage:
    python3 scripts/promote_scraped.py
    python3 scripts/promote_scraped.py --dry-run   (prints diff, no writes)

Exit codes:
    0   success (promotion ran or nothing to promote)
    1   error loading adoption_data.json or missing directories
"""

import argparse
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ADOPTION_PATH = ROOT / "adoption_data.json"
SCRAPED_DIR = ROOT / "scraped"
PENDING_REVIEW_PATH = ROOT / "logs" / "pending_review.json"

# Values that count as "not set" for the src field. An empty string, a
# literal TBD placeholder, or a whitespace-only string all qualify so we
# can fill them in without overwriting curated content.
SRC_EMPTY_MARKERS = {"", "tbd", "n/a", "none", "null"}


def is_src_empty(value):
    """True if a cycle's src field counts as missing."""
    if value is None:
        return True
    if isinstance(value, str) and value.strip().lower() in SRC_EMPTY_MARKERS:
        return True
    return False


def scraped_has_active_signal(snap):
    """Return True if the scraped snapshot suggests an active cycle.

    Looks at state-level flags first, then any cycle-level fields that
    name a Call for Bids, Invitation to Submit, or similar.
    """
    if snap.get("has_active_cycle") or snap.get("has_active_review"):
        return True
    for cycle in snap.get("cycles", []) or []:
        for key in ("call_for_bids_url", "invitation_to_submit_url",
                    "current_review_url"):
            if cycle.get(key):
                return True
    return False


def load_scraped_snapshots():
    """Load every scraped/<STATE>.json file into a dict keyed by code."""
    out = {}
    if not SCRAPED_DIR.exists():
        return out
    for path in sorted(SCRAPED_DIR.glob("*.json")):
        if path.name.endswith(".previous.json"):
            continue
        try:
            with path.open(encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        code = data.get("state") or path.stem
        if isinstance(code, str):
            out[code.upper()] = data
    return out


def promote(adoption, snapshots, today_iso):
    """Apply promotion rules in place on `adoption`. Returns (changes, conflicts).

    `changes` is a list of per-state change summaries for logging.
    `conflicts` is a list of review queue entries that need a human.
    """
    changes = []
    conflicts = []

    for state in adoption.get("states", []):
        code = (state.get("code") or "").upper()
        snap = snapshots.get(code)
        if not snap:
            # No scraped snapshot for this state. Nothing to promote.
            continue
        if (snap.get("cycle_count") or 0) == 0:
            # Adapter ran but returned nothing. Skip; the coordinator's
            # own zero-cycle failure mode already surfaces this.
            continue

        state_summary = {
            "state": code,
            "verified_bumped": False,
            "cycles_verified": 0,
            "src_filled": 0,
            "ac_flipped": 0,
        }

        # Rule 1a: state-level verified timestamp.
        if state.get("last_verified") != today_iso:
            state["last_verified"] = today_iso
            state_summary["verified_bumped"] = True

        active_signal = scraped_has_active_signal(snap)
        scraped_source_url = snap.get("source_url")

        for cycle in state.get("cycles", []) or []:
            # Rule 1b: per-cycle verified timestamp.
            if cycle.get("v") != today_iso:
                cycle["v"] = today_iso
                state_summary["cycles_verified"] += 1

            # Rule 2: fill missing src from the scraped page URL.
            if is_src_empty(cycle.get("src")) and scraped_source_url:
                cycle["src"] = scraped_source_url
                state_summary["src_filled"] += 1
            elif (scraped_source_url
                  and cycle.get("src")
                  and cycle.get("src") != scraped_source_url
                  and not is_src_empty(cycle.get("src"))):
                # Rule 4: existing src disagrees with scraped source.
                # Do not overwrite; queue for review.
                conflicts.append({
                    "state": code,
                    "cycle_id": cycle.get("id", ""),
                    "subject": cycle.get("su", ""),
                    "field": "src",
                    "adoption_data_value": cycle.get("src"),
                    "scraped_value": scraped_source_url,
                    "note": ("adoption_data.json src does not match the "
                             "scraped DOE landing page. Confirm which is "
                             "canonical."),
                })

            # Rule 3: flip ac False -> True only. Never True -> False.
            if active_signal and cycle.get("ac") is False:
                cycle["ac"] = True
                state_summary["ac_flipped"] += 1

        # Only keep the change summary if something actually moved.
        if any([state_summary["verified_bumped"],
                state_summary["cycles_verified"],
                state_summary["src_filled"],
                state_summary["ac_flipped"]]):
            changes.append(state_summary)

    return changes, conflicts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="Print changes but do not modify files")
    args = ap.parse_args()

    if not ADOPTION_PATH.exists():
        print(f"FATAL: {ADOPTION_PATH} missing", file=sys.stderr)
        sys.exit(1)

    with ADOPTION_PATH.open(encoding="utf-8") as f:
        adoption = json.load(f)

    snapshots = load_scraped_snapshots()
    if not snapshots:
        print("No scraped snapshots found. Nothing to promote.")
        sys.exit(0)

    today_iso = date.today().isoformat()
    changes, conflicts = promote(adoption, snapshots, today_iso)

    print(f"Promotion summary for {today_iso}:")
    if not changes:
        print("  (no fields changed)")
    for c in changes:
        print(f"  {c['state']}: "
              f"verified_bumped={c['verified_bumped']}, "
              f"cycles_verified={c['cycles_verified']}, "
              f"src_filled={c['src_filled']}, "
              f"ac_flipped={c['ac_flipped']}")

    if conflicts:
        print(f"\n{len(conflicts)} conflict(s) queued for review:")
        for c in conflicts:
            print(f"  {c['state']} {c['cycle_id']} {c['field']}: "
                  f"{c['adoption_data_value']!r} vs {c['scraped_value']!r}")

    if args.dry_run:
        print("\n--dry-run: no files written")
        return

    if changes:
        with ADOPTION_PATH.open("w", encoding="utf-8") as f:
            json.dump(adoption, f, indent=2, ensure_ascii=False)
            f.write("\n")
        print(f"\nWrote {ADOPTION_PATH.relative_to(ROOT)}")

    if conflicts:
        PENDING_REVIEW_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "generated_at": datetime.now(timezone.utc).isoformat(
                timespec="seconds"),
            "count": len(conflicts),
            "conflicts": conflicts,
        }
        with PENDING_REVIEW_PATH.open("w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
            f.write("\n")
        print(f"Wrote {PENDING_REVIEW_PATH.relative_to(ROOT)}")
    elif PENDING_REVIEW_PATH.exists():
        # Clear a stale review queue if today's run has no conflicts.
        # Preserves git history via the commit.
        PENDING_REVIEW_PATH.unlink()
        print(f"Cleared empty {PENDING_REVIEW_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
