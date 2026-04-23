"""Smoke tests for the Mississippi adapter.

Runs without network. Fixture is a trimmed copy of the real MS IMM
adoption page covering the cycle heading, wrapper PDFs, publisher
info anchors, and the subject catalog nav.

Run:
    python3 scripts/adapters/test_ms.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from scripts.adapters import ms  # noqa: E402


FIXTURE_HTML = """
<html><body>
<nav>
  <ul>
    <li><a href="/">Home</a></li>
    <li><a href="/adopted-materials/">State-Adopted Materials</a>
      <ul>
        <li><a href="/adopted-materials/science-adopted-materials/">Science Adopted Materials</a></li>
        <li><a href="/adopted-materials/health-and-physical-education-adopted-materials/">Health and PE Adopted Materials</a></li>
        <li><a href="/adopted-materials/mathematics-adopted-materials/">Mathematics Adopted Materials</a></li>
        <li><a href="/adopted-materials/social-studies-adopted-materials/">Social Studies Adopted Materials</a></li>
        <li><a href="/adopted-materials/ela/">ELA Adopted Materials</a></li>
        <li><a href="/adopted-materials/career-technical-education-adopted-materials/">CTE Adopted Materials</a></li>
        <li><a href="/adopted-materials/adopted-materials-pre-kindergarten/">Pre-K Adopted Materials</a></li>
        <li><a href="/adopted-materials/world-language-adopted-materials/">World Language Adopted Materials</a></li>
        <li><a href="/adopted-materials/arts-adopted-materials/">Arts Adopted Materials</a></li>
        <li><a href="/adopted-materials/business-and-technology-adopted-materials/">Business and Technology Adopted Materials</a></li>
        <li><a href="/adopted-materials/computer-science-adopted-materials/">Computer Science Adopted Materials</a></li>
      </ul>
    </li>
  </ul>
</nav>
<main>
  <h1>High-Quality Instructional Materials Adoption</h1>
  <p>Funds allocated for the purchase of textbooks ...</p>

  <p><a href="/wp-content/uploads/2026/02/2023-2031-Textbook-Adoption-Schedule.pdf">Upcoming HQIM Adoption Schedules</a></p>
  <p><a href="/wp-content/uploads/2026/02/26-27-STRC-Job-Description.pdf">Rating Committee</a></p>

  <h2>25-26 Adoption Call for Bids</h2>
  <ul>
    <li><strong>Textbook Depository Contract and Inventory</strong>
      <ul><li>Inventory is due from publishers by 2:00 p.m. on Wednesday, April 1.</li></ul>
    </li>
  </ul>

  <h2>Publisher Information</h2>
  <ul>
    <li><a href="/wp-content/uploads/2025/01/2024-MDE-Textbook-Handbook.pdf">Textbook Administration Handbook</a> (2025 Version)</li>
    <li><a href="/wp-content/uploads/2025/05/Publisher-Representative.docx">Publisher Representative Information Form</a></li>
    <li>Substitution Form (Coming Soon)</li>
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
    data = ms.parse(FIXTURE_HTML)

    if data["state"] != "MS":
        _fail(f"expected state MS, got {data['state']}")
    if data["name"] != "Mississippi":
        _fail(f"expected name Mississippi, got {data['name']}")
    _ok("wrapper fields populated")

    if data["ay_start"] != 2025 or data["ay_end"] != 2026:
        _fail(f"AY bounds wrong: {data['ay_start']}-{data['ay_end']}")
    if not data["cycle_label"] or "25-26" not in data["cycle_label"]:
        _fail(f"cycle_label wrong: {data['cycle_label']}")
    _ok("cycle label and AY bounds parsed from 25-26 heading")

    if "Textbook-Adoption-Schedule" not in (data["adoption_schedule_url"] or ""):
        _fail(f"adoption_schedule_url wrong: {data['adoption_schedule_url']}")
    if "STRC-Job-Description" not in (data["rating_committee_url"] or ""):
        _fail(f"rating_committee_url wrong: {data['rating_committee_url']}")
    if "Textbook-Handbook" not in (data["textbook_handbook_url"] or ""):
        _fail(f"textbook_handbook_url wrong: {data['textbook_handbook_url']}")
    if "Publisher-Representative" not in (data["publisher_rep_form_url"] or ""):
        _fail(f"publisher_rep_form_url wrong: {data['publisher_rep_form_url']}")
    _ok("schedule, rating committee, handbook, and rep form URLs captured")

    # 11 subject nav entries.
    if data["cycle_count"] != 11:
        _fail(f"expected 11 subjects, got {data['cycle_count']}")
    _ok("11 subject catalog entries captured")

    by_subject = {c["subject"] for c in data["cycles"]}
    for expected in ("ELA", "Mathematics", "Science", "Pre-Kindergarten",
                     "Career and Technical Education",
                     "Health and Physical Education",
                     "Computer Science", "Arts", "Social Studies",
                     "Business and Technology", "World Language"):
        if expected not in by_subject:
            _fail(f"missing subject: {expected}. got {sorted(by_subject)}")
    _ok("subject names normalized through slug overrides")

    # Every cycle carries the AY bounds so coordinators can compare
    # across states with a single (state, subject, ay_start, ay_end) key.
    for c in data["cycles"]:
        if c["ay_start"] != 2025 or c["ay_end"] != 2026:
            _fail(f"subject {c['subject']} AY wrong: {c['ay_start']}-{c['ay_end']}")
    _ok("every subject record carries the active AY 2025-2026")

    print("\nAll Mississippi adapter tests passed. Output sample:")
    print(json.dumps(data, indent=2)[:600] + "...")


if __name__ == "__main__":
    run()
