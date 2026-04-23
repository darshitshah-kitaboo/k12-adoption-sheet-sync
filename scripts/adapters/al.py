"""Alabama adapter.

Scrapes the Alabama SDE "Textbook Adoption and Procurement" page and
emits one cycle record per content area. Alabama runs a rolling
multi-year schedule where each subject comes up for review on its own
calendar, so the useful publisher signal is "for each subject, what is
the most recent approved list and the most recent pending review list".

Page structure:
    <h3>Textbook by Subject - Arts Education</h3>
      <p><a>Alabama State Board Approved/Rejected Arts Education
         Textbooks and Materials 2025-2026</a></p>
      <p>Description mentioning the State Board meeting date.</p>
      <p><a>Arts Education Textbook and Supplemental Materials List
         Submitted for State Textbook Committee Review 2025-2026</a></p>
      <p>Description mentioning the tentative State Board meeting.</p>
    <h3>Textbook by Subject - Career and Technical Education</h3>
      ...
    <h3>Adoption Process - Schedule</h3>
      <p><a>Alabama Courses of Study Standards and State Textbook
         Adoption Cycle</a></p>

Subjects on the page:
    Arts Education, Career and Technical Education, Digital Literacy
    and Computer Science, English Language Arts, Health/PE,
    Mathematics, Science, Social Studies, World Languages.

Per-subject fields emitted:
    subject, ay_start, ay_end, cycle_label, approved_list_url,
    approved_board_meeting_date, pending_list_url,
    pending_board_meeting_date.

Wrapper fields emitted:
    adoption_cycle_schedule_url, adoption_process_forms_url,
    publishers_documents_url.

Usage:
    python3 scripts/adapters/al.py
    python3 scripts/adapters/al.py --fixture FILE
    python3 scripts/adapters/al.py --out scraped/AL.json
"""

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from scripts.adapters import base

STATE_CODE = "AL"
STATE_NAME = "Alabama"
SOURCE_URL = (
    "https://www.alabamaachieves.org/content-areas-specialty/"
    "textbook-adoption-and-procurement/"
)

# Subject h3: "Textbook by Subject - Arts Education" or en-dash or em-dash.
SUBJECT_HEADING_RE = re.compile(
    r"Textbook\s+by\s+Subject\s*[\u2013\u2014\-]\s*(.+)",
    re.IGNORECASE,
)

# "2025-2026" or "2025\u20132026" in anchor text.
YEAR_RANGE_RE = re.compile(r"(\d{4})\s*[\u2013\-]\s*(\d{4})")

# "March 12, 2026" inside description paragraphs.
BOARD_MEETING_RE = re.compile(
    r"(January|February|March|April|May|June|July|"
    r"August|September|October|November|December)\s+"
    r"(\d{1,2}),?\s+(\d{4})",
    re.IGNORECASE,
)

_MONTHS = ["january", "february", "march", "april", "may", "june",
           "july", "august", "september", "october", "november", "december"]

# Anchor text signals. "approved" and "rejected" both appear in final
# list titles ("State Board Approved/Rejected ..."). "submitted for state
# textbook committee review" is the pending review list.
APPROVED_TERMS = ("state board approved", "approved/rejected")
PENDING_TERMS = ("submitted for state textbook committee review",)


def fetch_html(url=SOURCE_URL):
    """Fetch the Alabama SDE page via the shared helper."""
    return base.fetch_html(url)


def _subject_from_heading(text):
    """If `text` matches 'Textbook by Subject - X', return X, else None."""
    m = SUBJECT_HEADING_RE.match(text.strip())
    return m.group(1).strip() if m else None


def _section_items(h3, source_url):
    """Walk forward siblings of `h3` until the next h2/h3, collecting anchors
    and the paragraph text that follows each one.

    Returns a list of dicts with keys: text, href, description.
    """
    items = []
    for sib in h3.find_next_siblings():
        name = getattr(sib, "name", None)
        if name in ("h1", "h2", "h3"):
            break
        if not hasattr(sib, "find_all"):
            continue
        anchors = sib.find_all("a")
        if anchors:
            for a in anchors:
                href = a.get("href", "") or ""
                if not href:
                    continue
                # Prefer the title attribute when present. The visible
                # text on alabamaachieves.org often trails a "New Window"
                # icon that gets flattened into the link text.
                text = a.get("title") or a.get_text(" ", strip=True)
                items.append({
                    "text": text,
                    "href": urljoin(source_url, href),
                    "description": "",
                })
        else:
            desc = sib.get_text(" ", strip=True)
            if desc and items:
                # Attach to most recent anchor; the page pairs each
                # anchor with the paragraph immediately after it.
                items[-1]["description"] = (
                    items[-1]["description"] + " " + desc
                ).strip()
    return items


