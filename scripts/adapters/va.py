"""Virginia adapter.

Scrapes the Virginia DOE "Textbooks & Instructional Materials" page and
returns a normalized dict with the Board of Education's approved
materials landing pages per subject and the current review process
headline pulled from the News & Announcements block.

Virginia's model is a rolling subject-by-subject review. The Board of
Education approves textbooks for a single subject at a time, so the
main page does not carry a single dated cycle the way Florida or Texas
do. Instead it lists four subject landing pages (English, History &
Social Science, Mathematics, Science) and a current-news block that
names whichever subject is actively in review. The adapter treats each
subject link as its own cycle row and attaches the most recent news
announcement to the subject it mentions.

Per-subject fields emitted:
    subject, approved_materials_url,
    current_review_title, current_review_url,
    current_review_date.

Wrapper fields emitted:
    procurement_pricing_url, review_approval_process_url,
    review_sites_url, latest_announcement_date,
    latest_announcement_subject.

Usage:
    python3 scripts/adapters/va.py
    python3 scripts/adapters/va.py --fixture FILE
    python3 scripts/adapters/va.py --out scraped/VA.json
"""

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from scripts.adapters import base

STATE_CODE = "VA"
STATE_NAME = "Virginia"
SOURCE_URL = (
    "https://www.doe.virginia.gov/teaching-learning-assessment/"
    "instructional-resources-support/textbooks-instructional-materials"
)

# Month D, YYYY anywhere in a paragraph of bolded news copy.
ANNOUNCEMENT_DATE_RE = re.compile(
    r"(January|February|March|April|May|June|July|"
    r"August|September|October|November|December)\s+"
    r"(\d{1,2}),?\s+(\d{4})",
    re.IGNORECASE,
)

_MONTHS = ["january", "february", "march", "april", "may", "june",
           "july", "august", "september", "october", "november", "december"]

# Subject family keywords. The left side is the display subject; the
# right side is the set of terms we look for in link text and review
# h3 titles. VA posts announcements with phrasings like "Mathematics
# Textbook", "English Textbooks", "History and Social Science", and
# "Science Textbooks".
SUBJECT_FAMILIES = [
    ("English", ("english", "reading", "language arts", "literacy")),
    ("History & Social Science", ("history", "social science", "social studies")),
    ("Mathematics", ("mathematics", "math")),
    ("Science", ("science",)),
]


WARMUP_URL = "https://www.doe.virginia.gov/"


def fetch_html(url=SOURCE_URL):
    """Fetch the VDOE textbooks page with a warmup hit to the site root.

    doe.virginia.gov returns HTTP 403 to plain requests coming from
    GitHub's runners even with the full Chrome header set. The warmup
    pattern (used by TN and OK) opens a session, hits the domain root
    so any WAF challenge cookie lands, then requests the textbooks page
    with Sec-Fetch-Site: same-origin and a Referer pointing at the root.
    """
    return base.fetch_html(url, warmup_url=WARMUP_URL)


def _parse_announcement_date(text):
    """Return ISO date from the first Month D, YYYY in `text`, or None."""
    if not text:
        return None
    m = ANNOUNCEMENT_DATE_RE.search(text)
    if not m:
        return None
    try:
        month = _MONTHS.index(m.group(1).lower()) + 1
        day = int(m.group(2))
        year = int(m.group(3))
        return f"{year:04d}-{month:02d}-{day:02d}"
    except (ValueError, IndexError):
        return None


def _match_subject(text):
    """Return the first display subject whose family keywords appear in `text`."""
    low = text.lower()
    for subject, keywords in SUBJECT_FAMILIES:
        if any(kw in low for kw in keywords):
            return subject
    return None


