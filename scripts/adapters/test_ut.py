"""Smoke tests for the Utah adapter.

Runs without network. Uses a trimmed fixture modeled on the Utah USBE
IMC page. Two scenarios:
  1. Active math review window advertised as "2026-2027 Mathematics
     Review" in an h3 heading.
  2. Subject-only heading with no year inline; the year appears in the
     following paragraph. Verifies the fallback path.

Run:
    python3 scripts/adapters/test_ut.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from scripts.adapters import ut  # noqa: E402


FIXTURE_INLINE = """
<html><body>
<main>
  <h1>Instructional Materials Commission</h1>
  <p>The Utah Instructional Materials Commission (IMC) reviews materials
  by subject on a rolling schedule.</p>

  <h3>2026-2027 Mathematics Review</h3>
  <p>The next math adoption cycle opens July 2026 with committee review
  through December. Aligned to Utah Core Standards.</p>

  <ul>
    <li><a href="/curr/imc/review-process">IMC Review Process</a></li>
    <li><a href="/curr/core-standards">Utah Core Standards</a></li>
    <li><a href="/curr/imc/recommended-instructional-materials">Recommended Instructional Materials</a></li>
    <li><a href="/curr/imc/imc-calendar">IMC Calendar</a></li>
    <li><a href="/curr/imc/publisher-submission-guidelines">Publisher Submission Guidelines</a></li>
    <li><a href="/curr/imc/2026-2027-review-schedule.pdf">2026-2027 Review Schedule (PDF)</a></li>
  </ul>
</main>
</body></html>
"""

FIXTURE_PARA_YEAR = """
<html><body>
<main>
  <h1>Instructional Materials Commission</h1>

  <h3>Mathematics</h3>
  <p>The IMC will open the 2027 Mathematics Review window in July 2026.
  Materials will be recommended to the State Board in late 2026.</p>

  <ul>
    <li><a href="/curr/imc/review-process">How the IMC works</a></li>
    <li><a href="/curr/imc/recommended-materials">Recommended Materials</a></li>
    <li><a href="/curr/imc/calendar">IMC Meeting Calendar</a></li>
    <li><a href="/curr/imc/publisher-info">Publisher Information and Submit Materials</a></li>
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
    # ----- Scenario 1: inline year on subject heading -----
    data = ut.parse(FIXTURE_INLINE)

    if data["state"] != "UT":
        _fail(f"expected state UT, got {data['state']}")
    if data["name"] != "Utah":
        _fail(f"expected name Utah, got {data['name']}")
    _ok("wrapper fields populated")

    if not data["has_active_review"]:
        _fail("has_active_review should be True")
    if data["current_subject"] != "Mathematics":
        _fail(f"current_subject wrong: {data['current_subject']}")
    _ok("active math review detected from h3 heading")

    if not data["review_process_url"] or "review-process" not in data["review_process_url"]:
        _fail(f"review_process_url wrong: {data['review_process_url']}")
    if not data["core_standards_url"] or "core-standards" not in data["core_standards_url"]:
        _fail(f"core_standards_url wrong: {data['core_standards_url']}")
    if not data["recommended_materials_page_url"] or "recommended" not in data["recommended_materials_page_url"]:
        _fail(f"recommended_materials_page_url wrong: {data['recommended_materials_page_url']}")
    if not data["imc_calendar_url"] or "calendar" not in data["imc_calendar_url"]:
        _fail(f"imc_calendar_url wrong: {data['imc_calendar_url']}")
    if not data["publisher_submission_url"] or "publisher" not in data["publisher_submission_url"]:
        _fail(f"publisher_submission_url wrong: {data['publisher_submission_url']}")
    _ok("wrapper URLs captured: process, standards, recommended, calendar, publisher")

    if data["cycle_count"] != 1:
        _fail(f"expected 1 cycle, got {data['cycle_count']}")
    cycle = data["cycles"][0]
    if cycle["ay_start"] != 2026 or cycle["ay_end"] != 2027:
        _fail(f"AY wrong: {cycle['ay_start']}-{cycle['ay_end']}")
    if cycle["cycle_label"] != "2026-2027 Mathematics Review":
        _fail(f"cycle_label wrong: {cycle['cycle_label']}")
    if not cycle["review_schedule_url"] or "review-schedule" not in cycle["review_schedule_url"]:
        _fail(f"review_schedule_url wrong: {cycle['review_schedule_url']}")
    _ok("inline cycle: AY 2026-2027 Mathematics Review, schedule PDF captured")

    # ----- Scenario 2: year lives in the paragraph below the subject heading -----
    data2 = ut.parse(FIXTURE_PARA_YEAR)

    if data2["current_subject"] != "Mathematics":
        _fail(f"para scenario current_subject wrong: {data2['current_subject']}")
    c2 = data2["cycles"][0]
    if c2["ay_start"] != 2027 or c2["ay_end"] != 2028:
        _fail(f"para scenario AY wrong: {c2['ay_start']}-{c2['ay_end']}")
    _ok("paragraph-year fallback works: subject heading + year-in-next-paragraph parsed")

    print("\nAll Utah adapter tests passed. Inline output sample:")
    print(json.dumps(data, indent=2)[:600] + "...")


if __name__ == "__main__":
    run()
