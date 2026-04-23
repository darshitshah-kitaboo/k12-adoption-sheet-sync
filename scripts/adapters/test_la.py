"""Smoke tests for the Louisiana adapter.

Runs without network. Uses hand-written HTML that mirrors the real
LDOE Instructional Materials Reviews page structure, specifically the
"Currently Under Review" block and the rubric list.

Run:
    python3 scripts/adapters/test_la.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from scripts.adapters import la  # noqa: E402


FIXTURE_HTML = """
<html><body>
<h1>Instructional Materials Reviews</h1>
<h2>Currently Under Review: 2025-2026 Review Cycle</h2>
<p>The online instructional materials review focuses on materials in:</p>
<ul>
  <li>early childhood education,</li>
  <li>K-12 science full courses, and</li>
  <li>K-12 social studies courses.</li>
</ul>

<h3>Instructional Materials Review Weekly Report</h3>
<p><a href="/docs/default-source/curricular-resources/imr-weekly-report.pdf">Instructional Materials Review Weekly Report</a></p>

<h2>Publishers</h2>
<h3>Publisher Tools</h3>
<ul>
  <li><a href="/docs/default-source/curricular-resources/publisher-communication-protocol.pdf">Publisher Communication Protocol</a></li>
  <li><a href="/docs/default-source/curricular-resources/publisher-guide-for-imr-submission.pdf">Publisher's Guide for IMR Submission</a></li>
</ul>

<h3>Instructional Materials Review Rubrics</h3>
<ul>
  <li><a href="/docs/default-source/curricular-resources/2024-2025-imr-rubric---science-k-12.pdf">2024-2025 IMR Rubric - Science K-12</a></li>
  <li><a href="/docs/default-source/curricular-resources/2024-2025-imr-rubric---social-studies-k-12.pdf">2024-2025 IMR Rubric - Social Studies K-12</a></li>
  <li><a href="/docs/default-source/curricular-resources/2025-2026-imr-rubric---ece-ages-birth-to-five.pdf">2025-2026 IMR Rubric - ECE Ages Birth to Five</a></li>
  <li><a href="/docs/default-source/curricular-resources/2025-2026-imr-rubric---science-k-12.pdf">2025-2026 IMR Rubric - Science K-12</a></li>
  <li><a href="/docs/default-source/curricular-resources/2025-2026-imr-rubric---social-studies-k-12.pdf">2025-2026 IMR Rubric - Social Studies K-12</a></li>
</ul>
</body></html>
"""


def _fail(msg):
    print(f"FAIL: {msg}")
    sys.exit(1)


def _ok(msg):
    print(f"ok  {msg}")


def run():
    data = la.parse(FIXTURE_HTML)

    if data["state"] != "LA":
        _fail(f"expected state LA, got {data['state']}")
    if data["name"] != "Louisiana":
        _fail(f"expected name Louisiana, got {data['name']}")
    if data["cycle_label"] != "2025-2026 Review Cycle":
        _fail(f"cycle label wrong: {data['cycle_label']}")
    _ok("wrapper and cycle label parsed")

    cycles = data["cycles"]
    if data["cycle_count"] != len(cycles):
        _fail("cycle_count mismatch")

    # Three subjects: ECE, K-12 science, K-12 social studies.
    if len(cycles) != 3:
        _fail(f"expected 3 cycles, got {len(cycles)}: {[c['subject'] for c in cycles]}")
    _ok(f"parsed {len(cycles)} review cycles")

    by_subject = {c["subject"]: c for c in cycles}

    # Bullet cleanup: trailing commas and "and" should be stripped.
    expected_subjects = {"early childhood education",
                         "K-12 science full courses",
                         "K-12 social studies courses"}
    if set(by_subject.keys()) != expected_subjects:
        _fail(f"subject set wrong. Got {set(by_subject.keys())}")
    _ok("bullets cleaned: no trailing commas or dangling 'and'")

    # Every cycle should have the shared weekly report URL and publisher guide.
    for c in cycles:
        if "imr-weekly-report.pdf" not in (c["weekly_report_url"] or ""):
            _fail(f"weekly_report_url missing on {c['subject']}")
        if "publisher-guide-for-imr-submission" not in (c["publisher_guide_url"] or ""):
            _fail(f"publisher_guide_url missing on {c['subject']}")
        if c["ay_start"] != 2025 or c["ay_end"] != 2026:
            _fail(f"wrong AY bounds on {c['subject']}: {c['ay_start']}-{c['ay_end']}")
    _ok("weekly report, publisher guide, and AY bounds set on every record")

    # Rubric matching: current year (2025-2026) should win over older year.
    ece = by_subject["early childhood education"]
    if "2025-2026" not in (ece["rubric_url"] or ""):
        _fail(f"ECE should match 2025-2026 rubric: {ece['rubric_url']}")
    if "ece-ages-birth-to-five" not in (ece["rubric_url"] or "").lower():
        _fail(f"ECE rubric URL wrong: {ece['rubric_url']}")
    _ok("Early childhood matched 2025-2026 ECE rubric")

    sci = by_subject["K-12 science full courses"]
    if "2025-2026" not in (sci["rubric_url"] or ""):
        _fail(f"Science should match 2025-2026 rubric, not older: {sci['rubric_url']}")
    if "science-k-12" not in (sci["rubric_url"] or "").lower():
        _fail(f"Science rubric URL wrong: {sci['rubric_url']}")
    _ok("K-12 science matched 2025-2026 science rubric (not the 2024-2025 one)")

    ss = by_subject["K-12 social studies courses"]
    if "2025-2026" not in (ss["rubric_url"] or ""):
        _fail(f"Social studies should match 2025-2026 rubric: {ss['rubric_url']}")
    if "social-studies-k-12" not in (ss["rubric_url"] or "").lower():
        _fail(f"Social studies rubric URL wrong: {ss['rubric_url']}")
    _ok("K-12 social studies matched 2025-2026 social studies rubric")

    print("\nAll Louisiana adapter tests passed. Output sample:")
    print(json.dumps(data, indent=2)[:600] + "...")


if __name__ == "__main__":
    run()
