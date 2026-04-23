"""Smoke tests for the Alabama adapter.

Runs without network. Fixture is a trimmed copy of the real Alabama SDE
"Textbook Adoption and Procurement" page covering two subjects:
Arts Education (approved list only) and Mathematics (both approved and
pending lists) plus the Adoption Process sub-block.

Run:
    python3 scripts/adapters/test_al.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from scripts.adapters import al  # noqa: E402


FIXTURE_HTML = """
<html><body>
<main>
  <h2>Textbook Adoption and Procurement</h2>
  <p>Intro paragraph with background on Alabama's adoption process.</p>

  <h3>Textbook by Subject - Arts Education</h3>
  <p><a href="/arts/approved-2025-2026.pdf">Alabama State Board Approved/Rejected Arts Education Textbooks and Materials 2025-2026</a></p>
  <p>The State Board of Education approved these materials at the meeting on May 8, 2025.</p>

  <h3>Textbook by Subject - Mathematics</h3>
  <p><a href="/math/approved-2024-2025.pdf">Alabama State Board Approved/Rejected Mathematics Textbooks and Materials 2024-2025</a></p>
  <p>Adopted by the State Board of Education at the June 13, 2024 meeting.</p>
  <p><a href="/math/pending-2025-2026.pdf">Mathematics Textbook and Supplemental Materials List Submitted for State Textbook Committee Review 2025-2026</a></p>
  <p>Tentative approval is scheduled for the State Board meeting on March 12, 2026.</p>

  <h3>Textbook by Subject - Health/PE</h3>
  <p>No current cycle information available at this time.</p>

  <h3>Adoption Process - Schedule</h3>
  <p><a href="/cycle/schedule.pdf">Alabama Courses of Study Standards and State Textbook Adoption Cycle</a></p>
  <p><a href="/forms/adoption-forms.pdf">Alabama State Textbooks Adoption Process Forms</a></p>
  <p><a href="/publishers/publisher-documents.pdf">Publisher's Documents</a></p>
</main>
</body></html>
"""


def _fail(msg):
    print(f"FAIL: {msg}")
    sys.exit(1)


def _ok(msg):
    print(f"ok  {msg}")


def run():
    data = al.parse(FIXTURE_HTML)

    if data["state"] != "AL":
        _fail(f"expected state AL, got {data['state']}")
    if data["name"] != "Alabama":
        _fail(f"expected name Alabama, got {data['name']}")
    _ok("wrapper fields populated")

    # Newest cycle year across all subjects. Mathematics pending is
    # 2025-2026, Arts is 2025-2026, so newest_ay_start is 2025.
    if data["cycle_year"] != 2025:
        _fail(f"expected cycle_year 2025, got {data['cycle_year']}")
    if data["cycle_label"] != "2025-2026 Adoption":
        _fail(f"cycle_label wrong: {data['cycle_label']}")
    _ok("newest cycle year stamped as 2025-2026")

    # Wrapper URLs below the subject blocks.
    if not data["adoption_cycle_schedule_url"] or "schedule" not in data["adoption_cycle_schedule_url"]:
        _fail(f"adoption_cycle_schedule_url wrong: {data['adoption_cycle_schedule_url']}")
    if not data["adoption_process_forms_url"] or "adoption-forms" not in data["adoption_process_forms_url"]:
        _fail(f"adoption_process_forms_url wrong: {data['adoption_process_forms_url']}")
    if not data["publishers_documents_url"] or "publisher-documents" not in data["publishers_documents_url"]:
        _fail(f"publishers_documents_url wrong: {data['publishers_documents_url']}")
    _ok("schedule, forms, and publisher documents URLs captured")

    # Health/PE is skipped because it has no trackable cycle, so we
    # expect exactly two cycles: Arts Education and Mathematics.
    if data["cycle_count"] != 2 or len(data["cycles"]) != 2:
        _fail(f"expected 2 cycles (Arts, Math), got {data['cycle_count']}")
    _ok("subjects with no trackable cycle are skipped")

    by_subject = {c["subject"]: c for c in data["cycles"]}

    # Arts Education: approved 2025-2026 only, no pending.
    arts = by_subject.get("Arts Education")
    if not arts:
        _fail("Arts Education cycle missing")
    if arts["ay_start"] != 2025 or arts["ay_end"] != 2026:
        _fail(f"Arts AY wrong: {arts['ay_start']}-{arts['ay_end']}")
    if "approved-2025-2026" not in (arts["approved_list_url"] or ""):
        _fail(f"Arts approved_list_url wrong: {arts['approved_list_url']}")
    if arts["pending_list_url"] is not None:
        _fail(f"Arts pending_list_url should be None, got {arts['pending_list_url']}")
    if arts["approved_board_meeting_date"] != "2025-05-08":
        _fail(f"Arts approved meeting date wrong: {arts['approved_board_meeting_date']}")
    _ok("Arts Education approved-only cycle parsed with board meeting date")

    # Mathematics: approved 2024-2025 AND pending 2025-2026.
    math = by_subject.get("Mathematics")
    if not math:
        _fail("Mathematics cycle missing")
    # Pending year is newer than approved, so the cycle stamp uses pending.
    if math["ay_start"] != 2025 or math["ay_end"] != 2026:
        _fail(f"Math AY wrong: {math['ay_start']}-{math['ay_end']}")
    if "approved-2024-2025" not in (math["approved_list_url"] or ""):
        _fail(f"Math approved_list_url wrong: {math['approved_list_url']}")
    if "pending-2025-2026" not in (math["pending_list_url"] or ""):
        _fail(f"Math pending_list_url wrong: {math['pending_list_url']}")
    if math["approved_board_meeting_date"] != "2024-06-13":
        _fail(f"Math approved meeting date wrong: {math['approved_board_meeting_date']}")
    if math["pending_board_meeting_date"] != "2026-03-12":
        _fail(f"Math pending meeting date wrong: {math['pending_board_meeting_date']}")
    _ok("Mathematics approved + pending cycle parsed with both meeting dates")

    print("\nAll Alabama adapter tests passed. Output sample:")
    print(json.dumps(data, indent=2)[:600] + "...")


if __name__ == "__main__":
    run()
