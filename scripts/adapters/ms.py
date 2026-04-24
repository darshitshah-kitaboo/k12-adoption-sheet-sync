"""Mississippi adapter.

Scrapes the Mississippi Instructional Materials Matter "HQIM Adoption"
page and returns a normalized dict with the current adoption cycle and
the per-subject adopted-materials landing pages.

Mississippi publishes a single active adoption year at a time (e.g.
"25-26 Adoption Call for Bids"). The main page body carries cycle-level
artifacts like the long-range adoption schedule PDF, the rating
committee job description, the textbook administration handbook, and
the publisher representative form. Subject landing pages live on the
site-wide nav and are stable across cycles.

Per-subject fields emitted:
    subject, adopted_materials_url, ay_start, ay_end, cycle_label.

Wrapper fields emitted:
    cycle_label, ay_start, ay_end, has_active_cycle,
    call_for_bids_url, adoption_schedule_url,
    rating_committee_url, textbook_handbook_url,
    publisher_rep_form_url.

When the active cycle heading contains "Call for Bids", the wrapper
has_active_cycle flag is set to True and call_for_bids_url is filled
with the source_url (MS does not publish a separate Call for Bids
page or per-subject bid packets on this landing page). Per-cycle
call_for_bids_url is intentionally left null: MS has no per-subject
breakdown, and stamping every cycle would cause promote_scraped to
flip ac True on cycles that are not yet in their call phase (e.g.
future ELA or Math cycles that pre-date the current call).

Usage:
    python3 scripts/adapters/ms.py
    python3 scripts/adapters/ms.py --fixture FILE
    python3 scripts/adapters/ms.py --out scraped/MS.json
"""

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from scripts.adapters import base

STATE_CODE = "MS"
STATE_NAME = "Mississippi"
SOURCE_URL = "https://msinstructionalmaterials.org/resources/adoption/"

# "25-26 Adoption Call for Bids" or "2025-2026 Adoption Call for Bids".
CYCLE_HEADING_RE = re.compile(
    r"(\d{2,4})\s*[-\u2013]\s*(\d{2,4})\s+Adoption",
    re.IGNORECASE,
)

# MS uses "Call for Bids" as the active-cycle signal inside the h2
# heading. When present the page is advertising an open call;
# otherwise the section is a historical or upcoming reference.
CALL_FOR_BIDS_RE = re.compile(r"call\s+for\s+bids", re.IGNORECASE)

# Links to subject catalog pages on the MS IMM site. The slug after
# /adopted-materials/ names the subject, e.g.
#   /adopted-materials/science-adopted-materials/
#   /adopted-materials/ela/
SUBJECT_PATH_RE = re.compile(r"/adopted-materials/([^/]+)/?$")

# Slug-to-display-name overrides for cases where the slug does not
# cleanly title-case (ELA is an acronym, Pre-K uses a hyphen that the
# slug loses, etc.).
SLUG_OVERRIDES = {
    "ela": "ELA",
    "adopted-materials-pre-kindergarten": "Pre-Kindergarten",
    "career-technical-education-adopted-materials": "Career and Technical Education",
    "health-and-physical-education-adopted-materials": "Health and Physical Education",
    "business-and-technology-adopted-materials": "Business and Technology",
    "computer-science-adopted-materials": "Computer Science",
    "mathematics-adopted-materials": "Mathematics",
    "social-studies-adopted-materials": "Social Studies",
    "science-adopted-materials": "Science",
    "world-language-adopted-materials": "World Language",
    "arts-adopted-materials": "Arts",
}


def fetch_html(url=SOURCE_URL):
    """Fetch the MS IMM page via the shared helper."""
    return base.fetch_html(url)


def _normalize_year(y):
    """Convert two- or four-digit year to four-digit int. 25 -> 2025."""
    y = int(y)
    if y < 100:
        y += 2000
    return y


def _slug_to_subject(slug):
    """Turn an adopted-materials slug into a display subject name."""
    if slug in SLUG_OVERRIDES:
        return SLUG_OVERRIDES[slug]
    # Fallback: title-case with hyphens to spaces, drop "adopted materials".
    words = slug.replace("-", " ").split()
    keep = [w for w in words if w.lower() not in ("adopted", "materials")]
    return " ".join(w.capitalize() for w in keep) or slug


def parse(html, source_url=SOURCE_URL):
    """Parse the MS IMM adoption page and return a normalized dict."""
    soup = BeautifulSoup(html, "html.parser")
    scraped_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    all_links = base.all_links(soup, source_url)

    # Cycle label and AY bounds come from the first h2/h3 matching the
    # "NN-NN Adoption Call for Bids" pattern. Two-digit years expand to
    # the 2000s. "Call for Bids" in the same heading flips
    # has_active_cycle so the wrapper flag surfaces an open call.
    cycle_label = None
    ay_start = ay_end = None
    has_active_cycle = False
    for tag in soup.find_all(["h2", "h3"]):
        text = tag.get_text(" ", strip=True)
        m = CYCLE_HEADING_RE.search(text)
        if m:
            ay_start = _normalize_year(m.group(1))
            ay_end = _normalize_year(m.group(2))
            cycle_label = text
            has_active_cycle = bool(CALL_FOR_BIDS_RE.search(text))
            break

    # Wrapper artifacts. The adoption schedule and rating committee
    # anchors sit alone at the top of the main content block. Use the
    # visible link text to pick them out.
    _, adoption_schedule_url = base.first_link_matching(
        all_links, "upcoming hqim adoption schedules")
    if not adoption_schedule_url:
        _, adoption_schedule_url = base.first_link_matching(
            all_links, "adoption schedule")

    _, rating_committee_url = base.first_link_matching(
        all_links, "rating committee")

    # Publisher information block: handbook PDF and rep form DOCX.
    _, textbook_handbook_url = base.first_link_matching(
        all_links, "textbook administration handbook")
    _, publisher_rep_form_url = base.first_link_matching(
        all_links, "publisher representative")

    # Subject catalog. Walk every anchor whose href lands on an
    # /adopted-materials/SLUG/ path and dedupe by slug. The top nav
    # contains the canonical list on every MS IMM page.
    seen_slugs = set()
    cycles = []
    for text, href in all_links:
        m = SUBJECT_PATH_RE.search(href)
        if not m:
            continue
        slug = m.group(1).lower()
        if slug in seen_slugs:
            continue
        seen_slugs.add(slug)
        subject = _slug_to_subject(slug)
        cycles.append({
            "subject": subject,
            "adopted_materials_url": urljoin(source_url, href),
            "ay_start": ay_start,
            "ay_end": ay_end,
            "cycle_label": cycle_label,
        })

    # Sort subjects alphabetically so the diff stays stable when the
    # nav renders in a different order.
    cycles.sort(key=lambda c: c["subject"].lower())

    # Call for Bids URL. MS puts the open-call content on the adoption
    # landing page itself, so source_url is the best state-level link
    # while the heading says "Call for Bids". Null when no active call.
    call_for_bids_url = source_url if has_active_cycle else None

    return {
        "state": STATE_CODE,
        "name": STATE_NAME,
        "source_url": source_url,
        "scraped_at": scraped_at,
        "cycle_label": cycle_label,
        "ay_start": ay_start,
        "ay_end": ay_end,
        "cycle_count": len(cycles),
        "has_active_cycle": has_active_cycle,
        "call_for_bids_url": call_for_bids_url,
        "adoption_schedule_url": adoption_schedule_url,
        "rating_committee_url": rating_committee_url,
        "textbook_handbook_url": textbook_handbook_url,
        "publisher_rep_form_url": publisher_rep_form_url,
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
