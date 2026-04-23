"""Smoke tests for the Florida adapter.

Runs without network. Uses hand-written HTML that mirrors the actual FLDOE
page structure. If FLDOE redesigns their page and the parser stops working,
this test will keep passing (it is pinned to the current shape), but the
live adapter will produce empty cycles, which the coordinator will flag.

Run:
    python3 scripts/adapters/test_fl.py
"""

import json
import sys
from pathlib import Path

# Allow running from repo root or from scripts/adapters directly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from scripts.adapters import fl  # noqa: E402


# Minimal HTML mirroring the real FLDOE structure. Covers three shapes:
#   - Normal year with multiple subjects in h3 sections.
#   - Older year with a single inline subject in the h2.
#   - A year section that has no subjects yet (early publication).
FIXTURE_HTML = """
<html><body>
<h2>2027-2028 Adoption Year</h2>
<ul>
  <li><a href="/file/5574/2728pubtimelinechecklist.pdf">2027-2028 Publisher Timeline and Checklist</a> (PDF)</li>
</ul>
<h3>K-12 Social Studies</h3>
<ul>
  <li><a href="/file/5574/2728K12SocialStudiesSpecsFINAL-1.pdf">2027-2028 K-12 Social Studies Specifications</a> (PDF)</li>
</ul>

<h2>2026-2027 Adoption Year</h2>
<ul>
  <li><a href="/file/5574/2627pubtimelinechecklist.pdf">2026-2027 Publisher Timeline and Checklist</a> (PDF)</li>
  <li><a href="/file/5574/2627DetailedBidReport.pdf">2026-2027 Detailed Bid Report</a> (PDF)</li>
</ul>
<h3>K-12 Mathematics</h3>
<ul>
  <li><a href="/file/5574/2627SBL-K-12-Math.pdf">2026-2027 Short Bid Report</a> (PDF). 198 bids submitted for review.</li>
  <li><a href="/file/5574/2627K12MathSpecsFINAL.pdf">2026-2027 K-12 Mathematics Specifications</a> (PDF)</li>
  <li><a href="/file/5574/PCL-2627-Math.pdf">K-12 Mathematics Publisher Contact List</a> (PDF)</li>
</ul>
<h3>K-12 Computer Science</h3>
<ul>
  <li><a href="/file/5574/2627SBL-K-12-CompSci.pdf">2026-2027 Short Bid Report</a> (PDF) 77 bids submitted for review.</li>
  <li><a href="/file/5574/2627K12CompSciSpecsFINAL.pdf">2026-2027 K-12 Computer Science Specifications</a> (PDF)</li>
</ul>

<h2>2023-2024 Adoption Year: K-12 Science</h2>
<ul>
  <li><a href="/file/5574/K12Science-2324-ShortBidList.pdf">2023-2024 Science Short Bid Report</a> (PDF) 146 bids submitted for review.</li>
  <li><a href="/file/5574/2324ScienceSpecs.pdf">2023-2024 K-12 Science Specifications</a> (PDF)</li>
  <li><a href="/file/5574/2324-Sci-IM-AdoptionList-102425.pdf">2023-2024 Science Instructional Materials Adoption List - Updated 10/24/2025</a> (PDF)</li>
  <li><a href="/file/5574/2324-Sci-IM-AdoptionList-121224.pdf">2023-2024 Science Instructional Materials Adoption List - Updated 12/12/2024</a> (PDF)</li>
</ul>
</body></html>
"""


def _fail(msg):
    print(f"FAIL: {msg}")
    sys.exit(1)


def _ok(msg):
    print(f"ok  {msg}")


def run():
    data = fl.parse(FIXTURE_HTML)

    # Sanity on the wrapper fields.
    if data["state"] != "FL":
        _fail(f"expected state FL, got {data['state']}")
    if data["name"] != "Florida":
        _fail(f"expected name Florida, got {data['name']}")
    _ok("wrapper fields populated")

    cycles = data["cycles"]
    if data["cycle_count"] != len(cycles):
        _fail(f"cycle_count mismatch: {data['cycle_count']} vs {len(cycles)}")

    # Expected counts: 1 Social Studies, 2 for 2026-27, 1 inline 2023-24 Science = 4.
    if len(cycles) != 4:
        _fail(f"expected 4 cycles, got {len(cycles)}: {[c['subject'] for c in cycles]}")
    _ok(f"parsed {len(cycles)} cycles")

    by_subject = {c["subject"]: c for c in cycles}

    # K-12 Mathematics: bid count 198, most recent list is None (fixture has no
    # adoption lists under this subject).
    math = by_subject.get("K-12 Mathematics")
    if not math:
        _fail("K-12 Mathematics cycle missing")
    if math["bid_count"] != 198:
        _fail(f"Math bid_count expected 198, got {math['bid_count']}")
    if math["ay_start"] != 2026 or math["ay_end"] != 2027:
        _fail(f"Math year range wrong: {math['ay_start']}-{math['ay_end']}")
    if "2627K12MathSpecsFINAL.pdf" not in (math["specifications_url"] or ""):
        _fail(f"Math specs URL wrong: {math['specifications_url']}")
    _ok("K-12 Mathematics: bid count, year range, specs URL")

    # K-12 Computer Science: bid count 77, year 2026-27.
    cs = by_subject.get("K-12 Computer Science")
    if not cs:
        _fail("K-12 Computer Science cycle missing")
    if cs["bid_count"] != 77:
        _fail(f"CS bid_count expected 77, got {cs['bid_count']}")
    _ok("K-12 Computer Science: bid count")

    # Science inline year: bid count 146, latest list date 2025-10-24, list URL
    # pointing at the 10/24/2025 PDF.
    sci = by_subject.get("K-12 Science")
    if not sci:
        _fail(f"K-12 Science cycle missing. Have: {list(by_subject)}")
    if sci["bid_count"] != 146:
        _fail(f"Science bid_count expected 146, got {sci['bid_count']}")
    if sci["latest_list_date"] != "2025-10-24":
        _fail(f"Science latest_list_date expected 2025-10-24, got {sci['latest_list_date']}")
    if "102425" not in (sci["latest_list_url"] or ""):
        _fail(f"Science latest_list_url does not point to the 10/24 PDF: {sci['latest_list_url']}")
    if sci["ay_start"] != 2023 or sci["ay_end"] != 2024:
        _fail(f"Science year range wrong: {sci['ay_start']}-{sci['ay_end']}")
    _ok("K-12 Science: inline-subject form parsed, latest list wins")

    # 2027-28 Social Studies has no bid count yet (too early in the cycle).
    ss = by_subject.get("K-12 Social Studies")
    if not ss:
        _fail("K-12 Social Studies cycle missing")
    if ss["bid_count"] is not None:
        _fail(f"Social Studies bid_count should be None, got {ss['bid_count']}")
    if ss["ay_start"] != 2027 or ss["ay_end"] != 2028:
        _fail(f"Social Studies year range wrong: {ss['ay_start']}-{ss['ay_end']}")
    _ok("K-12 Social Studies: no bid count yet, year range correct")

    print(f"\nAll Florida adapter tests passed. Output sample:")
    print(json.dumps(data, indent=2)[:500] + "...")


if __name__ == "__main__":
    run()