def _collect_announcement(news_heading, source_url):
    """Walk the h3 block inside News & Announcements.

    Returns (title, inferred_subject, latest_iso_date, first_link_url)
    for the most recent announcement. Returns (None, None, None, None)
    if no news section exists.
    """
    if news_heading is None:
        return None, None, None, None

    # The first h3 under News & Announcements names the active review.
    h3 = None
    for sib in news_heading.find_next_siblings():
        name = getattr(sib, "name", None)
        if name in ("h1", "h2"):
            break
        if name == "h3":
            h3 = sib
            break
    if h3 is None:
        return None, None, None, None

    title = h3.get_text(" ", strip=True)
    subject = _match_subject(title)

    latest_date = None
    first_url = None
    for sib in h3.find_next_siblings():
        name = getattr(sib, "name", None)
        if name in ("h1", "h2", "h3"):
            break
        text = sib.get_text(" ", strip=True) if hasattr(sib, "get_text") else ""
        iso = _parse_announcement_date(text)
        if iso and (latest_date is None or iso > latest_date):
            latest_date = iso
        if first_url is None and hasattr(sib, "find_all"):
            for a in sib.find_all("a"):
                href = a.get("href", "") or ""
                if href and href.lower().startswith(("http", "/")):
                    # Resolve relative paths against source_url so the
                    # promoted dashboard URL is always absolute. Without
                    # this, a "/teaching-learning-.../mathematics-textbooks"
                    # href would land in the sheet as a broken relative path.
                    first_url = urljoin(source_url, href)
                    break

    return title, subject, latest_date, first_url


def parse(html, source_url=SOURCE_URL):
    """Parse the VDOE textbooks page and return a normalized dict."""
    soup = BeautifulSoup(html, "html.parser")
    scraped_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    all_links = base.all_links(soup, source_url)

    # Wrapper URLs pulled by anchor text. The side nav and the body
    # carry the same anchors; first_link_matching returns the first
    # match, which is fine because either copy resolves to the same URL.
    _, procurement_pricing_url = base.first_link_matching(
        all_links, "procurement", "pricing")
    _, review_approval_process_url = base.first_link_matching_any(
        all_links, [
            ["textbook review", "approval process"],
            ["textbook review & approval process"],
            ["textbook review", "approval"],
        ])
    _, review_sites_url = base.first_link_matching(
        all_links, "location of public review sites")

    # Current review announcement. Heading text is "News & Announcements".
    news_heading = base.find_heading_containing(
        soup, "news & announcements", tag_names=("h2", "h3"))
    if news_heading is None:
        news_heading = base.find_heading_containing(
            soup, "news and announcements", tag_names=("h2", "h3"))

    (announcement_title, announcement_subject,
     announcement_date, announcement_url) = _collect_announcement(
         news_heading, source_url)

    # Approved Textbooks & Materials block. Every subject in
    # SUBJECT_FAMILIES should appear as an h3-scoped bullet link.
    approved_heading = base.find_heading_containing(
        soup, "approved textbooks", tag_names=("h2", "h3"))

    cycles = []
    if approved_heading is not None:
        for link_text, href in base.collect_links_under(
                approved_heading, source_url,
                stop_tags=("h1", "h2")):
            subject = _match_subject(link_text)
            if not subject:
                continue
            # Keep one row per subject; the first link under each
            # bullet is the approved-materials landing page.
            if any(c["subject"] == subject for c in cycles):
                continue
            is_active = subject == announcement_subject
            cycles.append({
                "subject": subject,
                "approved_materials_url": href,
                "current_review_title": announcement_title if is_active else None,
                "current_review_url": announcement_url if is_active else None,
                "current_review_date": announcement_date if is_active else None,
            })

    # Sort alphabetically for stable diffs.
    cycles.sort(key=lambda c: c["subject"].lower())

    return {
        "state": STATE_CODE,
        "name": STATE_NAME,
        "source_url": source_url,
        "scraped_at": scraped_at,
        "cycle_count": len(cycles),
        "procurement_pricing_url": procurement_pricing_url,
        "review_approval_process_url": review_approval_process_url,
        "review_sites_url": review_sites_url,
        "latest_announcement_date": announcement_date,
        "latest_announcement_subject": announcement_subject,
        "latest_announcement_title": announcement_title,
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
