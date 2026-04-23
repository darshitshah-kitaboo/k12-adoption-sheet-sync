"""Oklahoma adapter.

Scrapes the Oklahoma State Department of Education "Information for
Publishers" page and emits one cycle record keyed to the current
Adoption Subject Cycle calendar. The actual list of subjects under
review lives inside a PDF (the Adoption Subject Cycle Calendar),
so this adapter captures the pointer to that PDF rather than the
subject list itself. Downstream consumers link to the PDF.

What the adapter extracts per cycle record:
    - current cycle year (e.g. 2026)
    - AY window (2026-2027) from the STC calendar heading
    - STC meeting calendar URL for that AY
    - Adoption Subject Cycle Calendar URL (the subject list PDF)
    - Publisher bid artifacts: data privacy form, out-of-cycle flyer,
      supplemental submissions form, substitution memo, substitution flyer

Wrapper-level URLs (same across cycles):
    - HQIM Evaluation Rubrics page
    - Approved Titles page
    - HQIM Review Process page
    - Publisher State Registration Form (Airtable)
    - PK-8 and 9-12 Subject Codes PDFs
    - HQIM cycle graphic PDF

Because Oklahoma publishes only one active cycle at a time, one
cycle record is emitted with subject "All subjects per Adoption
Subject Cycle Calendar", matching the TN substitution-window pattern.

Usage:
    python3 scripts/adapters/ok.py
    python3 scripts/adapters/ok.py --fixture FILE
    python3 scripts/adapters/ok.py --out scraped/OK.json
"""

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from bs4 import BeautifulSoup

from scripts.adapters import base

STATE_CODE = "OK"
STATE_NAME = "Oklahoma"
SOURCE_URL = (
    "https://oklahoma.gov/education/services/hqim/info-for-publishers.html"
)
WARMUP_URL = "https://oklahoma.gov/"

# "Adoption Subject Cycle Calendar 2026" or "Adoption Subject Cycle
# Calendar - 2026" or "...Adoption Subject Cycle Calendar 2026
# (amended March 2026)". Only the first 4-digit year counts.
CYCLE_YEAR_RE = re.compile(
    r"Adoption\s+Subject\s+(?:Material\s+)?(?:Cycle\s+)?"
    r"(?:Material\s+)?Calendar[\s\u2013-]*(\d{4})",
    re.IGNORECASE,
)

# "State Textbook Committee Calendar 2026-2027" with hyphen or en-dash.
STC_CALENDAR_AY_RE = re.compile(
    r"State\s+Textbook\s+Committee\s+Calendar\s+"
    r"(\d{4})\s*[-\u2013]\s*(\d{4})",
    re.IGNORECASE,
)


def fetch_html(url=SOURCE_URL):
    """Fetch the OK publisher info page using the shared WAF-safe fetch.

    The warmup hit to oklahoma.gov's root lets any WAF cookie land in
    the session before we hit the publisher page, matching the TN
    adapter pattern.
    """
    return base.fetch_html(url, warmup_url=WARMUP_URL)


