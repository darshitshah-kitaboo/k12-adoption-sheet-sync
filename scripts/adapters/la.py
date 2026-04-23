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
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    print("FATAL: requests and beautifulsoup4 required.", file=sys.stderr)
    print("Run: pip3 install requests beautifulsoup4", file=sys.stderr)
    sys.exit(2)

STATE_CODE = "LA"
STATE_NAME = "Louisiana"
SOURCE_URL = "https://doe.louisiana.gov/school-system-leaders/instructional-materials-reviews"

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
TIMEOUT = 30

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
    """Fetch the live LDOE IMR page. Raises on non-200."""
    r = requests.get(url, headers=BROWSER_HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return r.text


def _find_heading_containing(soup, phrase, tag_names=("h1", "h2", "h3", "h4")):
    """Return the first heading whose text contains the phrase (case insensitive)."""
    needle = phrase.lower()
    for name in tag_names:
        for h in soup.find_all(name):
            if needle in h.get_text(" ", strip=True).lower():
                return h
    return None


def _collect_bullets(start, stop_tags):
    """Return list of plain-text bullets before a stop tag."""
    bullets = []
    for sib in start.find_next_siblings():
        name = getattr(sib, "name", None)
        if name in stop_tags:
            break
        if not hasattr(sib, "find_all"):
            continue
        for li in sib.find_all("li"):
            txt = li.get_text(" ", strip=True)
            # Louisiana uses bullets with trailing commas and "and". Strip
            # both so the subject name is clean for downstream matching.
            # "and" may sit between a comma and nothing ("..., and"), so run
            # the trim twice.
            for _ in range(2):
                txt = txt.rstrip(",.").strip()
                if txt.lower().endswith(" and"):
                    txt = txt[:-4].strip()
            if txt:
                bullets.append(txt)
    return bullets


def _find_all_links(soup):
    """Return every (text, absolute_href) pair on the page."""
    out = []
    for a in soup.find_all("a"):
        href = a.get("href", "") or ""
        if not href:
            continue
        txt = a.get_text(" ", strip=True)
        out.append((txt, urljoin(SOURCE_URL, href)))
    return out


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
    current = _find_heading_containing(soup, "Currently Under Review")
    ay_start = ay_end = None
    cycle_label = None
    if current:
        m = CURRENT_CYCLE_RE.search(current.get_text(" ", strip=True))
        if m:
            ay_start = int(m.group(1))
            ay_end = int(m.group(2))
            cycle_label = f"{ay_start}-{ay_end} Review Cycle"

    # Subjects under review live in a UL immediately after that heading.
    subjects = []
    if current:
        subjects = _collect_bullets(current, stop_tags=("h1", "h2", "h3"))

    # Page-wide artifacts. The weekly report shows up in the same block.
    all_links = _find_all_links(soup)
    weekly_report_url = None
    for text, href in all_links:
        tl = text.lower()
        if "weekly report" in tl and "instructional materials" in tl:
            weekly_report_url = href
            break
    if not weekly_report_url:
        # Looser match as a fallback.
        for text, href in all_links:
            if "weekly report" in text.lower():
                weekly_report_url = href
                break

    publisher_guide_url = None
    for text, href in all_links:
        if "publisher" in text.lower() and "submission" in text.lower():
            publisher_guide_url = href
            break

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
