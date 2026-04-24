"""Smoke tests for the promote_scraped script.

Runs without network. Builds a fake adoption_data + scraped snapshot
set in memory, calls promote(), and checks each rule independently.

Run:
    python3 scripts/test_promote_scraped.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.promote_scraped import promote, is_src_empty  # noqa: E402


def _fail(msg):
    print(f"FAIL: {msg}")
    sys.exit(1)


def _ok(msg):
    print(f"ok  {msg}")


def run():
    # is_src_empty coverage first (simple predicate).
    for empty in (None, "", "  ", "TBD", "tbd", "N/A", "None", "null"):
        if not is_src_empty(empty):
            _fail(f"is_src_empty should be True for {empty!r}")
    for full in ("https://x.com", "https://example.gov/page"):
        if is_src_empty(full):
            _fail(f"is_src_empty should be False for {full!r}")
    _ok("is_src_empty recognizes empty, TBD, and real URLs")

    today = "2026-04-24"

    adoption = {
        "states": [
            # NC: already has src and last_verified; should bump v and
            # keep existing src. ac starts False, scraped has no active
            # signal so it stays False.
            {
                "code": "NC",
                "name": "North Carolina",
                "last_verified": "2026-04-20",
                "cycles": [
                    {
                        "id": "NC1",
                        "su": "ELA Standards Revision",
                        "src": "https://www.dpi.nc.gov/districts-schools/classroom-resources/office-teaching-and-learning",
                        "v": "2026-04-20",
                        "ac": False,
                    },
                ],
            },
            # UT: cycle has null src (should be filled) and ac False with
            # a scraped active-review signal (should flip to True).
            {
                "code": "UT",
                "name": "Utah",
                "last_verified": "2026-04-15",
                "cycles": [
                    {
                        "id": "UT1",
                        "su": "Mathematics",
                        "src": None,
                        "v": "2026-04-15",
                        "ac": False,
                    },
                ],
            },
            # TX: src is present but differs from scraped source_url;
            # should queue a conflict, not overwrite.
            {
                "code": "TX",
                "name": "Texas",
                "last_verified": "2026-04-15",
                "cycles": [
                    {
                        "id": "TX1",
                        "su": "English / K-12",
                        "src": "https://tea.texas.gov/other-url",
                        "v": "2026-04-15",
                        "ac": True,  # already True; must stay True
                    },
                ],
            },
            # OK: state has no scraped snapshot in the test. Nothing
            # should change for this state.
            {
                "code": "OK",
                "name": "Oklahoma",
                "last_verified": "2026-04-15",
                "cycles": [
                    {
                        "id": "OK1",
                        "su": "Social Studies",
                        "src": "https://oklahoma.gov/existing",
                        "v": "2026-04-15",
                        "ac": False,
                    },
                ],
            },
            # VA: cycle has "TBD" placeholder src; scraped source_url
            # should fill it. No active signal in the scraped snapshot.
            {
                "code": "VA",
                "name": "Virginia",
                "last_verified": "2026-04-10",
                "cycles": [
                    {
                        "id": "VA1",
                        "su": "English",
                        "src": "TBD",
                        "v": "2026-04-10",
                        "ac": False,
                    },
                ],
            },
        ],
    }

    snapshots = {
        "NC": {
            "state": "NC",
            "source_url": "https://www.dpi.nc.gov/districts-schools/classroom-resources/office-teaching-and-learning",
            "cycle_count": 1,
            "has_active_cycle": False,
            "cycles": [{"subject": "ELA Standards Revision"}],
        },
        "UT": {
            "state": "UT",
            "source_url": "https://schools.utah.gov/curr/imc",
            "cycle_count": 1,
            "has_active_review": True,
            "cycles": [{"subject": "Mathematics"}],
        },
        "TX": {
            "state": "TX",
            "source_url": "https://tea.texas.gov/imra",
            "cycle_count": 1,
            "cycles": [{"subject": "English / K-12"}],
        },
        "VA": {
            "state": "VA",
            "source_url": "https://www.doe.virginia.gov/textbooks",
            "cycle_count": 1,
            "cycles": [{"subject": "English"}],
        },
        # OK intentionally absent to confirm no-op path.
    }

    changes, conflicts = promote(adoption, snapshots, today)

    # -------- Assertions --------

    states_by_code = {s["code"]: s for s in adoption["states"]}

    # NC: v bumped, src unchanged, ac unchanged.
    nc = states_by_code["NC"]
    if nc["last_verified"] != today:
        _fail(f"NC last_verified wrong: {nc['last_verified']}")
    c = nc["cycles"][0]
    if c["v"] != today:
        _fail(f"NC cycle v wrong: {c['v']}")
    if c["src"] != "https://www.dpi.nc.gov/districts-schools/classroom-resources/office-teaching-and-learning":
        _fail(f"NC cycle src should be unchanged, got {c['src']}")
    if c["ac"] is not False:
        _fail(f"NC cycle ac should stay False, got {c['ac']}")
    _ok("NC: timestamps bumped, src preserved, ac stays False")

    # UT: v bumped, src filled, ac flipped to True.
    ut = states_by_code["UT"]
    if ut["last_verified"] != today:
        _fail(f"UT last_verified wrong: {ut['last_verified']}")
    c = ut["cycles"][0]
    if c["v"] != today:
        _fail(f"UT cycle v wrong: {c['v']}")
    if c["src"] != "https://schools.utah.gov/curr/imc":
        _fail(f"UT cycle src should be filled, got {c['src']}")
    if c["ac"] is not True:
        _fail(f"UT cycle ac should flip to True, got {c['ac']}")
    _ok("UT: v bumped, null src filled, ac flipped to True")

    # TX: v bumped, src NOT overwritten, conflict logged.
    tx = states_by_code["TX"]
    c = tx["cycles"][0]
    if c["v"] != today:
        _fail(f"TX cycle v wrong: {c['v']}")
    if c["src"] != "https://tea.texas.gov/other-url":
        _fail(f"TX cycle src should NOT be overwritten, got {c['src']}")
    if c["ac"] is not True:
        _fail(f"TX cycle ac should stay True, got {c['ac']}")
    _ok("TX: v bumped, existing src preserved, ac stays True")

    tx_conflicts = [x for x in conflicts if x["state"] == "TX"]
    if len(tx_conflicts) != 1:
        _fail(f"expected 1 TX conflict, got {len(tx_conflicts)}")
    if tx_conflicts[0]["field"] != "src":
        _fail(f"TX conflict field wrong: {tx_conflicts[0]['field']}")
    if tx_conflicts[0]["scraped_value"] != "https://tea.texas.gov/imra":
        _fail(f"TX conflict scraped_value wrong: {tx_conflicts[0]['scraped_value']}")
    _ok("TX: src conflict queued for review with correct values")

    # OK: nothing changed because no snapshot was provided.
    ok = states_by_code["OK"]
    if ok["last_verified"] != "2026-04-15":
        _fail(f"OK last_verified should be unchanged, got {ok['last_verified']}")
    if ok["cycles"][0]["v"] != "2026-04-15":
        _fail(f"OK cycle v should be unchanged, got {ok['cycles'][0]['v']}")
    _ok("OK: untouched because no scraped snapshot was provided")

    # VA: TBD src filled from scraped source_url.
    va = states_by_code["VA"]
    c = va["cycles"][0]
    if c["src"] != "https://www.doe.virginia.gov/textbooks":
        _fail(f"VA cycle src should be filled, got {c['src']}")
    _ok("VA: TBD placeholder src replaced with scraped source_url")

    # Changes summary should have an entry for each state that actually moved.
    states_in_changes = {c["state"] for c in changes}
    expected = {"NC", "UT", "TX", "VA"}
    if not expected.issubset(states_in_changes):
        _fail(f"changes missing states: expected ⊇ {expected}, got {states_in_changes}")
    if "OK" in states_in_changes:
        _fail("OK should not appear in changes (no snapshot)")
    _ok("change summary lists only states that were updated")

    print("\nAll promote_scraped tests passed.")


if __name__ == "__main__":
    run()
