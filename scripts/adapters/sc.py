"""South Carolina adapter.

Scrapes the South Carolina DOE "Instructional Materials" landing page
and returns a normalized dict of current approved adoptions plus the
publisher-facing artifacts needed to bid into the current cycle.

The SCDE page is a directory of sections rather than a per-subject
adoption detail page. Each section (HQIM, Current Approved Adoptions,
Instructions and Forms, Information for Publishers, etc.) is headed
with an h3 and followed by a ul of links. The adapter picks out the
subject-bearing links inside "Current Approved Adoptions" and treats
each one as a cycle record. Wrapper fields pick up the publisher
bid artifacts.

Per-subject fields emitted:
    subject, ay_start, ay_end, cycle_label,
    approved_materials_url, call_for_bids_url.

When the landing page links a "Call for Bids" page, a synthetic
"Instructional Materials" active cycle is also emitted so that
promote_scraped has a cycle-level actionable URL to work with
downstream. The year is extracted from the slug (e.g.
"2026-call-for-bids" -> 2026).

Wrapper fields emitted:
    call_for_bids_url, tentative_adoption_schedule_url,
    publisher_registration_url, imbp_registration_url,
    hqim_overview_url, adoption_webinars_url,
    publisher_reps_list_url, has_active_cycle.

Usage:
    python3 scripts/adapters/sc.py
    python3 scripts/adapters/sc.py --fixture FILE
    python3 scripts/adapters/sc.py --out scraped/SC.json
"""

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from scripts.adapters import base

STATE_CODE = "SC"
STATE_NAME = "South Carolina"
SOURCE_URL = "https://ed.sc.gov/instruction/instructional-materials/"

# "2025-26" or "2025-2026" at the start of a link text, optionally as
# "2025 Comprehensive..." or "2025 Computer Education Adoption".
YEAR_PREFIX_RE = re.compile(
    r"^\s*(\d{4})(?:\s*[-\u2013]\s*(\d{2,4}))?\b",
)

# Stop-phrase terms that signal a row is a navigation anchor (like
# "Supplemental Instructional Materials") rather than a datable subject
# adoption. These are kept as wrappers instead of subject cycles.
NON_SUBJECT_LINK_TERMS = (
    "comprehensive materials list",
)

# Match "2026-call-for-bid" or "2026-call-for-bids" inside the path of
# the Call for Bids URL. The four-digit year is the adoption year the
# call is targeting.
CFB_YEAR_IN_PATH = re.compile(r"/(\d{4})-call-for-bid", re.IGNORECASE)


def fetch_html(url=SOURCE_URL):
    """Fetch the SCDE instructional materials page via the shared helper."""
    return base.fetch_html(url)


def _normalize_year(y):
    """Convert two- or four-digit year to four-digit int."""
    y = int(y)
    if y < 100:
        y += 2000
    return y


def _extract_subject(link_text, ay_start, ay_end):
    """Strip the year prefix from a 'Current Approved Adoptions' link
    and clean the remainder into a subject name.

    Handles shapes like:
        "2025-26 Instructional Materials Adoption Information"
        "2024-25 Instructional Materials Adoption Information"
        "2025 Comprehensive Listing of Adopted Materials for Math"
        "2025 Comprehensive Listing of Ancillary Materials for Math"
        "2025 Computer Education Adoption"
        "2025-26 Approved Math Adoption"

    The "Adopted" vs "Ancillary" split matters because both rows carry
    the same (subject, ay_start, ay_end) otherwise, and the coordinator
    would treat them as a single cycle. Keeping that modifier on the
    subject keeps the key unique.
    """
    text = link_text.strip()
    # Strip the leading year or year range.
    text = YEAR_PREFIX_RE.sub("", text, count=1).strip()

    # Detect a material-type modifier that has to survive into the
    # final subject so rows don't collide on (subject, ay_start, ay_end).
    modifier = None
    low = text.lower()
    if "ancillary materials" in low:
        modifier = "Ancillary"
    elif "adopted materials" in low:
        modifier = "Adopted"

    # Strip filler words that add no subject signal.
    for phrase in (
        "Comprehensive Listing of Adopted Materials for",
        "Comprehensive Listing of Ancillary Materials for",
        "Instructional Materials Adoption Information",
        "Approved",
        "Adoption Information",
        "Adoption",
    ):
        if phrase.lower() in text.lower():
            text = re.sub(re.escape(phrase), "", text, flags=re.IGNORECASE).strip()
    # Collapse duplicate spaces and trim stray punctuation.
    text = re.sub(r"\s{2,}", " ", text).strip(" -:,")
    subject = text or "Instructional Materials"
    if modifier:
        subject = f"{subject} ({modifier})"
    return subject


