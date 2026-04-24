"""Smoke tests for the North Carolina adapter.

Runs without network. Uses a trimmed fixture modeled on dpi.nc.gov's
textbook adoption page. Two scenarios covered:
  1. No active cycle (current state as of 2026-04-23) with 2026 ELA SCoS
     adopted and implementation scheduled for 2027-28.
  2. Active cycle fixture with an Invitation to Submit anchor to verify
     has_active_cycle flips correctly.

Run:
    python3 scripts/adapters/test_nc.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from scripts.adapters import nc  # noqa: E402


# Scenario 1: Monitoring status. No active Call for Bids. Page mentions
# the 2026 ELA Standard Course of Study with a 2027-28 implementation.
FIXTURE_MONITORING = """
<html><body>
<main>
  <h1>Textbook Adoption</h1>
  <p>The State Board of Education adopted the 2026 ELA Standard Course of
  Study on January 8, 2026. Implementation begins in 2027-28.</p>
  <ul>
    <li><a href="/districts-schools/district-operations/textbook-adoption/textbook-commission">NC Textbook Commission</a></li>
    <li><a href="/districts-schools/classroom-resources/office-teaching-and-learning">Office of Teaching and Learning</a></li>
    <li><a href="/publishers-registry">Publishers Registry</a></li>
    <li><a href="/ela-standards/ela-standard-course-of-study.pdf">2026 ELA Standard Course of Study</a></li>
    <li><a href="/textbook-evaluation-criteria.pdf">Textbook Evaluation Criteria</a></li>
  </ul>
</main>
</body></html>
"""

# Scenario 2: Active cycle with an Invitation to Submit posted.
FIXTURE_ACTIVE = """
<html><body>
<main>
  <h1>Textbook Adoption</h1>
  <p>The NC Textbook Commission has opened the 2027 ELA adoption cycle.</p>
  <ul>
    <li><a href="/districts-schools/district-operations/textbook-adoption/textbook-commission">NC Textbook Commission</a></li>
    <li><a href="/ela-2026/invitation-to-submit.pdf">Invitation to Submit 2027 ELA</a></li>
    <li><a href="/publishers-registry">Publishers Registry</a></li>
    <li><a href="/2026-ela-standard-course-of-study.pdf">2026 ELA Standard Course of Study</a></li>
  </ul>
</main>
</body></html>
"""


def _fail(msg):
    print(f"FAIL: {msg}")
    sys.exit(1)


def _ok(msg):
    print(f"ok  {msg}")


def run():
    # ----- Scenario 1: monitoring -----
    data = nc.parse(FIXTURE_MONITORING)

    if data["state"] != "NC":
        _fail(f"expected state NC, got {data['state']}")
    if data["name"] != "North Carolina":
        _fail(f"expected name North Carolina, got {data['name']}")
    _ok("wrapper fields populated")

    if data["has_active_cycle"]:
        _fail("has_active_cycle should be False in monitoring fixture")
    _ok("monitoring fixture: has_active_cycle is False")

    if data["ela_standards_year"] != 2026:
        _fail(f"expected ela_standards_year 2026, got {data['ela_standards_year']}")
    _ok("2026 ELA Standard Course of Study year extracted from page text")

    if not data["textbook_commission_url"] or "textbook-commission" not in data["textbook_commission_url"]:
        _fail(f"textbook_commission_url wrong: {data['textbook_commission_url']}")
    if not data["publishers_registry_url"] or "publishers-registry" not in data["publishers_registry_url"]:
        _fail(f"publishers_registry_url wrong: {data['publishers_registry_url']}")
    if not data["ela_standards_url"] or "ela" not in data["ela_standards_url"].lower():
        _fail(f"ela_standards_url wrong: {data['ela_standards_url']}")
    if not data["office_teaching_learning_url"] or "office-teaching-and-learning" not in data["office_teaching_learning_url"]:
        _fail(f"office_teaching_learning_url wrong: {data['office_teaching_learning_url']}")
    _ok("wrapper URLs captured: commission, registry, ELA SCoS, office page")

    if data["cycle_count"] != 1 or len(data["cycles"]) != 1:
        _fail(f"expected 1 cycle, got {data['cycle_count']}")
    cycle = data["cycles"][0]
    if cycle["ay_start"] != 2027 or cycle["ay_end"] != 2028:
        _fail(f"AY wrong: {cycle['ay_start']}-{cycle['ay_end']}")
    if cycle["subject"] != "ELA Standards Revision":
        _fail(f"subject wrong: {cycle['subject']}")
    if cycle["invitation_to_submit_url"] is not None:
        _fail(f"invitation_to_submit_url should be None, got {cycle['invitation_to_submit_url']}")
    if cycle["call_for_bids_url"] is not None:
        _fail(f"call_for_bids_url should be None, got {cycle['call_for_bids_url']}")
    if not cycle["evaluation_criteria_url"] or "evaluation-criteria" not in cycle["evaluation_criteria_url"]:
        _fail(f"evaluation_criteria_url wrong: {cycle['evaluation_criteria_url']}")
    _ok("monitoring cycle: AY 2027-2028, ELA Standards Revision, no Call for Bids")

    # ----- Scenario 2: active cycle -----
    data2 = nc.parse(FIXTURE_ACTIVE)

    if not data2["has_active_cycle"]:
        _fail("has_active_cycle should be True in active fixture")
    _ok("active fixture: has_active_cycle flips to True")

    c2 = data2["cycles"][0]
    if not c2["invitation_to_submit_url"] or "invitation-to-submit" not in c2["invitation_to_submit_url"]:
        _fail(f"active: invitation_to_submit_url wrong: {c2['invitation_to_submit_url']}")
    if "Implementation" not in (c2["cycle_label"] or ""):
        _fail(f"active: cycle_label should say Implementation, got {c2['cycle_label']}")
    _ok("active cycle: invitation URL captured and label flipped")

    print("\nAll North Carolina adapter tests passed. Monitoring output sample:")
    print(json.dumps(data, indent=2)[:600] + "...")


if __name__ == "__main__":
    run()
