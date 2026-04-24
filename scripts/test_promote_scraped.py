"""Smoke tests for the promote_scraped script.

Runs without network. Builds a fake adoption_data + scraped snapshot
set in memory, calls promote(), and checks each rule independently.

Run:
    python3 scripts/test_promote_scraped.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.promote_scraped import (  # noqa: E402
    actionable_url,
    find_scraped_cycle,
    is_more_specific,
    is_src_empty,
    promote,
)


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

    # actionable_url picks first non-empty in priority order.
    if actionable_url(None) is not None:
        _fail("actionable_url(None) should return None")
    if actionable_url({}) is not None:
        _fail("actionable_url({}) should return None")
    cyc = {"invitation_to_submit_url": "https://x/its",
           "call_for_bids_url": "https://x/cfb"}
    if actionable_url(cyc) != "https://x/cfb":
        _fail("actionable_url should prefer call_for_bids_url")
    _ok("actionable_url prefers call_for_bids_url then invitation then review")

    # is_more_specific: PDF beats landing page, deeper path beats shallower.
    if not is_more_specific(
            "https://x.gov/uploads/2026/bid-packet.pdf",
            "https://x.gov/adoption/"):
        _fail("PDF should be more specific than landing page")
    if not is_more_specific(
            "https://x.gov/newsroom/news-releases/rfb-2026/",
            "https://x.gov/instruction/"):
        _fail("deeper path should be more specific than shallower")
    if is_more_specific(
            "https://x.gov/a/",
            "https://x.gov/a/b/c/"):
        _fail("shallower path should NOT be more specific than deeper")
    if is_more_specific(None, "https://x.gov/a/"):
        _fail("None candidate should not be more specific")
    _ok("is_more_specific treats PDFs and deeper paths as more specific")

    # find_scraped_cycle matches by subject with exact + loose fallback.
    snap_cycles = [{"subject": "Mathematics"},
                   {"subject": "ELA Standards Revision"}]
    m = find_scraped_cycle(snap_cycles, {"su": "mathematics"})
    if not m or m["subject"] != "Mathematics":
        _fail("exact subject match failed")
    m = find_scraped_cycle(snap_cycles, {"su": "ELA"})
    if not m or "ELA" not in m["subject"]:
        _fail("loose substring match failed")
    if find_scraped_cycle(snap_cycles, {"su": "Chemistry"}) is not None:
        _fail("non-matching subject should return None")
    _ok("find_scraped_cycle handles exact and loose subject matches")

    # Normalization: '&' in adoption_data vs 'and' in scraped h3 must match.
    ampersand_snap = [{"subject": "Digital Literacy and Computer Science"}]
    m = find_scraped_cycle(ampersand_snap, {"su": "Digital Literacy & Computer Science"})
    if not m:
        _fail("'&' vs 'and' subjects should match via normalization")
    _ok("find_scraped_cycle treats '&' and 'and' as equivalent")

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
            # TX: src is present but differs from scraped source_url, and
            # scraper did NOT provide a cycle-level actionable URL. This
            # should queue a conflict, not overwrite. ac stays True.
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
                        "ac": True,
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
            # AL: existing src is a specific bid packet PDF and scraper
            # only has a generic landing page as source_url. This should
            # NOT log a conflict because the existing URL is clearly
            # more specific than the scraper's offer.
            {
                "code": "AL",
                "name": "Alabama",
                "last_verified": "2026-04-20",
                "cycles": [
                    {
                        "id": "AL1",
                        "su": "Digital Literacy & Computer Science",
                        "src": "https://www.alabamaachieves.org/wp-content/uploads/2026/02/TAP_20260227_BidPacket.pdf",
                        "v": "2026-04-20",
                        "ac": False,
                    },
                ],
            },
            # ID: existing src points at a landing page and scraper has a
            # cycle-level call_for_bids_url. Rule 4b should flip ac True
            # AND replace src with the actionable URL. No conflict.
            {
                "code": "ID",
                "name": "Idaho",
                "last_verified": "2026-04-15",
                "cycles": [
                    {
                        "id": "ID1",
                        "su": "Science",
                        "src": "https://www.sde.idaho.gov/instructional-materials/",
                        "v": "2026-04-15",
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
        # AL scraped subject uses "and" while adoption_data uses "&".
        # find_scraped_cycle must normalize them to match.
        "AL": {
            "state": "AL",
            "source_url": "https://www.alabamaachieves.org/content-areas-specialty/textbook-adoption-and-procurement/",
            "cycle_count": 1,
            "cycles": [{"subject": "Digital Literacy and Computer Science"}],
        },
        "ID": {
            "state": "ID",
            "source_url": "https://www.sde.idaho.gov/instructional-materials/",
            "cycle_count": 1,
            "cycles": [{
                "subject": "Science",
                "call_for_bids_url":
                    "https://www.sde.idaho.gov/im/call-for-bids-2026-science.pdf",
            }],
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

    # AL: existing specific PDF should be kept, no conflict logged.
    al = states_by_code["AL"]
    c = al["cycles"][0]
    if c["src"] != "https://www.alabamaachieves.org/wp-content/uploads/2026/02/TAP_20260227_BidPacket.pdf":
        _fail(f"AL cycle src should be unchanged PDF, got {c['src']}")
    al_conflicts = [x for x in conflicts if x["state"] == "AL"]
    if al_conflicts:
        _fail(f"AL should NOT have a conflict (existing PDF is more specific), got {al_conflicts}")
    _ok("AL: existing specific PDF preserved, no false-positive conflict")

    # ID: active cycle rule replaces landing page with bid packet URL.
    idaho = states_by_code["ID"]
    c = idaho["cycles"][0]
    if c["ac"] is not True:
        _fail(f"ID cycle ac should flip to True on cycle-level URL, got {c['ac']}")
    expected_id_src = "https://www.sde.idaho.gov/im/call-for-bids-2026-science.pdf"
    if c["src"] != expected_id_src:
        _fail(f"ID cycle src should be replaced with bid packet URL, got {c['src']}")
    id_conflicts = [x for x in conflicts if x["state"] == "ID"]
    if id_conflicts:
        _fail(f"ID should NOT have a conflict (rule 4b auto-applies), got {id_conflicts}")
    id_change = next((x for x in changes if x["state"] == "ID"), None)
    if not id_change:
        _fail("ID should appear in changes")
    if id_change["src_replaced_active"] != 1:
        _fail(f"ID src_replaced_active should be 1, got {id_change['src_replaced_active']}")
    if id_change["ac_flipped"] != 1:
        _fail(f"ID ac_flipped should be 1, got {id_change['ac_flipped']}")
    _ok("ID: active-cycle URL replaces landing page, ac flipped, no conflict")

    # Changes summary should have an entry for each state that actually moved.
    states_in_changes = {c["state"] for c in changes}
    expected = {"NC", "UT", "TX", "VA", "ID"}
    if not expected.issubset(states_in_changes):
        _fail(f"changes missing states: expected superset of {expected}, got {states_in_changes}")
    if "OK" in states_in_changes:
        _fail("OK should not appear in changes (no snapshot)")
    _ok("change summary lists only states that were updated")

    print("\nAll promote_scraped tests passed.")


if __name__ == "__main__":
    run()