def parse(html, source_url=SOURCE_URL):
    """Parse the SCDE instructional materials page."""
    soup = BeautifulSoup(html, "html.parser")
    scraped_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    all_links = base.all_links(soup, source_url)

    # Wrapper URLs. Pick up publisher-facing artifacts by anchor text.
    _, call_for_bids_url = base.first_link_matching(
        all_links, "call for bid")
    _, tentative_adoption_schedule_url = base.first_link_matching(
        all_links, "tentative textbook adoption schedule")
    if not tentative_adoption_schedule_url:
        _, tentative_adoption_schedule_url = base.first_link_matching(
            all_links, "tentative", "adoption schedule")

    _, publisher_registration_url = base.first_link_matching_any(
        all_links, [
            ["publisher and vendor registration"],
            ["publisher", "vendor", "registration"],
        ],
        avoid=("imbp", "instructional materials bid portal"),
    )
    _, imbp_registration_url = base.first_link_matching_any(
        all_links, [
            ["imbp"],
            ["instructional materials bid portal"],
        ])

    _, hqim_overview_url = base.first_link_matching(
        all_links, "high-quality instructional materials")
    _, adoption_webinars_url = base.first_link_matching(
        all_links, "statewide webinars")
    _, publisher_reps_list_url = base.first_link_matching(
        all_links, "publisher representatives")

    # Find the "Current Approved Adoptions" section and walk every
    # sub-link under it. The SCDE site uses a feature-boxes card grid
    # where each section is a <div class="fb-item"> containing a title
    # block (the h3) and a sibling content block (<div class="fb-content">)
    # that holds the <ul> of links. The h3 is deeply nested inside an
    # <a> wrapper, so find_next_siblings() from the h3 returns nothing.
    # To reach the links we walk up to the enclosing fb-item and read
    # its fb-content.
    current_heading = base.find_heading_containing(
        soup, "current approved adoptions", tag_names=("h2", "h3", "h4"))

    def _links_in_card(heading):
        """Return (text, absolute_href) pairs inside the heading's fb-item card."""
        cur = heading
        card = None
        for _ in range(6):
            p = getattr(cur, "parent", None)
            if p is None:
                break
            klass = p.get("class") or []
            if "fb-item" in klass:
                card = p
                break
            cur = p
        if card is None:
            return []
        content = card.find("div", class_="fb-content")
        if content is None:
            return []
        pairs = []
        for a in content.find_all("a"):
            href = a.get("href", "") or ""
            if not href:
                continue
            txt = a.get_text(" ", strip=True)
            pairs.append((txt, urljoin(source_url, href)))
        return pairs

    cycles = []
    newest_ay_start = None
    if current_heading:
        # Try the fb-item card pattern first (real page), then fall back
        # to the legacy sibling walk that the smoke test fixture uses.
        section_links = _links_in_card(current_heading)
        if not section_links:
            section_links = base.collect_links_under(
                current_heading, source_url,
                stop_tags=("h1", "h2", "h3"))
        for link_text, href in section_links:
            low = link_text.lower()
            # Skip the Comprehensive Materials List wrapper link, which
            # points at an umbrella page rather than a dated adoption.
            if any(term in low for term in NON_SUBJECT_LINK_TERMS):
                continue
            m = YEAR_PREFIX_RE.match(link_text)
            if not m:
                continue
            ay_start = _normalize_year(m.group(1))
            ay_end = _normalize_year(m.group(2)) if m.group(2) else ay_start + 1
            subject = _extract_subject(link_text, ay_start, ay_end)
            cycle_label = f"{ay_start}-{str(ay_end)[-2:]} Adoption"
            if newest_ay_start is None or ay_start > newest_ay_start:
                newest_ay_start = ay_start
            cycles.append({
                "subject": subject,
                "ay_start": ay_start,
                "ay_end": ay_end,
                "cycle_label": cycle_label,
                "approved_materials_url": href,
                "call_for_bids_url": None,
            })

    # Synthesize an active "Instructional Materials" cycle when the
    # landing page links a Call for Bids page. SCDE runs a single
    # statewide call that covers every subject in a given adoption
    # year, so there is no per-subject breakdown on the landing page.
    # Carrying the call_for_bids_url at the cycle level lets
    # promote_scraped flip ac True and fill src for any matching
    # adoption_data cycle downstream.
    has_active_cycle = bool(call_for_bids_url)
    if call_for_bids_url:
        m_year = CFB_YEAR_IN_PATH.search(call_for_bids_url)
        if m_year:
            cfb_ay_start = int(m_year.group(1))
            cfb_ay_end = cfb_ay_start + 1
            cycles.append({
                "subject": "Instructional Materials",
                "ay_start": cfb_ay_start,
                "ay_end": cfb_ay_end,
                "cycle_label": f"{cfb_ay_start}-{str(cfb_ay_end)[-2:]} Adoption",
                "approved_materials_url": None,
                "call_for_bids_url": call_for_bids_url,
            })
            if newest_ay_start is None or cfb_ay_start > newest_ay_start:
                newest_ay_start = cfb_ay_start

    # Stable order: newest cycle first, then subject alphabetical.
    cycles.sort(key=lambda c: (-c["ay_start"], c["subject"].lower()))

    newest_cycle_label = None
    if newest_ay_start is not None:
        newest_cycle_label = f"{newest_ay_start}-{str(newest_ay_start + 1)[-2:]} Adoption"

    return {
        "state": STATE_CODE,
        "name": STATE_NAME,
        "source_url": source_url,
        "scraped_at": scraped_at,
        "cycle_year": newest_ay_start,
        "cycle_label": newest_cycle_label,
        "cycle_count": len(cycles),
        "has_active_cycle": has_active_cycle,
        "call_for_bids_url": call_for_bids_url,
        "tentative_adoption_schedule_url": tentative_adoption_schedule_url,
        "publisher_registration_url": publisher_registration_url,
        "imbp_registration_url": imbp_registration_url,
        "hqim_overview_url": hqim_overview_url,
        "adoption_webinars_url": adoption_webinars_url,
        "publisher_reps_list_url": publisher_reps_list_url,
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
