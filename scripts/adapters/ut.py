"""Utah adapter.

Scrapes the Utah State Board of Education Instructional Materials
Commission (IMC) landing page and emits one cycle record keyed to the
current or next-scheduled subject review.

Utah's IMC reviews by subject on a rolling schedule. The 2025
Mathematics adoption closed December 2025 with the State Board
approving Math Nation (K-12) and STEMscopes Math (K-8). The next
review window is tied to the revised Utah Core Standards rollout,
with UT1 in the dashboard tracking the 2026-2027 math implementation.

Fields emitted per cycle:
    subject, ay_start, ay_end, cycle_label,
    recommended_materials_url, review_schedule_url,
    imc_calendar_url, publisher_submission_url.

Wrapper fields (cross-cycle):
    imc_page_url, review_process_url, core_standards_url,
    recommended_materials_page_url, has_active_review.

Usage:
    python3 scripts/adapters/ut.py
    python3 scripts/adapters/ut.py --fixture FILE
    python3 scripts/adapters/ut.py --out scraped/UT.json
"""

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from bs4 import BeautifulSoup

from scripts.adapters import base

STATE_CODE = "UT"
STATE_NAME = "Utah"
SOURCE_URL = "https://schools.utah.gov/curr/imc"

# Subjects Utah IMC reviews, mapped to display names. Left side is the
# canonical subject name, right side is a tuple of case-insensitive
# keyword groups we match against link text and headings.
SUBJECT_FAMILIES = [
    ("Mathematics", ("mathematics", "math")),
    ("English Language Arts", ("english language arts", "ela",
                               "language arts")),
    ("Science", ("science",)),
    ("Social Studies", ("social studies",)),
    ("Health", ("health",)),
    ("Fine Arts", ("fine arts", "arts education")),
    ("World Languages", ("world languages", "world language",
                         "dual language")),
    ("CTE", ("career and technical education", "cte")),
]

# "2026-2027 Math Review" or "2026 Mathematics Adoption".
CYCLE_AY_RE = re.compile(
    r"(\d{4})\s*[-\u2013]\s*(\d{4})",
)
CYCLE_SINGLE_YEAR_RE = re.compile(
    r"(\d{4})\s+(?:Mathematics|Math|English|Science|Social\s+Studies|"
    r"Arts|World\s+Languages|CTE|Review|Adoption)",
    re.IGNORECASE,
)


def fetch_html(url=SOURCE_URL):
    """Fetch the Utah IMC landing page."""
    return base.fetch_html(url)


def _match_subject(text):
    """Return the first display subject whose keywords appear in `text`."""
    low = (text or "").lower()
    for subject, keywords in SUBJECT_FAMILIES:
        if any(kw in low for kw in keywords):
            return subject
    return None


def _extract_cycle_window(text):
    """Return (ay_start, ay_end) from the first NNNN-NNNN in `text`, or (None, None)."""
    if not text:
        return None, None
    m = CYCLE_AY_RE.search(text)
    if m:
        start = int(m.group(1))
        end = int(m.group(2))
        if 2015 <= start <= 2040 and 2015 <= end <= 2045 and end >= start:
            return start, end
    m2 = CYCLE_SINGLE_YEAR_RE.search(text)
    if m2:
        start = int(m2.group(1))
        if 2015 <= start <= 2040:
            return start, start + 1
    return None, None