def _classify(anchor_text):
    """Return (kind, (ay_start, ay_end)) for an anchor title.

    kind is 'approved', 'pending', or None. Year range is None if the
    title did not include a YYYY-YYYY fragment.
    """
    low = anchor_text.lower()
    year_m = YEAR_RANGE_RE.search(anchor_text)
    year = (int(year_m.group(1)), int(year_m.group(2))) if year_m else None

    # "approved/rejected" needs to match before generic "approved" so
    # historical "State Adopted Textbooks ..." lines without a review
    # verdict are not misclassified. We search for exact phrases.
    for term in APPROVED_TERMS:
        if term in low:
            return "approved", year
    for term in PENDING_TERMS:
        if term in low:
            return "pending", year
    return None, year


def _latest(items, kind):
    """Pick the item with the highest ay_start matching `kind`.

    Returns the dict with extra keys ay_start, ay_end, or None if no match.
    """
    best = None
    for it in items:
        ikind, year = _classify(it["text"])
        if ikind != kind or year is None:
            continue
        if best is None or year[0] > best["ay_start"]:
            best = {**it, "ay_start": year[0], "ay_end": year[1]}
    return best


def _meeting_date(description):
    """Parse 'March 12, 2026' from a description paragraph.

    Returns ISO date string or None.
    """
    if not description:
        return None
    m = BOARD_MEETING_RE.search(description)
    if not m:
        return None
    try:
        month = _MONTHS.index(m.group(1).lower()) + 1
        day = int(m.group(2))
        year = int(m.group(3))
        return f"{year:04d}-{month:02d}-{day:02d}"
    except (ValueError, IndexError):
        return None


def parse(html, source_url=SOURCE_URL):
    """Parse the Alabama SDE page and return a normalized dict."""
    soup = BeautifulSoup(html, "html.parser")
    scraped_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    all_links = base.all_links(soup, source_url)

    # Wrapper URLs. The adoption cycle PDF lives under an "Adoption
    # Process - Schedule" heading. The title "Alabama Courses of Study
    # Standards and State Textbook Adoption Cycle" is stable.
    _, adoption_cycle_schedule_url = base.first_link_matching(
        all_links, "courses of study", "textbook adoption cycle")
    _, adoption_process_forms_url = base.first_link_matching(
        all_links, "alabama state textbooks adoption process forms")
    _, publishers_documents_url = base.first_link_matching_any(
        all_links, [
            ["publisher", "documents"],
            ["publisher's", "documents"],
        ])

    cycles = []
    newest_ay_start = None
    newest_cycle_label = None

    for h3 in soup.find_all("h3"):
        subj = _subject_from_heading(h3.get_text(" ", strip=True))
        if not subj:
            continue

        items = _section_items(h3, source_url)
        approved = _latest(items, "approved")
        pending = _latest(items, "pending")

        if not approved and not pending:
            # A subject heading with no trackable cycle (Health/PE had
            # only pre-2016 entries at the time of writing). Skip it so
            # we do not emit a stale record with no urls.
            continue

        # Reference record used to stamp the cycle's AY. Prefer pending
        # when it is the newer of the two since that signals the next
        # upcoming cycle; otherwise take approved.
        if pending and (not approved or pending["ay_start"] >= approved["ay_start"]):
            ref = pending
        else:
            ref = approved

        ay_start = ref["ay_start"]
        ay_end = ref["ay_end"]
        cycle_label = f"{ay_start}-{ay_end} Adoption"

        if newest_ay_start is None or ay_start > newest_ay_start:
            newest_ay_start = ay_start
            newest_cycle_label = cycle_label

        cycles.append({
            "subject": subj,
            "ay_start": ay_start,
            "ay_end": ay_end,
            "cycle_label": cycle_label,
            "approved_list_url": approved["href"] if approved else None,
            "approved_board_meeting_date": _meeting_date(
                approved.get("description") if approved else None),
            "pending_list_url": pending["href"] if pending else None,
            "pending_board_meeting_date": _meeting_date(
                pending.get("description") if pending else None),
        })

    return {
        "state": STATE_CODE,
        "name": STATE_NAME,
        "source_url": source_url,
        "scraped_at": scraped_at,
        "cycle_year": newest_ay_start,
        "cycle_label": newest_cycle_label,
        "cycle_count": len(cycles),
        "adoption_cycle_schedule_url": adoption_cycle_schedule_url,
        "adoption_process_forms_url": adoption_process_forms_url,
        "publishers_documents_url": publishers_documents_url,
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
