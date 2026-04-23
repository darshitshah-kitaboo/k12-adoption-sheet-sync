"""Smoke tests for the Texas adapter.

Runs without network. Uses hand-written HTML that mirrors the actual
Texas SBOE current IMRA cycle page structure. If SBOE redesigns the
page and the parser stops working, this test will keep passing (it is
pinned to the current shape), but the live adapter will produce zero
cycles, which the coordinator will flag.

Run:
    python3 scripts/adapters/test_tx.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from scripts.adapters import tx  # noqa: E402


# Fixture based on the live SBOE page shape. Uses en-dashes in subject
# names like the live page, and keeps section ordering intact.
FIXTURE_HTML = """
<html><body>
<h1>Current IMRA Cycle</h1>
<p>Instructional Materials Review and Approval (IMRA) Cycle 2026 will include
a review of instructional materials for K-12 fine arts, certain courses in
6-12 Career and Technical Education (CTE), and K-5 supplemental reading
language arts (RLA).</p>

<h3>IMRA Process</h3>
<p>The <a href="https://tea.texas.gov/state-board-of-education/imra/imra-process-2026.pdf">IMRA Process document</a>
outlines the multi-step IMRA process.</p>

<h2>Request for Instructional Materials (RFIM) | IMRA 2026</h2>
<p>The <a href="https://tea.texas.gov/state-board-of-education/imra/imra26-rfim.pdf">RFIM</a>
for IMRA Cycle 2026 includes details and directions about the IMRA process.</p>

<h4>Full-subject, Tier one instructional materials:</h4>
<ul>
  <li>K-12 English mathematics, including middle school advanced mathematics</li>
  <li>K-6 Spanish mathematics, including grade 6 advanced mathematics</li>
  <li>K-5 English and Spanish language arts and reading (ELAR and SLAR)</li>
  <li>6-12 Career and Technical Education (CTE) | Batch 1 courses</li>
  <li>K-12 fine arts</li>
</ul>

<h4>Partial-subject, Tier one instructional materials:</h4>
<ul>
  <li>K-3 English and Spanish phonics</li>
</ul>

<h4>Supplemental instructional materials:</h4>
<ul>
  <li>K-12 English mathematics</li>
  <li>K-6 Spanish mathematics</li>
  <li>K-5 ELAR and SLAR</li>
</ul>

<h2>Rubrics | IMRA 2026</h2>

<h3>Suitability Rubric</h3>
<ul>
  <li><a href="https://tea.texas.gov/state-board-of-education/imra/imra25-sboe-suitability-rubric-approved-11-22-24.pdf">IMRA Suitability Rubric</a> (PDF)</li>
</ul>

<h3>Quality Rubrics</h3>

<h4>Full-subject and partial-subject, Tier one instructional materials:</h4>
<ul>
  <li><a href="https://tea.texas.gov/state-board-of-education/imra/imra26-cte-6-12-sboe-approved-quality-rubric.pdf">CTE 6-12 IMRA Quality Rubric</a> (PDF)</li>
  <li><a href="https://tea.texas.gov/state-board-of-education/imra/imra25-elar-k3-sboe-approved-quality-rubric.pdf">ELAR K-3 IMRA Quality Rubric</a> (PDF)</li>
  <li><a href="https://tea.texas.gov/state-board-of-education/imra/imra25-elar-4-8-sboe-approved-quality-rubric.pdf">ELAR 4-8 IMRA Quality Rubric</a> (PDF)</li>
  <li><a href="https://tea.texas.gov/state-board-of-education/imra/imra26-fine-arts-k12-sboe-approved-quality-rubric.pdf">Fine Arts K-12 IMRA Quality Rubric</a> (PDF)</li>
  <li><a href="https://tea.texas.gov/state-board-of-education/imra/imra25-slar-k3-sboe-approed-quality-rubric.pdf">SLAR K-3 IMRA Quality Rubric</a> (PDF)</li>
  <li><a href="https://tea.texas.gov/state-board-of-education/imra/imra25-slar-4-6-sboe-approved-quality-rubric.pdf">SLAR 4-6 IMRA Quality Rubric</a> (PDF)</li>
  <li><a href="https://tea.texas.gov/state-board-of-education/imra/imra25-math-k12-sboe-approved-quality-rubric.pdf">Mathematics K-12 IMRA Quality Rubric</a> (PDF)</li>