def parse(html, source_url=SOURCE_URL):
    """Parse the Utah IMC page and return a normalized dict."""
    soup = BeautifulSoup(html, "html.parser")
    scraped_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    main = soup.find("main") or soup.find("article") or soup
    main_text = main.get_text(" ", strip=True)

    links = base.all_links(soup, source_url)

    # Wrapper URLs. USBE's site repeats nav anchors on every page, so
    # match on distinctive phrases and fall back to weaker ones.
    _, review_process_url = base.first_link_matching_any(
        links, [
            ["imc review process"],
            ["review process"],
            ["how the imc works"],
        ])
    _, core_standards_url = base.first_link_matching_any(
        links, [
            ["utah core standards"],
            ["core standards"],
            ["state standards"],
        ])
    _, recommended_materials_page_url = base.first_link_matching_any(
        links, [
            ["recommended instructional materials"],
            ["recommended materials"],
            ["imc recommended"],
        ])
    _, imc_calendar_url = base.first_link_matching_any(
        links, [
            ["imc calendar"],
            ["imc meeting"],
            ["commission calendar"],
            ["review schedule"],
        ])
    _, publisher_submission_url = base.first_link_matching_any(
        links, [
            ["publisher submission"],
            ["submit materials"],
            ["publisher information"],
            ["publisher", "submit"],
        ])
    _, review_schedule_pdf_url = base.first_link_matching_any(
        links, [
            ["review schedule", "pdf"],
            ["adoption schedule"],
            ["review cycle"],
        ])

    # Detect the current/next subject under review. Utah highlights the
    # active subject in a headline or list on the IMC page. We look for
    # any heading that names one of the subject families along with a
    # year. The first match wins.
    current_subject = None
    current_subject_url = None
    current_ay_start = current_ay_end = None

    for tag in soup.find_all(["h1", "h2", "h3", "h4"]):
        text = tag.get_text(" ", strip=True)
        subject = _match_subject(text)
        if not subject:
            continue
        ay_s, ay_e = _extract_cycle_window(text)
        if not ay_s:
            # Heading names a subject but no year; look at its surrounding
            # paragraph text.
            nxt = tag.find_next(["p", "li", "div"])
            if nxt is not None:
                ay_s, ay_e = _extract_cycle_window(
                    nxt.get_text(" ", strip=True))
        if ay_s:
            current_subject = subject
            current_ay_start = ay_s
            current_ay_end = ay_e
            # Look for an anchor inside this heading or its next sibling.
            a = tag.find("a")
            if a is not None and a.get("href"):
                from urllib.parse import urljoin
                current_subject_url = urljoin(source_url, a["href"])
            break

    # Fallback: if no subject heading matched, scan anchor text.
    if current_subject is None:
        for text, href in links:
            subject = _match_subject(text)
            if not subject:
                continue
            ay_s, ay_e = _extract_cycle_window(text)
            if ay_s:
                current_subject = subject
                current_subject_url = href
                current_ay_start = ay_s
                current_ay_end = ay_e
                break

    # Fallback: use page-wide text to at least stamp an AY window if we
    # spotted a subject but no nearby year, so the cycle record is still
    # useful downstream.
    if current_subject and current_ay_start is None:
        ay_s, ay_e = _extract_cycle_window(main_text)
        if ay_s:
            current_ay_start = ay_s
            current_ay_end = ay_e

    has_active_review = current_subject is not None

    cycle_label = None
    if current_ay_start and current_ay_end:
        cycle_label = (f"{current_ay_start}-{current_ay_end} "
                       f"{current_subject} Review")
    elif current_subject:
        cycle_label = f"{current_subject} Review (dates TBD)"

    cycles = [{
        "subject": current_subject or "Unknown (see IMC calendar)",
        "ay_start": current_ay_start,
        "ay_end": current_ay_end,
        "cycle_label": cycle_label,
        "recommended_materials_url": recommended_materials_page_url,
        "review_schedule_url": review_schedule_pdf_url,
        "imc_calendar_url": imc_calendar_url,
        "publisher_submission_url": publisher_submission_url,
        "subject_landing_url": current_subject_url,
    }]

    return {
        "state": STATE_CODE,
        "name": STATE_NAME,
        "source_url": source_url,
        "scraped_at": scraped_at,
        "has_active_review": has_active_review,
        "current_subject": current_subject,
        "cycle_count": len(cycles),
        "imc_page_url": source_url,
        "review_process_url": review_process_url,
        "core_standards_url": core_standards_url,
        "recommended_materials_page_url": recommended_materials_page_url,
        "imc_calendar_url": imc_calendar_url,
        "publisher_submission_url": publisher_submission_url,
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
