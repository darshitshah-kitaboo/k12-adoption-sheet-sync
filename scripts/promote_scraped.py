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

2. Match each adoption_data cycle to the matching scraped cycle by
   subject. The scraped cycle may carry an "actionable" URL in one of
   the fields call_for_bids_url, invitation_to_submit_url,
   current_review_url. These are the URLs a publisher actually needs
   during an open call.

3. If a cycle's `ac` (Active Call Open) is False but the scraped
   snapshot reports an active cycle (state-level has_active_cycle /
   has_active_review, or a cycle-level actionable URL from rule 2),
   flip `ac` to True. Does NOT flip True -> False; adapters can miss a
   Call for Bids that exists on a subpage, so the False->True direction
   is the only safe automation.

4. Resolve the `src` field:
     a. If src is empty/TBD, fill it with the best URL available
        (cycle actionable URL first, then snapshot source_url).
     b. If the cycle is active (ac True) and the scraper provided a
        cycle-level actionable URL, replace src with that URL. For an
        open call, the bid packet or submission link is what belongs
        in the sheet. The old value is preserved in git history.
     c. Otherwise, if an existing src disagrees with the snapshot's
        source_url, do NOT overwrite. Queue it in pending_review.json
        for manual resolution. Exception: suppress the queue entry
        when the existing src is clearly more specific than the
        scraper's fallback (PDF vs landing page, deeper path vs
        shallower path). In that case the existing value is obviously
        better so there is nothing for a human to decide.

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
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
ADOPTION_PATH = ROOT / "adoption_data.json"
SCRAPED_DIR = ROOT / "scraped"
PENDING_REVIEW_PATH = ROOT / "logs" / "pending_review.json"

# Values that count as "not set" for the src field. An empty string, a
# literal TBD placeholder, or a whitespace-only string all qualify so we
# can fill them in without overwriting curated content.
SRC_EMPTY_MARKERS = {"", "tbd", "n/a", "none", "null"}

# Keys on a scraped cycle that point to actionable URLs a publisher
# would use during an open call. Checked in priority order.
ACTIONABLE_CYCLE_KEYS = (
    "call_for_bids_url",
    "invitation_to_submit_url",
    "current_review_url",
)

# File extensions that indicate a URL points to a specific artifact
# rather than a generic landing page.
SPECIFIC_DOC_EXTS = (".pdf", ".doc", ".docx", ".xls", ".xlsx")


def is_src_empty(value):
    """True if a cycle's src field counts as missing."""
    if value is None:
        return True
    if isinstance(value, str) and value.strip().lower() in SRC_EMPTY_MARKERS:
        return True
    return False


def scraped_has_active_signal(snap):
    """True if the scraped snapshot suggests any active cycle.

    Used as the state-level fallback when there is no cycle-level
    actionable URL. Looks at state-level flags first, then any cycle-
    level actionable URLs.
    """
    if snap.get("has_active_cycle") or snap.get("has_active_review"):
        return True
    for cycle in snap.get("cycles", []) or []:
        for key in ACTIONABLE_CYCLE_KEYS:
            if cycle.get(key):
                return True
    return False


def find_scraped_cycle(snap_cycles, adoption_cycle):
    """Match an adoption_data cycle to a scraped cycle by subject.

    Exact case-insensitive subject match first, then loose substring
    match in either direction. Returns None when nothing matches or
    the adoption cycle has no subject.
    """
    adoption_subject = (adoption_cycle.get("su") or "").strip().lower()
    if not adoption_subject:
        return None
    for sc in snap_cycles or []:
        scraped_subject = (sc.get("subject") or "").strip().lower()
        if scraped_subject and scraped_subject == adoption_subject:
            return sc
    for sc in snap_cycles or []:
        scraped_subject = (sc.get("subject") or "").strip().lower()
        if not scraped_subject:
            continue
        if (adoption_subject in scraped_subject
                or scraped_subject in adoption_subject):
            return sc
    return None


def actionable_url(scraped_cycle):
    """First non-empty actionable URL on a scraped cycle, else None."""
    if not scraped_cycle:
        return None
    for key in ACTIONABLE_CYCLE_KEYS:
        url = scraped_cycle.get(key)
        if url:
            return url
    return None


def _path_depth(url):
    """Count path segments in a URL. Returns 0 on parse failure."""
    if not url or not isinstance(url, str):
        return 0
    try:
        path = urlparse(url).path.strip("/")
    except (ValueError, AttributeError):
        return 0
    return len([p for p in path.split("/") if p])