</ul>

<h4>Supplemental instructional materials:</h4>
<ul>
  <li><a href="https://tea.texas.gov/state-board-of-education/imra/imra25-supplemental-math-k12-sboe-approved-quality-rubric.pdf">Supplemental Mathematics K-12 IMRA Quality Rubric</a> (PDF)</li>
  <li><a href="https://tea.texas.gov/state-board-of-education/imra/imra26-supplemental-rla-k5-sboe-approved-quality-rubric.pdf">Supplemental RLA K-5 IMRA Quality Rubric</a> (PDF)</li>
</ul>

</body></html>
"""


def _fail(msg):
    print(f"FAIL: {msg}")
    sys.exit(1)


def _ok(msg):
    print(f"ok  {msg}")


def run():
    data = tx.parse(FIXTURE_HTML)

    if data["state"] != "TX":
        _fail(f"expected state TX, got {data['state']}")
    if data["name"] != "Texas":
        _fail(f"expected name Texas, got {data['name']}")
    if data["cycle_year"] != 2026:
        _fail(f"expected cycle_year 2026, got {data['cycle_year']}")
    _ok("wrapper fields and cycle year parsed")

    cycles = data["cycles"]
    if data["cycle_count"] != len(cycles):
        _fail("cycle_count mismatch")

    # Expected: 5 full-subject + 1 partial-subject + 3 supplemental = 9.
    if len(cycles) != 9:
        subjects = [(c["tier"], c["subject"]) for c in cycles]
        _fail(f"expected 9 cycles, got {len(cycles)}: {subjects}")
    _ok(f"parsed {len(cycles)} cycles across three tiers")

    # Tier counts.
    tier_counts = {}
    for c in cycles:
        tier_counts[c["tier"]] = tier_counts.get(c["tier"], 0) + 1
    if tier_counts.get("full-subject-tier-one") != 5:
        _fail(f"full-subject tier should have 5, got {tier_counts}")
    if tier_counts.get("partial-subject-tier-one") != 1:
        _fail(f"partial-subject tier should have 1, got {tier_counts}")
    if tier_counts.get("supplemental") != 3:
        _fail(f"supplemental tier should have 3, got {tier_counts}")
    _ok("tier counts correct: 5 full, 1 partial, 3 supplemental")

    # Shared artifacts should be identical across every cycle record.
    for c in cycles:
        if "imra-process-2026.pdf" not in (c["process_url"] or ""):
            _fail(f"process_url missing or wrong on {c['subject']}: {c['process_url']}")
        if "imra26-rfim.pdf" not in (c["rfim_url"] or ""):
            _fail(f"rfim_url missing or wrong on {c['subject']}: {c['rfim_url']}")
        if "suitability-rubric" not in (c["suitability_rubric_url"] or ""):
            _fail(f"suitability_rubric_url missing on {c['subject']}")
    _ok("process, RFIM, and suitability URLs populated on every record")

    # Rubric matching spot checks.
    by_subject = {(c["tier"], c["subject"]): c for c in cycles}

    math_full = by_subject.get(("full-subject-tier-one",
                                "K-12 English mathematics, including middle school advanced mathematics"))
    if not math_full:
        _fail(f"math full-subject cycle missing. Have: {list(by_subject)}")
    math_rubrics = " ".join(math_full["quality_rubric_urls"])
    if "math-k12" not in math_rubrics.lower():
        _fail(f"math cycle did not match math rubric: {math_full['quality_rubric_urls']}")
    if "supplemental" in math_rubrics.lower():
        _fail("full-subject math should not match the supplemental rubric")
    _ok("K-12 math matched to math quality rubric only")

    elar_slar = by_subject.get(("full-subject-tier-one",
                                "K-5 English and Spanish language arts and reading (ELAR and SLAR)"))
    if not elar_slar:
        _fail("ELAR and SLAR cycle missing")
    combined = " ".join(elar_slar["quality_rubric_urls"]).lower()
    if "elar" not in combined or "slar" not in combined:
        _fail(f"ELAR/SLAR cycle should match both rubric families: {elar_slar['quality_rubric_urls']}")
    _ok("K-5 ELAR and SLAR matched to both ELAR and SLAR rubrics")

    fine_arts = by_subject.get(("full-subject-tier-one", "K-12 fine arts"))
    if not fine_arts:
        _fail("Fine arts cycle missing")
    if not any("fine-arts" in u.lower() for u in fine_arts["quality_rubric_urls"]):
        _fail(f"Fine arts did not match fine arts rubric: {fine_arts['quality_rubric_urls']}")
    _ok("K-12 fine arts matched to fine arts rubric")

    # Supplemental math should match supplemental math rubric.
    supp_math = by_subject.get(("supplemental", "K-12 English mathematics"))
    if not supp_math:
        _fail("Supplemental math cycle missing")
    supp_rubric_joined = " ".join(supp_math["quality_rubric_urls"]).lower()
    if "supplemental-math" not in supp_rubric_joined:
        _fail(f"Supplemental math did not match supplemental math rubric: {supp_math['quality_rubric_urls']}")
    _ok("Supplemental math matched supplemental math rubric")

    # Supplemental ELAR/SLAR should pick up the Supplemental RLA K-5 rubric.
    # The rubric title uses "RLA" instead of "ELAR/SLAR", so the matcher
    # must know these are the same family.
    supp_rla = by_subject.get(("supplemental", "K-5 ELAR and SLAR"))
    if not supp_rla:
        _fail("Supplemental K-5 ELAR and SLAR cycle missing")
    rla_joined = " ".join(supp_rla["quality_rubric_urls"]).lower()
    if "supplemental-rla" not in rla_joined:
        _fail(f"Supplemental ELAR/SLAR did not match supplemental RLA rubric: {supp_rla['quality_rubric_urls']}")
    if "supplemental-math" in rla_joined:
        _fail("Supplemental ELAR/SLAR should not match supplemental math rubric")
    _ok("Supplemental K-5 ELAR/SLAR matched Supplemental RLA rubric")

    # K-3 phonics should pick up the ELAR K-3 and SLAR K-3 rubrics since
    # phonics is covered under reading/language arts at K-3.
    phonics = by_subject.get(("partial-subject-tier-one", "K-3 English and Spanish phonics"))
    if not phonics:
        _fail("Partial-subject K-3 phonics cycle missing")
    phonics_joined = " ".join(phonics["quality_rubric_urls"]).lower()
    if "elar-k3" not in phonics_joined:
        _fail(f"K-3 phonics did not match ELAR K-3 rubric: {phonics['quality_rubric_urls']}")
    if "slar-k3" not in phonics_joined:
        _fail(f"K-3 phonics did not match SLAR K-3 rubric: {phonics['quality_rubric_urls']}")
    _ok("K-3 phonics matched ELAR K-3 and SLAR K-3 rubrics")

    # Academic year bounds: cycle 2026 means ay_start 2026, ay_end 2027.
    for c in cycles:
        if c["ay_start"] != 2026 or c["ay_end"] != 2027:
            _fail(f"Cycle {c['subject']} had wrong AY bounds: {c['ay_start']}-{c['ay_end']}")
    _ok("academic year bounds set from cycle year on every record")

    print("\nAll Texas adapter tests passed. Output sample:")
    print(json.dumps(data, indent=2)[:600] + "...")


if __name__ == "__main__":
    run()