def parse(html, source_url=SOURCE_URL):
    """Parse OK publisher info HTML and return a normalized dict."""
    soup = BeautifulSoup(html, "html.parser")
    scraped_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    # Page main body. The OSDE template puts breadcrumbs and content
    # inside a main/article region; the side nav is separate. We scan
    # the whole page for links but use the main content for the cycle
    # year pattern.
    main = soup.find("main") or soup.find("article") or soup
    main_text = main.get_text(" ", strip=True)

    cycle_year = None
    m = CYCLE_YEAR_RE.search(main_text)
    if m:
        cycle_year = int(m.group(1))

    ay_start = ay_end = None
    ay_m = STC_CALENDAR_AY_RE.search(main_text)
    if ay_m:
        ay_start = int(ay_m.group(1))
        ay_end = int(ay_m.group(2))
    elif cycle_year:
        # If the STC calendar heading was worded differently, fall
        # back to cycle_year -> cycle_year+1 so downstream consumers
        # still get an AY window.
        ay_start = cycle_year
        ay_end = cycle_year + 1

    cycle_label = None
    if ay_start and ay_end:
        cycle_label = f"{ay_start}-{ay_end} STC Adoption Cycle"
    elif cycle_year:
        cycle_label = f"{cycle_year} STC Adoption Cycle"

    links = base.all_links(soup, source_url)

    # Cycle-scoped documents. The AEM side nav repeats some of these
    # titles ("Approved Titles", "HQIM Evaluation Rubrics") so we avoid
    # anchors whose text exactly matches a nav label by scoping the
    # hunt with distinctive multi-word phrases.
    _, stc_calendar_url = base.first_link_matching(
        links, "state textbook committee calendar")
    _, subject_cycle_calendar_url = base.first_link_matching_any(
        links, [
            ["adoption subject cycle calendar"],
            ["subject material adoption cycle calendar"],
        ])

    _, data_privacy_form_url = base.first_link_matching(
        links, "data privacy", "attestation")
    _, out_of_cycle_flyer_url = base.first_link_matching_any(
        links, [
            ["out-of-cycle"],
            ["out of cycle"],
        ])
    # Supplemental form anchor text on oklahoma.gov is just "form", so
    # the surrounding heading is the only reliable signal. Scope to
    # airtable.com hrefs so we don't grab an unrelated anchor.
    supplemental_form_url = base.link_under_heading(
        soup, source_url, "supplemental submissions",
        href_prefix="https://airtable.com/")
    if not supplemental_form_url:
        _, supplemental_form_url = base.first_link_matching_any(
            links, [
                ["supplemental", "form"],
                ["supplementary", "form"],
            ])
    _, substitution_memo_url = base.first_link_matching(
        links, "substitution bid memorandum")
    _, substitution_flyer_url = base.first_link_matching(
        links, "substitution bid flyer")
    _, substitution_guidance_url = base.first_link_matching_any(
        links, [
            ["substitution guidance"],
            ["publisher updates during contracted adoption period"],
        ])

    # Wrapper-scoped URLs (cross-cycle). These live in the side nav or
    # the "Other Useful Information" section.
    _, evaluation_rubrics_url = base.first_link_matching(
        links, "hqim evaluation rubrics")
    _, approved_titles_url = base.first_link_matching(
        links, "approved titles")
    _, review_process_url = base.first_link_matching(
        links, "hqim review process")
    _, publisher_registration_form_url = base.first_link_matching(
        links, "publisher state registration form")
    _, hqim_cycle_graphic_url = base.first_link_matching(
        links, "cycle graphic")
    _, subject_codes_pk8_url = base.first_link_matching_any(
        links, [
            ["pk-8", "subject codes"],
            ["pk-8th", "subject codes"],
            ["pk-8", "codes"],
        ])
    _, subject_codes_9_12_url = base.first_link_matching_any(
        links, [
            ["9-12", "subject codes"],
            ["9th-12th", "subject codes"],
            ["9-12", "codes"],
        ])

    cycles = []
    if cycle_year or ay_start:
        cycles.append({
            "subject": "All subjects per Adoption Subject Cycle Calendar",
            "ay_start": ay_start,
            "ay_end": ay_end,
            "cycle_label": cycle_label,
            "stc_calendar_url": stc_calendar_url,
            "subject_cycle_calendar_url": subject_cycle_calendar_url,
            "data_privacy_form_url": data_privacy_form_url,
            "out_of_cycle_flyer_url": out_of_cycle_flyer_url,
            "supplemental_form_url": supplemental_form_url,
            "substitution_memo_url": substitution_memo_url,
            "substitution_flyer_url": substitution_flyer_url,
            "substitution_guidance_url": substitution_guidance_url,
        })

    return {
        "state": STATE_CODE,
        "name": STATE_NAME,
        "source_url": source_url,
        "scraped_at": scraped_at,
        "cycle_year": cycle_year,
        "cycle_label": cycle_label,
        "cycle_count": len(cycles),
        "evaluation_rubrics_url": evaluation_rubrics_url,
        "approved_titles_url": approved_titles_url,
        "review_process_url": review_process_url,
        "publisher_registration_form_url": publisher_registration_form_url,
        "hqim_cycle_graphic_url": hqim_cycle_graphic_url,
        "subject_codes_pk8_url": subject_codes_pk8_url,
        "subject_codes_9_12_url": subject_codes_9_12_url,
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
