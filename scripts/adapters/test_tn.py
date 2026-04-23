"""Smoke tests for the Tennessee adapter.

Runs without network. Uses a stripped-down version of the real
Tennessee Publisher Information page HTML (AEM-rendered, article tag
wraps the meaningful content, a side nav lists related pages).

Run:
    python3 scripts/adapters/test_tn.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from scripts.adapters import tn  # noqa: E402


# Trimmed copy of the real page structure. Only the bits the adapter
# cares about are preserved: the article body, the "Cycle 2027" and
# deadline language, the template and rule links, the publisher
# distribution form link, and the side nav links for Schedule F /
# official lists / adoption process.
FIXTURE_HTML = """
<html><body>
<nav aria-label="Left Navigation">
  <ul>
    <li><a href="/textbook-commission/textbook-adoption-process.html">Adoption Process &amp; Timeline</a></li>
    <li><a href="/textbook-commission/textbook-reviews.html">Official Lists of Textbooks and Instructional Materials</a></li>
    <li><a href="/textbook-commission/schedule-f-textbook-adoption-cycle.html">Schedule F Textbook Adoption Cycle</a></li>
  </ul>
</nav>
<article>
  <h1>Publisher Information</h1>
  <h2>General Information for All Adoption Cycles</h2>
  <ul>
    <li><a href="/content/tn/education/districts/textbook-services/textbook-laws-rules-and-policies.html">Tennessee State Textbook Laws, Rules, and Policies</a></li>
  </ul>
  <p><strong>If your company is interested in receiving information regarding textbooks and instructional materials, please complete
  <a href="/content/dam/tn/education/textbook/txtbk_publisher_distr_list_info.xlsx">this form</a> and you will be added to the distribution list.</strong></p>

  <h3>Presentations</h3>
  <h4>Textbook Substitution Guidelines and Directions</h4>
  <p><strong>Cycle 2027 for March 2027 Textbook Commission Meeting (Deadline for Submission via email to
  <a href="mailto:Tennessee.Textbooks@tn.gov">Tennessee.Textbooks@tn.gov</a> is December 31, 2026)</strong></p>
  <p>At the first regular meeting of each calendar year, the Commission will consider substitutions.
  For more details, please refer to <a href="https://publications.tnsosfiles.com/rules/0520/0520-05/0520-05-01.20160512.pdf">Commission Rule 0520-05-01-07</a>.</p>
  <p><strong>Step One:</strong></p>
  <p>Complete the <a href="/content/dam/tn/education/textbook/Textbook%20Substitutions%20Template.xlsx">Textbook Substitutions Template</a>,
  and email the document as an attachment to <a href="mailto:Tennessee.Textbooks@tn.gov">Tennessee.Textbooks@tn.gov</a>.</p>
</article>
</body></html>
"""


def _fail(msg):
    print(f"FAIL: {msg}")
    sys.exit(1)


def _ok(msg):
    print(f"ok  {msg}")


def run():
    data = tn.parse(FIXTURE_HTML)

    if data["state"] != "TN":
        _fail(f"expected state TN, got {data['state']}")
    if data["name"] != "Tennessee":
        _fail(f"expected name Tennessee, got {data['name']}")
    _ok("wrapper fields populated")

    # Cycle year.
    if data["cycle_year"] != 2027:
        _fail(f"expected cycle_year 2027, got {data['cycle_year']}")
    if data["cycle_label"] != "Cycle 2027 Substitution Window":
        _fail(f"cycle_label wrong: {data['cycle_label']}")
    _ok("cycle year and label parsed from 'Cycle 2027 for March 2027' language")

    # Navigation links (wrapper level).
    if not data["schedule_f_url"] or "schedule-f" not in data["schedule_f_url"].lower():
        _fail(f"schedule_f_url missing: {data['schedule_f_url']}")
    if not data["official_list_url"] or "textbook-reviews" not in data["official_list_url"]:
        _fail(f"official_list_url missing: {data['official_list_url']}")
    if not data["adoption_process_url"]:
        _fail(f"adoption_process_url missing: {data['adoption_process_url']}")
    _ok("nav links captured: schedule-f, official list, adoption process")

    # Cycle record.
    if data["cycle_count"] != 1 or len(data["cycles"]) != 1:
        _fail(f"expected 1 cycle, got {data['cycle_count']}")
    cycle = data["cycles"][0]

    if cycle["ay_start"] != 2026 or cycle["ay_end"] != 2027:
        _fail(f"AY bounds wrong: {cycle['ay_start']}-{cycle['ay_end']}")
    if cycle["commission_meeting"] != "March 2027":
        _fail(f"commission_meeting wrong: {cycle['commission_meeting']}")
    if cycle["submission_deadline"] != "2026-12-31":
        _fail(f"submission_deadline wrong: {cycle['submission_deadline']}")
    _ok("commission meeting, AY bounds, and submission deadline parsed")

    if "Substitutions%20Template" not in (cycle["substitution_template_url"] or ""):
        _fail(f"substitution template URL wrong: {cycle['substitution_template_url']}")
    if "0520-05-01" not in (cycle["substitution_rule_url"] or ""):
        _fail(f"substitution rule URL wrong: {cycle['substitution_rule_url']}")
    if "txtbk_publisher_distr_list_info" not in (cycle["publisher_distr_list_url"] or ""):
        _fail(f"publisher distribution list URL wrong: {cycle['publisher_distr_list_url']}")
    _ok("substitution template, commission rule, and distribution list URLs captured")

    print("\nAll Tennessee adapter tests passed. Output sample:")
    print(json.dumps(data, indent=2)[:600] + "...")


if __name__ == "__main__":
    run()
