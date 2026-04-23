"""Louisiana adapter.

Scrapes the Louisiana DOE "Instructional Materials Reviews" page and
returns a normalized dict of subjects under review in the current review
cycle.

Louisiana does not adopt textbooks at the state level. Districts pick
their own materials. What the state publishes is a tiered review of
publisher submissions, and that is what publishers actually track to
understand where their product stands in Louisiana.

The page has one section called "Currently Under Review: YYYY-YYYY
Review Cycle" which lists the subjects the state is reviewing that year.
For each subject we want to know:
    - subject name
    - review cycle year range (ay_start, ay_end)
    - the matching rubric PDF for that year/subject
    - the shared weekly report PDF

Downstream consumers (dashboards, notifications) can then answer:
"Is Louisiana reviewing my subject area this cycle, and where is the
current rubric?"

Usage:
    python3 scripts/adapters/la.py
    python3 scripts/adapters/la.py --fixture FILE
    python3 scripts/adapters/la.py --out scraped/LA.json
"""

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from bs4 import BeautifulSoup

from scripts.adapters import base

STATE_CODE = "LA"
STATE_NAME = "Louisiana"
SOURCE_URL = "https://doe.louisiana.gov/school-system-leaders/instructional-materials-reviews"

# "Currently Under Review: 2025-2026 Review Cycle" or with en-dash.
CURRENT_CYCLE_RE = re.compile(
    r"Currently\s+Under\s+Review\s*:?\s*(\d{4})\s*[-\u2013]\s*(\d{4})",
    re.IGNORECASE,
)

# Subject keywords mapped to rubric keywords, same pattern as TX.
# Reading covers ELA/RLA/phonics; math, science, social studies are
# their own families; early childhood stands alone.
RUBRIC_KEYWORDS = {
    "reading": ["ela", "reading", "language arts", "phonics", "rla"],
    "math": ["math"],
    "science": ["science"],
    "social studies": ["social studies"],
    "early childhood": ["early childhood", "ece", "birth to five"],
}


def fetch_html(url=SOURCE_URL):
    """Fetch the LDOE IMR page.

    Louisiana doe.louisiana.gov has never WAF-blocked scripted requests,
    so a plain one-shot GET through the shared helper is enough.
    """
    return base.fetch_html(url)


def _match_rubric(subject, ay_start, links):
    """Return rubric URL for this subject in the current cycle year.

    Strategy: find rubric-titled links whose text mentions the cycle's
    starting year (e.g. "2025-2026") AND a keyword family matching the
    subject. If the dated version is not there, fall back to an undated
    subject match so something is still attached.
    """
    subj_lower = subject.lower()
    target_year = f"{ay_start}-{ay_start + 1}" if ay_start else ""

    # Figure out which keyword families apply to this subject.
    subject_families = [
        family for family, keywords in RUBRIC_KEYWORDS.items()
        if any(kw in subj_lower for kw in keywords)
    ]
    if not subject_families:
        return None

    # Only rubric links count. Scan them once.
    candidates = [
        (text, href) for text, href in links
        if "rubric" in text.lower() and "imr" in text.lower()
    ]

    # First pass: dated rubric that matches subject.
    for family in subject_families:
        fam_keywords = RUBRIC_KEYWORDS[family]
        for text, href in candidates:
            tl = text.lower()
            if target_year and target_year not in tl:
                continue
            if any(kw in tl for kw in fam_keywords):
                return href

    # Second pass: any rubric matching subject regardless of year.
    for family in subject_families:
        fam_keywords = RUBRIC_KEYWORDS[family]
        for text, href in candidates:
            tl = text.lower()
            if any(kw in tl for kw in fam_keywords):
                return href

    return None


def parse(html, source_url=SOURCE_URL):
    """Parse LDOE IMR page HTML and return a normalized dict."""
    soup = BeautifulSoup(html, "html.parser")
    scraped_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    # Find the "Currently Under Review: YYYY-YYYY" heading and grab its years.
    current = base.find_heading_containing(soup, "Currently Under Review")
    ay_start = ay_end = None
    cycle_label = None
    if current:
        m = CURRENT_CYCLE_RE.search(current.get_text(" ", strip=True))
        if m:
            ay_start = int(m.group(1))
            ay_end = int(m.group(2))
            cycle_label = f"{ay_start}-{ay_end} Review Cycle"

    # Subjects under review live in a UL immediately after that heading.
    # LA bullets tend to end with ", and" or trailing commas, so enable
    # the base helper's cleanup pass.
    subjects = []
    if current:
        subjects = base.collect_bullets(
            current, stop_tags=("h1", "h2", "h3"), clean=True)

    # Page-wide artifacts. The weekly report shows up in the same block.
    all_links = base.all_links(soup, source_url)
    _, weekly_report_url = base.first_link_matching(
        all_links, "weekly report", "instructional materials")
    if not weekly_report_url:
        # Looser match as a fallback.
        _, weekly_report_url = base.first_link_matching(
            all_links, "weekly report")

    _, publisher_guide_url = base.first_link_matching(
        all_links, "publisher", "submission")

    cycles = []
    for subject in subjects:
        rubric_url = _match_rubric(subject, ay_start, all_links)
        cycles.append({
            "subject": subject,
            "ay_start": ay_start,
            "ay_end": ay_end,
            "cycle_label": cycle_label,
            "rubric_url": rubric_url,
            "weekly_report_url": weekly_report_url,
            "publisher_guide_url": publisher_guide_url,
        })

    return {
        "state": STATE_CODE,
        "name": STATE_NAME,
        "source_url": source_url,
        "scraped_at": scraped_at,
        "cycle_label": cycle_label,
        "cycle_count": len(cycles),
        "cycles": cycles,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixture", help="Parse a local HTML file instead of fetching")
    ap.add_argument("--out", help="Write JSON output to this file")
    args = ap.parse_args()

    if args.fixture:
        html = Path(args.fixture).read_text(encoding="utf-8")
    else:
        html = fetch_html()

    data = parse(html)
    text = json.dumps(data, indent=2)

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"Wrote {args.out} with {data['cycle_count']} cycles")
    else:
        print(text)

    return data


if __name__ == "__main__":
    main()
