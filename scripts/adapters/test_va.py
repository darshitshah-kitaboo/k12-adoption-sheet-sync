"""Smoke tests for the Virginia adapter.

Runs without network. Fixture is a trimmed copy of the real VDOE
textbooks page covering the News & Announcements block, the Textbook
Review & Approval section with wrapper links, and the Approved
Textbooks & Materials bullet list.

Run:
    python3 scripts/adapters/test_va.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from scripts.adapters import va  # noqa: E402


FIXTURE_HTML = """
<html><body>
<main>
  <h1>Textbooks &amp; Instructional Materials</h1>

  <h2>News &amp; Announcements</h2>
  <h3>2025 Mathematics Textbook and Instructional Materials Review Process</h3>
  <p><strong>March 27, 2025</strong> - At its March 2025 meeting, the Virginia Board of Education approved a list of additional mathematics textbooks and instructional materials. All documents related to approved textbooks and instructional materials can be found on the <a href="/teaching-learning-assessment/k-12-standards-instruction/mathematics/2024-mathematics-textbooks">Mathematics Textbooks and Instructional Resources</a> web page.</p>
  <p><strong>February 27, 2025</strong> - At its February 2025 meeting, the Virginia Board of Education approved a list of mathematics textbooks and instructional materials.</p>

  <h2>Textbook Review &amp; Approval</h2>
  <p>The Board of Education has the responsibility ...</p>
  <ul>
    <li><a href="/teaching-learning-assessment/instructional-resources-support/textbooks-instructional-materials/procurement-pricing">Procurement &amp; Pricing</a></li>
    <li><a href="/teaching-learning-assessment/instructional-resources-support/textbooks-instructional-materials/textbook-review-approval">Textbook Review &amp; Approval Process</a></li>
    <li><a href="/teaching-learning-assessment/instructional-resources-support/textbooks-instructional-materials/textbook-review-approval/location-of-public-review-sites">Location of Public Review Sites</a></li>
  </ul>

  <h2>Instructional Materials and the Standards of Learning</h2>
  <h3>Approved Textbooks &amp; Materials</h3>
  <ul>
    <li><a href="/teaching-learning-assessment/k-12-standards-instruction/english-reading-literacy/english-textbooks">English Reading, Language Arts, Literature</a></li>
    <li><a href="/teaching-learning-assessment/k-12-standards-instruction/history-and-social-science/history-social-science-textbooks">History &amp; Social Science</a></li>
    <li><a href="/teaching-learning-assessment/k-12-standards-instruction/mathematics/2024-mathematics-textbooks">Mathematics</a></li>
    <li><a href="/teaching-learning-assessment/k-12-standards-instruction/science/science-textbooks">Science</a></li>
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
    data = va.parse(FIXTURE_HTML)

    if data["state"] != "VA":
        _fail(f"expected state VA, got {data['state']}")
    if data["name"] != "Virginia":
        _fail(f"expected name Virginia, got {data['name']}")
    _ok("wrapper fields populated")

    if "procurement-pricing" not in (data["procurement_pricing_url"] or ""):
        _fail(f"procurement_pricing_url wrong: {data['procurement_pricing_url']}")
    if "textbook-review-approval" not in (data["review_approval_process_url"] or ""):
        _fail(f"review_approval_process_url wrong: {data['review_approval_process_url']}")
    if "location-of-public-review-sites" not in (data["review_sites_url"] or ""):
        _fail(f"review_sites_url wrong: {data['review_sites_url']}")
    _ok("procurement, review process, and review sites URLs captured")

    # Announcement parsed: Mathematics review, latest date March 27 2025.
    if data["latest_announcement_subject"] != "Mathematics":
        _fail(f"latest_announcement_subject wrong: {data['latest_announcement_subject']}")
    if data["latest_announcement_date"] != "2025-03-27":
        _fail(f"latest_announcement_date wrong: {data['latest_announcement_date']}")
    if "Mathematics" not in (data["latest_announcement_title"] or ""):
        _fail(f"latest_announcement_title wrong: {data['latest_announcement_title']}")
    _ok("news announcement subject, date, and title pulled from h3 block")

    # Four subjects in Approved Textbooks & Materials.
    if data["cycle_count"] != 4:
        subjects = [c["subject"] for c in data["cycles"]]
        _fail(f"expected 4 subjects, got {data['cycle_count']}: {subjects}")
    _ok("four approved-subject rows emitted")

    by_subject = {c["subject"]: c for c in data["cycles"]}
    for name in ("English", "History & Social Science", "Mathematics", "Science"):
        if name not in by_subject:
            _fail(f"missing subject: {name}. got {sorted(by_subject)}")
    _ok("all four subject families matched into approved rows")

    # Math row should carry the active review info; others should not.
    math = by_subject["Mathematics"]
    if math["current_review_date"] != "2025-03-27":
        _fail(f"math current_review_date wrong: {math['current_review_date']}")
    if "Mathematics" not in (math["current_review_title"] or ""):
        _fail(f"math current_review_title wrong: {math['current_review_title']}")
    if "2024-mathematics-textbooks" not in (math["approved_materials_url"] or ""):
        _fail(f"math approved_materials_url wrong: {math['approved_materials_url']}")
    _ok("active review metadata attached to Mathematics subject")

    for other in ("English", "History & Social Science", "Science"):
        row = by_subject[other]
        if row["current_review_date"] is not None:
            _fail(f"{other} should have no current_review_date")
        if row["current_review_title"] is not None:
            _fail(f"{other} should have no current_review_title")
    _ok("non-matching subjects carry no current review metadata")

    print("\nAll Virginia adapter tests passed. Output sample:")
    print(json.dumps(data, indent=2)[:600] + "...")


if __name__ == "__main__":
    run()
