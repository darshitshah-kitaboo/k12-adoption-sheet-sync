"""
Validation gate for adoption_data.json.

Runs before refresh_and_push.py. If this script exits non-zero, the workflow
aborts and nothing gets written to the Google Sheet. Bad data never reaches
the front-end.

Checks performed:
  Schema           Required fields exist, types are correct
  Formats          Dates are valid ISO, URLs are https, codes are 2 letters
  References       Every cycle's state code matches a known state
  Uniqueness       No duplicate cycle IDs, no duplicate state codes
  Completeness     Every cycle has a primary source URL
  Counts           Soft thresholds flag suspicious drops

Exit codes:
  0   all checks passed
  1   one or more checks failed (details logged)
"""

import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_PATH = ROOT / "adoption_data.json"

US_STATE_CODES = {
    "AL","AK","AZ","AR","CA","CO","CT","DE","DC","FL","GA","HI","ID","IL","IN",
    "IA","KS","KY","LA","ME","MD","MA","MI","MN","MS","MO","MT","NE","NV","NH",
    "NJ","NM","NY","NC","ND","OH","OK","OR","PA","RI","SC","SD","TN","TX","UT",
    "VT","VA","WA","WV","WI","WY",
}

ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
HTTPS_URL = re.compile(r"^https://[^\s]+$")

# Soft thresholds. If counts drop below these, the push is blocked. The idea
# is to catch accidental data loss (bad scrape wipes most cycles) before it
# corrupts the front-end. Adjust when real data expands beyond these floors.
MIN_STATES = 50
MIN_CYCLES = 20
MIN_ENROLLMENT_ENTRIES = 50


def fail(errors, msg):
    errors.append(msg)


def check_schema(data, errors):
    if "states" not in data or "enrollment" not in data:
        fail(errors, "Missing top-level 'states' or 'enrollment' key")
        return
    if not isinstance(data["states"], list):
        fail(errors, "'states' must be a list")
    if not isinstance(data["enrollment"], dict):
        fail(errors, "'enrollment' must be a dict")


def check_state_fields(data, errors):
    required = ["code", "name", "governance", "status", "cycles"]
    seen_codes = set()
    for idx, s in enumerate(data.get("states", [])):
        for field in required:
            if field not in s:
                fail(errors, f"State at index {idx} missing required field: {field}")
        code = s.get("code")
        if code:
            if code in seen_codes:
                fail(errors, f"Duplicate state code: {code}")
            seen_codes.add(code)
            if code not in US_STATE_CODES:
                fail(errors, f"Unknown state code: {code}")
        if not isinstance(s.get("cycles", []), list):
            fail(errors, f"State {code}: 'cycles' must be a list")


def check_cycle_fields(data, errors):
    required = ["id", "su", "gr", "gd", "st"]
    seen_ids = set()
    for s in data.get("states", []):
        code = s.get("code", "?")
        for c in s.get("cycles", []):
            for field in required:
                if field not in c:
                    fail(errors, f"{code}/{c.get('id','?')}: missing required field '{field}'")
            cid = c.get("id")
            if cid:
                if cid in seen_ids:
                    fail(errors, f"Duplicate cycle id: {cid}")
                seen_ids.add(cid)

            # Primary source is mandatory for traceability
            if not c.get("src"):
                fail(errors, f"{code}/{cid}: missing primary source URL (src)")
            elif not HTTPS_URL.match(c.get("src", "")):
                fail(errors, f"{code}/{cid}: primary source is not a valid https URL")

            # Last-verified MUST be ISO. It drives freshness indicators.
            v = c.get("v")
            if v and not ISO_DATE.match(str(v)):
                fail(errors, f"{code}/{cid}.v: last_verified '{v}' must be ISO YYYY-MM-DD")

            # Contract start/end and deadline can legitimately be free-text
            # (TBD, "Fall 2027", "Until superseded") but cycle IDs pointing to
            # the timeline widget should have ISO dl when possible. We don't
            # fail on this; only warn if dl is non-ISO AND cycle is marked
            # Active, because that means countdown won't render.
            dl = c.get("dl")
            if c.get("ac") and dl and not ISO_DATE.match(str(dl)):
                fail(errors, f"{code}/{cid}: active cycle has non-ISO dl '{dl}'. "
                             f"The front-end countdown needs a real date here.")

            # Key events milestones
            for i, ev in enumerate(c.get("ke") or []):
                if not ev.get("d") or not ISO_DATE.match(ev["d"]):
                    fail(errors, f"{code}/{cid}.ke[{i}]: invalid or missing date")
                if not ev.get("l"):
                    fail(errors, f"{code}/{cid}.ke[{i}]: missing label")

            # Extra sources
            for i, src in enumerate(c.get("src2") or []):
                if not HTTPS_URL.match(src.get("u", "")):
                    fail(errors, f"{code}/{cid}.src2[{i}]: url is not https")


def check_enrollment(data, errors):
    for code, e in data.get("enrollment", {}).items():
        if code not in US_STATE_CODES:
            fail(errors, f"Enrollment code '{code}' is not a known US state")
        if not isinstance(e.get("total"), int) or e["total"] <= 0:
            fail(errors, f"Enrollment {code}: 'total' must be a positive integer")


def check_counts(data, errors):
    n_states = len(data.get("states", []))
    n_cycles = sum(len(s.get("cycles", [])) for s in data.get("states", []))
    n_enroll = len(data.get("enrollment", {}))
    if n_states < MIN_STATES:
        fail(errors, f"States count {n_states} below floor {MIN_STATES}")
    if n_cycles < MIN_CYCLES:
        fail(errors, f"Cycles count {n_cycles} below floor {MIN_CYCLES}")
    if n_enroll < MIN_ENROLLMENT_ENTRIES:
        fail(errors, f"Enrollment count {n_enroll} below floor {MIN_ENROLLMENT_ENTRIES}")
    print(f"  States: {n_states} (floor {MIN_STATES})")
    print(f"  Cycles: {n_cycles} (floor {MIN_CYCLES})")
    print(f"  Enrollment entries: {n_enroll} (floor {MIN_ENROLLMENT_ENTRIES})")


def check_reference_integrity(data, errors):
    state_codes = {s.get("code") for s in data.get("states", [])}
    enroll_codes = set(data.get("enrollment", {}).keys())
    missing = state_codes - enroll_codes - {None}
    if missing:
        fail(errors, f"States without enrollment entries: {sorted(missing)}")


def main():
    if not DATA_PATH.exists():
        print(f"FATAL: {DATA_PATH} missing")
        sys.exit(1)

    with DATA_PATH.open() as f:
        data = json.load(f)

    print(f"Validating {DATA_PATH.name} at {datetime.now().isoformat(timespec='seconds')}")
    errors = []

    check_schema(data, errors)
    check_counts(data, errors)
    check_state_fields(data, errors)
    check_cycle_fields(data, errors)
    check_enrollment(data, errors)
    check_reference_integrity(data, errors)

    if errors:
        print(f"\nVALIDATION FAILED. {len(errors)} issue(s):")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)

    print("\nAll checks passed. Safe to push.")
    sys.exit(0)


if __name__ == "__main__":
    main()
