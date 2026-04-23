"""Smoke tests for the South Carolina adapter.

Runs without network. Fixture is a trimmed copy of the SCDE
"Instructional Materials" landing page covering the h3 sections the
adapter actually uses (HQIM overview, Current Approved Adoptions,
Information for Publishers, Contact Information).

Run:
    python3 scripts/adapters/test_sc.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from scripts.adapters import sc  # noqa: E402


FIXTURE_HTML = """
<html><body>
<main>
  <h2>Instructional Materials</h2>

  <h3><a href="/instruction/instructional-materials/high-quality-instructional-materials-hqim/">High-Quality Instructional Materials</a></h3>
  <ul>
    <li><a href="/instruction/instructional-materials/high-quality-instructional-materials-hqim/">About</a></li>
    <li><a href="/instruction/instructional-materials/high-quality-instructional-materials-hqim/statewide-webinars-for-the-textbook-adoption-process/">Statewide Webinars for Adoption Process</a></li>
  </ul>

  <h3><a href="/instruction/instructional-materials/current-approved-adoptions/">Current Approved Adoptions</a></h3>
  <ul>
    <li><a href="/instruction/instructional-materials/current-approved-adoptions/supplemental-instructional-materials/2025-comprehensive-listing-of-adopted-materials-for-math/">2025 Comprehensive Listing of Adopted Materials for Math</a></li>
    <li><a href="/instruction/instructional-materials/current-approved-adoptions/supplemental-instructional-materials/2025-comprehensive-listing-of-ancillary-materials-for-math/">2025 Comprehensive Listing of Ancillary Materials for Math</a></li>
    <li><a href="/instruction/instructional-materials/current-approved-adoptions/2025-26-approved-math-adoption/">2025-26 Instructional Materials Adoption Information</a></li>
    <li><a href="/instruction/instructional-materials/current-approved-adoptions/2024-25-instructional-materials-adoption-information/">2024-25 Instructional Materials Adoption Information</a></li>
    <li><a href="/instruction/instructional-materials/current-approved-adoptions/2024-computer-education-adopted-programs/">2025 Computer Education Adoption</a></li>
    <li><a href="/instruction/instructional-materials/current-approved-adoptions/supplemental-instructional-materials/">Comprehensive Materials List</a></li>
  </ul>

  <h3><a href="/instruction/instructional-materials/contact-information/">Contact Information</a></h3>
  <ul>
    <li><a href="/instruction/instructional-materials/contact-information/">Office of Instructional Materials</a></li>
    <li><a href="https://docs.google.com/document/d/1AcoWV8_NrsyaNC1wp-oTaUx9DOJBMnLQ_lpjPbzm_ik/edit?usp=sharing">Publisher Representatives with Adopted Instructional Materials</a></li>
  </ul>

  <h3><a href="/instruction/instructional-materials/information-for-publishers/">Information for Publishers</a></h3>
  <ul>
    <li><a href="/instruction/instructional-materials/information-for-publishers/2026-call-for-bids-for-instructional-materials/">2026 Call for Bid Information</a></li>
    <li><a href="https://docs.google.com/document/d/1OZUFOXYed66FdzQH9mD8vou-XkmqOEANzpN7m5-OBHI/view?tab=t.0">Tentative Textbook Adoption Schedule</a></li>
    <li><a href="/instruction/instructional-materials/information-for-publishers/publisher-and-vendor-registration/">Publisher and Vendor Registration</a></li>
    <li><a href="/instruction/instructional-materials/information-for-publishers/publisher-vendor-registration-for-the-instructional-materials-bid-portal-imbp/">Publisher and Vendor Registration for Instructional Materials Bid Portal (IMBP)</a></li>
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
    data = sc.parse(FIXTURE_HTML)

    if data["state"] != "SC":
        _fail(f"expected state SC, got {data['state']}")
    if data["name"] != "South Carolina":
        _fail(f"expected name South Carolina, got {data['name']}")
    _ok("wrapper fields populated")

    # Newest adoption is "2025-26 Instructional Materials Adoption Information".
    if data["cycle_year"] != 2025:
        _fail(f"expected cycle_year 2025, got {data['cycle_year']}")
    if data["cycle_label"] != "2025-26 Adoption":
        _fail(f"cycle_label wrong: {data['cycle_label']}")
    _ok("newest adoption stamped as 2025-26")

    # Publisher-facing wrappers.
    if "2026-call-for-bids" not in (data["call_for_bids_url"] or ""):
        _fail(f"call_for_bids_url wrong: {data['call_for_bids_url']}")
    if "docs.google.com" not in (data["tentative_adoption_schedule_url"] or ""):
        _fail(f"tentative_adoption_schedule_url wrong: {data['tentative_adoption_schedule_url']}")
    if "publisher-and-vendor-registration" not in (data["publisher_registration_url"] or ""):
        _fail(f"publisher_registration_url wrong: {data['publisher_registration_url']}")
    if "imbp" not in (data["imbp_registration_url"] or "").lower():
        _fail(f"imbp_registration_url wrong: {data['imbp_registration_url']}")
    if not data["publisher_reps_list_url"] or "docs.google.com" not in data["publisher_reps_list_url"]:
        _fail(f"publisher_reps_list_url wrong: {data['publisher_reps_list_url']}")
    _ok("call for bids, schedule, registrations, and reps list URLs captured")

    # HQIM overview and webinars pulled from the HQIM h3 block.
    if "high-quality-instructional-materials-hqim" not in (data["hqim_overview_url"] or ""):
        _fail(f"hqim_overview_url wrong: {data['hqim_overview_url']}")
    if "statewide-webinars" not in (data["adoption_webinars_url"] or ""):
        _fail(f"adoption_webinars_url wrong: {data['adoption_webinars_url']}")
    _ok("HQIM overview and adoption webinars URLs captured")

    # Cycles: skip the bare "Comprehensive Materials List" (no year
    # prefix), keep the 5 dated links.
    if data["cycle_count"] != 5:
        subjects = [(c["subject"], c["ay_start"]) for c in data["cycles"]]
        _fail(f"expected 5 cycles, got {data['cycle_count']}: {subjects}")
    _ok("5 dated adoption rows kept, undated wrapper skipped")

    # Sort order: newest ay_start first.
    ay_starts = [c["ay_start"] for c in data["cycles"]]
    if ay_starts != sorted(ay_starts, reverse=True):
        _fail(f"cycles not sorted newest-first: {ay_starts}")
    _ok("cycles sorted newest AY first")

    # Spot-check a row per shape: year range and single year.
    math_row = next(c for c in data["cycles"]
                    if "2025-comprehensive-listing-of-adopted-materials-for-math" in c["approved_materials_url"])
    if math_row["ay_start"] != 2025 or math_row["ay_end"] != 2026:
        _fail(f"math comprehensive AY wrong: {math_row['ay_start']}-{math_row['ay_end']}")
    if math_row["subject"] != "Math (Adopted)":
        _fail(f"math adopted subject wrong: {math_row['subject']!r}")
    _ok("single-year '2025' links expand to 2025-2026 adoption")

    # Adopted and ancillary math must be separate subject keys so the
    # coordinator diff treats them as different rows.
    ancillary_row = next(c for c in data["cycles"]
                         if "ancillary-materials-for-math" in c["approved_materials_url"])
    if ancillary_row["subject"] != "Math (Ancillary)":
        _fail(f"math ancillary subject wrong: {ancillary_row['subject']!r}")
    _ok("adopted vs ancillary modifier preserved on Math subject")

    range_row = next(c for c in data["cycles"]
                     if "2025-26-approved-math-adoption" in c["approved_materials_url"])
    if range_row["ay_start"] != 2025 or range_row["ay_end"] != 2026:
        _fail(f"2025-26 row AY wrong: {range_row['ay_start']}-{range_row['ay_end']}")
    _ok("year-range '2025-26' links parsed correctly")

    print("\nAll South Carolina adapter tests passed. Output sample:")
    print(json.dumps(data, indent=2)[:600] + "...")


if __name__ == "__main__":
    run()
