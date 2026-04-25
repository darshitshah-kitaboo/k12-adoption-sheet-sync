"""Smoke tests for the shared local-control adapter.

Runs without network. Fixture mimics a generic state DOE curriculum
hub with a mix of PDF guidance docs, an HQIM list link, an offsite
review portal, and footer noise that should be filtered out.

Run:
    python3 scripts/adapters/test_localctl.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from scripts.adapters import localctl  # noqa: E402


FIXTURE_HTML = """
<html>
<head><title>Curriculum and Instruction</title></head>
<body>
  <header><a href="https://example.gov/">Home</a></header>
  <main>
    <h1>Curriculum and Instruction</h1>
    <p>Districts in our state select instructional materials locally.</p>

    <h2>Frameworks and Standards</h2>
    <ul>
      <li><a href="/docs/math-framework-2025.pdf">Mathematics Framework 2025</a></li>
      <li><a href="/docs/ela-standards.pdf">ELA Standards</a></li>
      <li><a href="/docs/science-curriculum-guide.pdf">Science Curriculum Guide</a></li>
    </ul>

    <h2>High Quality Instructional Materials</h2>
    <ul>
      <li><a href="/docs/hqim-approved-list.pdf">HQIM Approved List</a></li>
      <li><a href="https://airtable.com/embed/shrAbcDef">Recommended Materials Catalog</a></li>
    </ul>

    <h2>Review Rubrics</h2>
    <ul>
      <li><a href="/docs/review-rubric.docx">Review Rubric Template</a></li>
      <li><a href="/docs/evaluation-criteria.pdf">Evaluation Criteria</a></li>
    </ul>

    <h2>Procurement Guidance</h2>
    <ul>
      <li><a href="/docs/procurement-handbook.pdf">Procurement Handbook</a></li>
    </ul>
  </main>

  <footer>
    <a href="/privacy">Privacy</a>
    <a href="/accessibility">Accessibility</a>
    <a href="https://twitter.com/example">Twitter</a>
    <a href="/sitemap">Sitemap</a>
  </footer>
</body>
</html>
"""


def _fail(msg):
    print(f"FAIL: {msg}")
    sys.exit(1)


def _ok(msg):
    print(f"ok  {msg}")


def run():
    data = localctl.parse(
        FIXTURE_HTML,
        source_url="https://example.gov/curriculum",
        state_code="ZZ",
        state_name="Test State",
        extra_hosts=("airtable.com",),
    )

    if data["state"] != "ZZ" or data["name"] != "Test State":
        _fail(f"wrapper state/name wrong: {data['state']}/{data['name']}")
    _ok("wrapper state and name populated")

    if data["page_title"] != "Curriculum and Instruction":
        _fail(f"page_title wrong: {data['page_title']}")
    _ok("page_title pulled from h1")

    # 7 trackable links: 3 frameworks, 2 HQIM (one Airtable), 2 rubrics,
    # 1 procurement = 8 total. Footer junk (privacy, accessibility,
    # twitter, sitemap) should be excluded.
    if data["document_count"] != 8:
        _fail(f"expected 8 docs, got {data['document_count']}: "
              f"{[d['document_url'] for d in data['cycles']]}")
    _ok("8 trackable documents captured (footer noise filtered)")

    if data["cycle_count"] != 8:
        _fail(f"cycle_count != document_count: {data['cycle_count']}")
    _ok("cycle_count matches document_count")

    if not data["content_hash"] or len(data["content_hash"]) != 16:
        _fail(f"content_hash malformed: {data['content_hash']}")
    _ok("content_hash present and 16 chars")

    if data["has_active_cycle"] is not False:
        _fail("has_active_cycle should be False for local-control")
    if data["call_for_bids_url"] is not None:
        _fail(f"call_for_bids_url should be None: {data['call_for_bids_url']}")
    _ok("local-control flags inert by design")

    by_subject = {}
    for c in data["cycles"]:
        by_subject.setdefault(c["subject"], []).append(c["title"])

    # Spot check categorization.
    if "HQIM" not in by_subject:
        _fail(f"HQIM bucket missing. Got: {sorted(by_subject)}")
    if "Framework" not in by_subject:
        _fail(f"Framework bucket missing. Got: {sorted(by_subject)}")
    if "Rubric" not in by_subject:
        _fail(f"Rubric bucket missing. Got: {sorted(by_subject)}")
    _ok("categorization routes HQIM, Framework, and Rubric correctly")

    # Verify Airtable host whitelist worked.
    airtable_hits = [c for c in data["cycles"]
                     if "airtable.com" in c["document_url"]]
    if len(airtable_hits) != 1:
        _fail(f"expected 1 Airtable hit, got {len(airtable_hits)}")
    _ok("extra_hosts whitelist captures non-PDF resources")

    # Stable diff: hash should be reproducible.
    again = localctl.parse(
        FIXTURE_HTML,
        source_url="https://example.gov/curriculum",
        state_code="ZZ", state_name="Test State",
        extra_hosts=("airtable.com",),
    )
    if again["content_hash"] != data["content_hash"]:
        _fail("content_hash not stable across runs")
    _ok("content_hash stable across repeated runs")

    # Removing a link should change the hash.
    pruned = FIXTURE_HTML.replace(
        '<li><a href="/docs/math-framework-2025.pdf">Mathematics Framework 2025</a></li>',
        "")
    pruned_data = localctl.parse(
        pruned, source_url="https://example.gov/curriculum",
        state_code="ZZ", state_name="Test State",
        extra_hosts=("airtable.com",),
    )
    if pruned_data["content_hash"] == data["content_hash"]:
        _fail("content_hash did not change when a link was removed")
    if pruned_data["document_count"] != 7:
        _fail(f"pruned doc count wrong: {pruned_data['document_count']}")
    _ok("content_hash and document_count react to link removal")

    print("\nAll local-control adapter tests passed. Sample output:")
    print(json.dumps(data, indent=2)[:600] + "...")


if __name__ == "__main__":
    run()