def is_more_specific(candidate, fallback):
    """True if `candidate` looks more specific than `fallback`.

    Used to suppress a conflict log when the existing src in
    adoption_data.json is clearly a better artifact than the scraper's
    generic landing page. Rules, in order:
      - candidate ends in a document extension and fallback does not.
      - candidate's URL path is deeper than fallback's.

    Returns False when either URL is missing.
    """
    if not candidate or not fallback:
        return False
    c_lower = candidate.lower().split("?")[0].rstrip("/")
    f_lower = fallback.lower().split("?")[0].rstrip("/")
    c_is_doc = any(c_lower.endswith(ext) for ext in SPECIFIC_DOC_EXTS)
    f_is_doc = any(f_lower.endswith(ext) for ext in SPECIFIC_DOC_EXTS)
    if c_is_doc and not f_is_doc:
        return True
    return _path_depth(candidate) > _path_depth(fallback)


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
    """Apply promotion rules in place. Returns (changes, conflicts)."""
    changes = []
    conflicts = []

    for state in adoption.get("states", []):
        code = (state.get("code") or "").upper()
        snap = snapshots.get(code)
        if not snap:
            continue
        if (snap.get("cycle_count") or 0) == 0:
            continue

        state_summary = {
            "state": code,
            "verified_bumped": False,
            "cycles_verified": 0,
            "src_filled": 0,
            "src_replaced_active": 0,
            "ac_flipped": 0,
        }

        # Rule 1a: state-level verified timestamp.
        if state.get("last_verified") != today_iso:
            state["last_verified"] = today_iso
            state_summary["verified_bumped"] = True

        state_active_signal = scraped_has_active_signal(snap)
        scraped_source_url = snap.get("source_url")
        snap_cycles = snap.get("cycles", []) or []

        for cycle in state.get("cycles", []) or []:
            # Rule 1b: per-cycle verified timestamp.
            if cycle.get("v") != today_iso:
                cycle["v"] = today_iso
                state_summary["cycles_verified"] += 1

            scraped_cycle = find_scraped_cycle(snap_cycles, cycle)
            cycle_actionable = actionable_url(scraped_cycle)

            # Rule 3: flip ac False -> True on any active signal.
            cycle_active_signal = (
                bool(cycle_actionable) or state_active_signal)
            if cycle_active_signal and cycle.get("ac") is False:
                cycle["ac"] = True
                state_summary["ac_flipped"] += 1

            # Rule 4: resolve src.
            current_src = cycle.get("src")
            best_url = cycle_actionable or scraped_source_url

            if is_src_empty(current_src) and best_url:
                # 4a: empty src gets the best URL available.
                cycle["src"] = best_url
                state_summary["src_filled"] += 1
            elif (cycle.get("ac") is True
                  and cycle_actionable
                  and current_src != cycle_actionable):
                # 4b: active cycle with a cycle-level actionable URL
                # wins over whatever was there. The bid packet or
                # submission link is what belongs in the sheet while
                # the call is open.
                cycle["src"] = cycle_actionable
                state_summary["src_replaced_active"] += 1
            elif (scraped_source_url
                  and current_src
                  and not is_src_empty(current_src)
                  and current_src != scraped_source_url
                  and current_src != cycle_actionable):
                # 4c: inactive cycle with a real src mismatch.
                # Suppress the log when the existing src is clearly
                # more specific than the scraper's fallback (PDF vs
                # landing page, or deeper path). Keeping a curated
                # specific URL is always preferable to a generic
                # landing page, so there is nothing for a human to
                # decide in those cases.
                scraper_offer = cycle_actionable or scraped_source_url
                if not is_more_specific(current_src, scraper_offer):
                    conflicts.append({
                        "state": code,
                        "cycle_id": cycle.get("id", ""),
                        "subject": cycle.get("su", ""),
                        "field": "src",
                        "adoption_data_value": current_src,
                        "scraped_value": scraper_offer,
                        "note": ("adoption_data.json src does not match "
                                 "the scraped URL. Confirm which is "
                                 "canonical."),
                    })

        if any([state_summary["verified_bumped"],
                state_summary["cycles_verified"],
                state_summary["src_filled"],
                state_summary["src_replaced_active"],
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
              f"src_replaced_active={c['src_replaced_active']}, "
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
        PENDING_REVIEW_PATH.unlink()
        print(f"Cleared empty {PENDING_REVIEW_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
