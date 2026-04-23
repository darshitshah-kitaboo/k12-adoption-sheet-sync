"""Smoke tests for the Oklahoma adapter.

Runs without network. Uses a trimmed fixture modeled on the real
oklahoma.gov "Information for Publishers" page.

Run:
    python3 scripts/adapters/test_ok.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from scripts.adapters import ok  # noqa: E402


# Trimmed copy of the real page layout. Keeps only the anchors the
# adapter cares about, plus enough narrative to trip the cycle year
# and STC calendar AY regexes.
FIXTURE_HTML = """
<html><body>
<nav aria-label="side">
  <ul>
    <li><a href="/education/services/hqim/stc.html">Oklahoma State Textbook Committee</a></li>
    <li><a href="/education/services/hqim/approved-titles.html">Approved Titles</a></li>
    <li><a href="/education/services/hqim/hqim-evaluation-rubrics.html">HQIM Evaluation Rubrics</a></li>
    <li><a href="/education/services/hqim/info-for-publishers.html">Information for Publishers</a></li>
    <li><a href="/education/services/hqim/hqim-review-process.html">HQIM Review Process</a></li>
  </ul>
</nav>
<main>
  <h1>Information for Publishers</h1>
  <h2>Overview</h2>
  <p>The High-Quality Instructional Material
  <a href="/content/dam/ok/en/osde/documents/services/standards-learning/hqim/information-for-publishers/HQIM%20Cycle%20Graphic.pdf">cycle graphic</a>
  outlines Oklahoma's annual HQIM cycle from March through the following year.</p>
  <p><a href="https://airtable.com/appsLAsi2GHi0cwAb/pagKx6TVlJJNN5kjL/form">Publisher State Registration Form</a></p>

  <h2>Intent To Bid</h2>
  <p>publishers should complete and upload the
  <a href="/content/dam/ok/en/osde/documents/services/standards-learning/hqim/information-for-publishers/Data%20Privacy%20and%20Integration%20Attestation%202026.pdf">Data Privacy and Integration Attestation Form</a>.</p>
  <p>See the <a href="/content/dam/ok/en/osde/documents/services/standards-learning/hqim/information-for-publishers/Out%20of%20Cycle%20Flyer%202025.pdf">Out-of-Cycle document</a> for more information.</p>

  <h2>Supplemental Submissions</h2>
  <p>Publishers submitting exclusively supplemental materials for the 2026 Adoption Cycle may submit using this
  <a href="https://airtable.com/appTPfE04LpvqaPrK/paghz5HkpyPCYTpvJ/form">form</a>.</p>

  <h2>Substitution Bid</h2>
  <p>review the
  <a href="/content/dam/ok/en/osde/documents/services/standards-learning/hqim/information-for-publishers/Substitution%20Bid%202025.pdf">Substitution Bid Flyer</a>
  and
  <a href="/content/dam/ok/en/osde/documents/services/standards-learning/hqim/information-for-publishers/Publisher%20Updates%20During%20Contracted%20Adoption%20Period.pdf">Substitution Guidance Document</a>.</p>
  <p><a href="/content/dam/ok/en/osde/documents/services/standards-learning/hqim/information-for-publishers/Intent%20to%20Substitute%20Memo%202025.pdf">Substitution Bid Memorandum</a></p>

  <h2>State Textbook Committee Calendars</h2>
  <ul>
    <li><a href="/content/dam/ok/en/osde/documents/services/standards-learning/hqim/Amended%20STC%20Calendar%202026.pdf">State Textbook Committee Calendar 2026-2027 (amended March 2026)</a></li>
    <li><a href="/content/dam/ok/en/osde/documents/services/standards-learning/hqim/Amended%20Subject%20Material%20Adoption%20Cycle%20Calendar%202026.pdf">Adoption Subject Cycle Calendar 2026 (amended March 2026)</a></li>
  </ul>

  <h2>Other Useful Information and Links</h2>
  <p>Oklahoma Subject Codes</p>
  <ul>
    <li><a href="/content/dam/ok/en/osde/documents/services/standards-learning/hqim/information-for-publishers/2025-2026%20PK-8%20Subject%20Codes.pdf">PK-8th subject codes</a></li>
    <li><a href="/content/dam/ok/en/osde/documents/services/standards-learning/hqim/information-for-publishers/2025-2026%209-12%20Subject%20Codes.pdf">9th-12th subject codes</a></li>
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
    data = ok.parse(FIXTURE_HTML)

    if data["state"] != "OK":
        _fail(f"expected state OK, got {data['state']}")
    if data["name"] != "Oklahoma":
        _fail(f"expected name Oklahoma, got {data['name']}")
    _ok("wrapper fields populated")

    if data["cycle_year"] != 2026:
        _fail(f"expected cycle_year 2026, got {data['cycle_year']}")
    if data["cycle_label"] != "2026-2027 STC Adoption Cycle":
        _fail(f"cycle_label wrong: {data['cycle_label']}")
    _ok("cycle year 2026 and AY 2026-2027 parsed from narrative")

    # Wrapper URLs from side nav and Other Useful Information.
    if not data["evaluation_rubrics_url"] or "evaluation-rubrics" not in data["evaluation_rubrics_url"]:
        _fail(f"evaluation_rubrics_url wrong: {data['evaluation_rubrics_url']}")
    if not data["approved_titles_url"] or "approved-titles" not in data["approved_titles_url"]:
        _fail(f"approved_titles_url wrong: {data['approved_titles_url']}")
    if not data["review_process_url"] or "review-process" not in data["review_process_url"]:
        _fail(f"review_process_url wrong: {data['review_process_url']}")
    _ok("side-nav wrapper URLs captured: rubrics, approved titles, review process")

    if not data["publisher_registration_form_url"] or "airtable" not in data["publisher_registration_form_url"].lower():
        _fail(f"publisher_registration_form_url wrong: {data['publisher_registration_form_url']}")
    if not data["hqim_cycle_graphic_url"] or "HQIM%20Cycle%20Graphic" not in data["hqim_cycle_graphic_url"]:
        _fail(f"hqim_cycle_graphic_url wrong: {data['hqim_cycle_graphic_url']}")
    if not data["subject_codes_pk8_url"] or "PK-8" not in data["subject_codes_pk8_url"]:
        _fail(f"subject_codes_pk8_url wrong: {data['subject_codes_pk8_url']}")
    if not data["subject_codes_9_12_url"] or "9-12" not in data["subject_codes_9_12_url"]:
        _fail(f"subject_codes_9_12_url wrong: {data['subject_codes_9_12_url']}")
    _ok("publisher registration, cycle graphic, and subject code PDFs captured")

    # One cycle record with all cycle-scoped artifacts.
    if data["cycle_count"] != 1 or len(data["cycles"]) != 1:
        _fail(f"expected 1 cycle, got {data['cycle_count']}")
    cycle = data["cycles"][0]

    if cycle["ay_start"] != 2026 or cycle["ay_end"] != 2027:
        _fail(f"AY bounds wrong: {cycle['ay_start']}-{cycle['ay_end']}")
    if cycle["subject"] != "All subjects per Adoption Subject Cycle Calendar":
        _fail(f"subject wrong: {cycle['subject']}")
    _ok("cycle record AY bounds and subject pointer set")

    if "STC%20Calendar%202026" not in (cycle["stc_calendar_url"] or ""):
        _fail(f"stc_calendar_url wrong: {cycle['stc_calendar_url']}")
    if "Subject%20Material%20Adoption%20Cycle%20Calendar%202026" not in (cycle["subject_cycle_calendar_url"] or ""):
        _fail(f"subject_cycle_calendar_url wrong: {cycle['subject_cycle_calendar_url']}")
    _ok("STC calendar and Adoption Subject Cycle Calendar PDFs captured")

    if "Data%20Privacy" not in (cycle["data_privacy_form_url"] or ""):
        _fail(f"data_privacy_form_url wrong: {cycle['data_privacy_form_url']}")
    if "Out%20of%20Cycle" not in (cycle["out_of_cycle_flyer_url"] or ""):
        _fail(f"out_of_cycle_flyer_url wrong: {cycle['out_of_cycle_flyer_url']}")
    if "airtable" not in (cycle["supplemental_form_url"] or "").lower():
        _fail(f"supplemental_form_url wrong: {cycle['supplemental_form_url']}")
    _ok("intent-to-bid, out-of-cycle, and supplemental form URLs captured")

    if "Intent%20to%20Substitute%20Memo" not in (cycle["substitution_memo_url"] or ""):
        _fail(f"substitution_memo_url wrong: {cycle['substitution_memo_url']}")
    if "Substitution%20Bid" not in (cycle["substitution_flyer_url"] or ""):
        _fail(f"substitution_flyer_url wrong: {cycle['substitution_flyer_url']}")
    if "Publisher%20Updates%20During%20Contracted" not in (cycle["substitution_guidance_url"] or ""):
        _fail(f"substitution_guidance_url wrong: {cycle['substitution_guidance_url']}")
    _ok("substitution memo, flyer, and guidance URLs captured")

    print("\nAll Oklahoma adapter tests passed. Output sample:")
    print(json.dumps(data, indent=2)[:600] + "...")


if __name__ == "__main__":
    run()
