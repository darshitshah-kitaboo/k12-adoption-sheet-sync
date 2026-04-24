"""North Carolina adapter.

Scrapes the NC DPI "Textbook Adoption" page and emits one cycle record
matching the current status of the NC Textbook Commission.

As of the 2026-04-23 refresh, NC has no active Call for Bids. The State
Board adopted Draft 4 of the 2026 ELA Standard Course of Study on
January 8, 2026, with implementation scheduled for 2027-28. Multiple
Textbook Commission seats have been vacant since April 2025, so the
Commission has not posted a new adoption cycle.

The adapter treats NC as a single-cycle state, the same shape OK and TN
use. The emitted row points at the ELA Standards Revision cycle and
carries wrapper URLs for the Commission page, the Publishers Registry,
and any current Invitation to Submit.

Fields emitted per cycle:
    subject, ay_start, ay_end, cycle_label,
    call_for_bids_url, invitation_to_submit_url,
    commission_page_url, publishers_registry_url.

Wrapper fields (cross-cycle):
    textbook_adoption_page_url, textbook_commission_url,
    ela_standards_url, has_active_cycle.

Usage:
    python3 scripts/adapters/nc.py
    python3 scripts/adapters/nc.py --fixture FILE
    python3 scripts/adapters/nc.py --out scraped/NC.json
"""

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from bs4 import BeautifulSoup

from scripts.adapters import base

STATE_CODE = "NC"
STATE_NAME = "North Carolina"
SOURCE_URL = (
    "https://www.dpi.nc.gov/districts-schools/district-operations/"
    "textbook-adoption"
)

# "2026 ELA Standard Course of Study" or "ELA Standard Course of Study
# (2026)" or "Draft 4 2026 ELA Standards". Any 4-digit year near ELA.
ELA_YEAR_RE = re.compile(
    r"(\d{4})\s+English\s+Language\s+Arts\s+Standard",
    re.IGNORECASE,
)
ELA_YEAR_ALT_RE = re.compile(
    r"English\s+Language\s+Arts[^\d]{0,30}(\d{4})",
    re.IGNORECASE,
)
ELA_SCoS_ALT_RE = re.compile(
    r"(\d{4})\s+ELA\s+Standard\s+Course\s+of\s+Study",
    re.IGNORECASE,
)


def fetch_html(url=SOURCE_URL):
    """Fetch the NC DPI textbook adoption page."""
    return base.fetch_html(url)


def _extract_ela_standards_year(text):
    """Return the first 4-digit year adjacent to an ELA standards mention."""
    if not text:
        return None
    for pat in (ELA_SCoS_ALT_RE, ELA_YEAR_RE, ELA_YEAR_ALT_RE):
        m = pat.search(text)
        if m:
            year = int(m.group(1))
            if 2015 <= year <= 2035:
                return year
    return None


def parse(html, source_url=SOURCE_URL):
    """Parse the NC DPI textbook adoption page and return a normalized dict."""
    soup = BeautifulSoup(html, "html.parser")
    scraped_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    main = soup.find("main") or soup.find("article") or soup
    main_text = main.get_text(" ", strip=True)

    links = base.all_links(soup, source_url)

    # Wrapper URLs. Side-nav and body anchors on dpi.nc.gov tend to repeat,
    # so match on distinctive multi-word phrases rather than single tokens.
    _, textbook_commission_url = base.first_link_matching(
        links, "textbook commission")
    _, publishers_registry_url = base.first_link_matching_any(
        links, [
            ["publishers registry"],
            ["publisher registry"],
            ["publisher", "register"],
        ])
    _, ela_standards_url = base.first_link_matching_any(
        links, [
            ["english language arts", "standard course of study"],
            ["ela", "standard course of study"],
            ["ela", "scos"],
            ["english", "course of study"],
        ])
    _, office_teaching_learning_url = base.first_link_matching(
        links, "office of teaching and learning")

    # Current cycle signals. If an "Invitation to Submit" or "Call for Bids"
    # anchor appears on the page, NC has an active cycle.
    _, invitation_url = base.first_link_matching_any(
        links, [
            ["invitation to submit"],
            ["invitation", "submit"],
        ])
    _, call_for_bids_url = base.first_link_matching_any(
        links, [
            ["call for bids"],
            ["call for bid"],
            ["bid", "textbook"],
        ])

    # Rubrics, criteria, evaluation docs.
    _, evaluation_criteria_url = base.first_link_matching_any(
        links, [
            ["evaluation criteria"],
            ["selection criteria"],
            ["textbook criteria"],
        ])

    has_active_cycle = bool(invitation_url or call_for_bids_url)

    # ELA standards year. Default to 2026 when the page names the 2026 SCoS,
    # which is the current SBE-adopted revision driving the 2027-28 cycle.
    ela_year = _extract_ela_standards_year(main_text)

    # Build the cycle record. NC runs one tracked cycle at a time; when no
    # active Call for Bids exists the record still carries the standards
    # revision pointer so downstream consumers know what's coming.
    if ela_year:
        ay_start = ela_year + 1
        ay_end = ay_start + 1
        cycle_label = (f"{ay_start}-{ay_end} ELA Standards Implementation"
                       if has_active_cycle
                       else f"{ay_start}-{ay_end} ELA (pending Call for Bids)")
        subject = "ELA Standards Revision"
    else:
        ay_start = ay_end = None
        cycle_label = ("Active Call for Bids" if has_active_cycle
                       else "Monitoring (no active cycle)")
        subject = "Unknown (see Commission page)"

    cycles = [{
        "subject": subject,
        "ay_start": ay_start,
        "ay_end": ay_end,
        "cycle_label": cycle_label,
        "call_for_bids_url": call_for_bids_url,
        "invitation_to_submit_url": invitation_url,
        "commission_page_url": textbook_commission_url,
        "publishers_registry_url": publishers_registry_url,
        "evaluation_criteria_url": evaluation_criteria_url,
    }]

    return {
        "state": STATE_CODE,
        "name": STATE_NAME,
        "source_url": source_url,
        "scraped_at": scraped_at,
        "has_active_cycle": has_active_cycle,
        "ela_standards_year": ela_year,
        "cycle_count": len(cycles),
        "textbook_adoption_page_url": source_url,
        "textbook_commission_url": textbook_commission_url,
        "publishers_registry_url": publishers_registry_url,
        "ela_standards_url": ela_standards_url,
        "office_teaching_learning_url": office_teaching_learning_url,
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
