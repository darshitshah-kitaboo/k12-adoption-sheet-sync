"""Florida adapter.

Scrapes the Florida Department of Education instructional materials page
and returns a normalized dict of active adoption cycles.

The page is structured as:
    <h2>2026-2027 Adoption Year</h2>
        <h3>K-12 Mathematics</h3>
            <ul> ... bid count, links, specs, dated adoption lists ... </ul>
        <h3>K-12 Computer Science</h3>
            ...
    <h2>2025-2026 Adoption Year</h2>
        <h3>9-12 Career and Technical Education</h3>
            ...

Older single-subject years use a different format where the subject is in
the h2 itself, e.g. "2023-2024 Adoption Year: K-12 Science". The parser
handles both shapes.

For each subject the adapter extracts:
    - subject name
    - adoption year range (ay_start, ay_end)
    - bid_count parsed from "N bids submitted for review"
    - latest adoption list update date and URL
    - specifications URL
    - publisher timeline URL
    - short bid report URL

The adapter does not compute statutory deadlines, contract dates, or other
fields that come from Florida statute rather than the DOE page. Those stay
hand-maintained on adoption_data.json and are merged in by the coordinator.

Usage:
    python3 scripts/adapters/fl.py                    # fetch live and print
    python3 scripts/adapters/fl.py --fixture FILE     # parse a local HTML file
    python3 scripts/adapters/fl.py --out scraped/FL.json
"""

import argparse
import json
import re
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from scripts.adapters import base

STATE_CODE = "FL"
STATE_NAME = "Florida"
SOURCE_URL = "https://www.fldoe.org/academics/standards/instructional-materials/"

# Matches either "2026-2027 Adoption Year" or "2023-2024 Adoption Year: K-12 Science".
# Accepts ASCII hyphen or Unicode en-dash between the years.
YEAR_HEADER_RE = re.compile(
    r"(\d{4})\s*[-\u2013]\s*(\d{4})\s+Adoption Year(?:\s*:\s*(.+))?$",
    re.IGNORECASE,
)

BID_COUNT_RE = re.compile(r"(\d+)\s+bids\s+submitted\s+for\s+review", re.IGNORECASE)
UPDATED_DATE_RE = re.compile(r"(?:Updated\s+)?(\d{1,2})/(\d{1,2})/(\d{2,4})")


def fetch_html(url=SOURCE_URL):
    """Fetch the FLDOE IM page through the shared helper.

    fldoe.org accepts plain scripted GETs, no warmup needed.
    """
    return base.fetch_html(url)


def _parse_date(text):
    """Pull the first M/D/YY or M/D/YYYY from text. Returns date or None."""
    m = UPDATED_DATE_RE.search(text or "")
    if not m:
        return None
    month, day, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if year < 100:
        year += 2000
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _walk_block(start_element, stop_tags=("h2", "h3")):
    """Walk siblings after start_element until a stop_tag is hit.

    Returns (combined_text, list_of_(link_text, href) tuples).

    This helper stays local to FL because it combines two walks (text
    aggregation and link collection) into one pass. Splitting it into
    `base.collect_links_under` plus a text-only walk would double the
    traversal for no real payoff.
    """
    text_parts = []
    links = []
    for sib in start_element.find_next_siblings():
        if getattr(sib, "name", None) in stop_tags:
            break
        if hasattr(sib, "get_text"):
            text_parts.append(sib.get_text(" ", strip=True))
        if hasattr(sib, "find_all"):
            for a in sib.find_all("a"):
                href = a.get("href", "") or ""
                txt = a.get_text(" ", strip=True)
                if href:
                    links.append((txt, href))
    return " ".join(text_parts), links


def _build_cycle(subject, ay_start, ay_end, text, links, source_url):
    """Produce a single cycle record from a subject block.

    URLs are absolutized against source_url so downstream consumers
    (dashboards, spreadsheets, emails) can use the hrefs directly without
    needing to know they came from the FLDOE site.
    """
    bid_count = None
    m = BID_COUNT_RE.search(text)
    if m:
        bid_count = int(m.group(1))

    latest_date = None
    latest_list_url = None
    spec_url = None
    timeline_url = None
    short_bid_url = None
    detailed_bid_url = None
    publisher_contact_urls = []

    for link_text, href in links:
        href = urljoin(source_url, href)  # normalize relative paths to absolute
        lower_text = link_text.lower()
        lower_href = href.lower()
        if "adoption list" in lower_text or "imal" in lower_href or "adoption-list" in lower_href:
            d = _parse_date(link_text)
            if d and (latest_date is None or d > latest_date):
                latest_date = d
                latest_list_url = href
        elif "specifications" in lower_text:
            spec_url = spec_url or href
        elif "publisher timeline" in lower_text or "timeline and checklist" in lower_text:
            timeline_url = timeline_url or href
        elif "short bid" in lower_text:
            short_bid_url = short_bid_url or href
        elif "detailed bid" in lower_text:
            detailed_bid_url = detailed_bid_url or href
        elif "publisher contact" in lower_text:
            publisher_contact_urls.append(href)

    return {
        "subject": subject.strip(),
        "ay_start": ay_start,
        "ay_end": ay_end,
        "bid_count": bid_count,
        "latest_list_date": latest_date.isoformat() if latest_date else None,
        "latest_list_url": latest_list_url,
        "specifications_url": spec_url,
        "timeline_url": timeline_url,
        "short_bid_url": short_bid_url,
        "detailed_bid_url": detailed_bid_url,
        "publisher_contact_urls": publisher_contact_urls,
    }


def parse(html, source_url=SOURCE_URL):
    """Parse FLDOE IM page HTML and return a normalized dict."""
    soup = BeautifulSoup(html, "html.parser")
    scraped_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    cycles = []

    for h2 in soup.find_all("h2"):
        year_text = h2.get_text(" ", strip=True)
        m = YEAR_HEADER_RE.match(year_text)
        if not m:
            continue
        ay_start = int(m.group(1))
        ay_end = int(m.group(2))
        inline_subject = m.group(3)

        if inline_subject:
            # "2023-2024 Adoption Year: K-12 Science" style. Whole block is one subject.
            text, links = _walk_block(h2, stop_tags=("h2",))
            cycles.append(_build_cycle(inline_subject, ay_start, ay_end, text, links, source_url))
        else:
            # Normal shape. Walk h3 subject subsections under this h2.
            for sib in h2.find_next_siblings():
                name = getattr(sib, "name", None)
                if name == "h2":
                    break
                if name == "h3":
                    subject = sib.get_text(" ", strip=True)
                    text, links = _walk_block(sib)
                    cycles.append(_build_cycle(subject, ay_start, ay_end, text, links, source_url))

    return {
        "state": STATE_CODE,
        "name": STATE_NAME,
        "source_url": source_url,
        "scraped_at": scraped_at,
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
