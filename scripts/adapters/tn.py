"""Tennessee adapter.

Scrapes the Tennessee Textbook and Instructional Materials Quality
Commission "Publisher Information" page and returns the current
substitution cycle window with submission deadline and the key
publisher-facing document URLs.

Tennessee runs two parallel processes:
    1. A six-year rotating adoption schedule (Schedules A through F).
       Each calendar year, one schedule is up for review. This data
       lives on a separate "Schedule F Textbook Adoption Cycle" page
       and is not yet parsed here.
    2. A substitution window every January. Publishers with already
       listed books can request a revision be substituted. The publisher
       page captures this window, and this adapter parses that.

For the substitution cycle the adapter extracts:
    - Cycle year (e.g. Cycle 2027 for the March 2027 meeting)
    - Submission deadline date
    - Meeting month and year
    - Substitution template URL
    - Commission rule URL
    - Publisher distribution list form URL

One cycle record is emitted with subject "All subjects (substitution
window)" because the substitution process is not subject-scoped. When
Schedule F parsing is added, per-subject adoption cycles will be
emitted alongside this one.

Usage:
    python3 scripts/adapters/tn.py
    python3 scripts/adapters/tn.py --fixture FILE
    python3 scripts/adapters/tn.py --out scraped/TN.json
"""

import argparse
import json
import re
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    print("FATAL: requests and beautifulsoup4 required.", file=sys.stderr)
    print("Run: pip3 install requests beautifulsoup4", file=sys.stderr)
    sys.exit(2)

STATE_CODE = "TN"
STATE_NAME = "Tennessee"
SOURCE_URL = "https://www.tn.gov/textbook-commission/textbook-information-for-publishers.html"
WARMUP_URL = "https://www.tn.gov/"

# tn.gov sits behind a WAF that resets connections on requests that look
# scripted. An earlier minimal header set (UA + Accept + Accept-Language)
# was rejected with ConnectionReset on the GitHub runner. The headers below
# mirror what Chrome actually sends, including Sec-Fetch-* and client hints.
# Keep this list in lockstep with a recent stable Chrome release; if tn.gov
# starts rejecting again, refresh the UA and sec-ch-ua values first.
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,image/apng,*/*;q=0.8,"
        "application/signed-exchange;v=b3;q=0.7"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Cache-Control": "max-age=0",
    "Connection": "keep-alive",
    "DNT": "1",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-User": "?1",
    "Sec-Fetch-Dest": "document",
    "sec-ch-ua": '"Chromium";v="122", "Not(A:Brand";v="24", "Google Chrome";v="122"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
}
TIMEOUT = 30
# How many times to retry on transient network failures. tn.gov occasionally
# resets the first connection but accepts the second after a short pause.
MAX_ATTEMPTS = 3
RETRY_SLEEP_SECONDS = 3

# "Cycle 2027 for March 2027 Textbook Commission Meeting"
CYCLE_YEAR_RE = re.compile(
    r"Cycle\s+(\d{4})\s+for\s+(January|February|March|April|May|June|"
    r"July|August|September|October|November|December)\s+(\d{4})",
    re.IGNORECASE,
)

# "Deadline for Submission via email to ... is December 31, 2026"
DEADLINE_RE = re.compile(
    r"Deadline\s+for\s+Submission[^)]*?is\s+"
    r"(January|February|March|April|May|June|July|August|September|"
    r"October|November|December)\s+(\d{1,2}),?\s+(\d{4})",
    re.IGNORECASE,
)

_MONTH_IDX = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june",
     "july", "august", "september", "october", "november", "december"], start=1)}


def fetch_html(url=SOURCE_URL):
    """Fetch the live TN publisher info page.

    tn.gov drops connections that look scripted, so we:
      1. Open a Session (persists cookies the WAF may set on the warmup).
      2. Hit the root domain first with Sec-Fetch-Site: none.
      3. Follow up with the real request using Sec-Fetch-Site: same-origin
         and a Referer, matching what a browser sends after a homepage hit.
      4. Retry on ConnectionError with a short pause. The first connection
         to tn.gov sometimes resets and the second goes through.
    Raises the last exception if every attempt fails.
    """
    last_err = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            with requests.Session() as s:
                s.headers.update(BROWSER_HEADERS)
                # Warmup. Failure here is non-fatal; the real request still
                # tries. The warmup hit is what establishes any WAF cookie.
                try:
                    s.get(WARMUP_URL, timeout=TIMEOUT)
                except requests.RequestException:
                    pass
                # Real request looks like a link click from tn.gov homepage.
                real_headers = {
                    "Sec-Fetch-Site": "same-origin",
                    "Referer": WARMUP_URL,
                }
                r = s.get(url, headers=real_headers, timeout=TIMEOUT)
                r.raise_for_status()
                return r.text
        except requests.RequestException as e:
            last_err = e
            if attempt < MAX_ATTEMPTS:
                time.sleep(RETRY_SLEEP_SECONDS)
    raise last_err


def _first_link_matching(links, *needles):
    """Return the first (text, href) whose text contains all needles (case insensitive)."""
    for text, href in links:
        low = text.lower()
        if all(n.lower() in low for n in needles):
            return text, href
    return None, None


def _all_links(soup, base_url):
    """Collect (text, absolute_href) pairs for every anchor on the page."""
    out = []
    for a in soup.find_all("a"):
        href = a.get("href", "") or ""
        if not href:
            continue
        txt = a.get_text(" ", strip=True)
        out.append((txt, urljoin(base_url, href)))
    return out


def parse(html, source_url=SOURCE_URL):
    """Parse TN publisher info HTML and return a normalized dict."""
    soup = BeautifulSoup(html, "html.parser")
    scraped_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    # Focus on the main article so we do not pick up matches from the header
    # or footer nav (which also mentions calendar years).
    article = soup.find("article") or soup
    article_text = article.get_text(" ", strip=True)

    cycle_year = None
    commission_meeting = None
    ay_start = ay_end = None
    cycle_label = None
    m = CYCLE_YEAR_RE.search(article_text)
    if m:
        cycle_year = int(m.group(1))
        meeting_month = m.group(2).title()
        meeting_year = int(m.group(3))
        commission_meeting = f"{meeting_month} {meeting_year}"
        # Academic year window is the year BEFORE the cycle meeting through
        # the cycle year itself. Cycle 2027 runs for AY 2026-2027.
        ay_start = cycle_year - 1
        ay_end = cycle_year
        cycle_label = f"Cycle {cycle_year} Substitution Window"

    submission_deadline = None
    dm = DEADLINE_RE.search(article_text)
    if dm:
        month = _MONTH_IDX.get(dm.group(1).lower())
        day = int(dm.group(2))
        year = int(dm.group(3))
        if month:
            try:
                submission_deadline = date(year, month, day).isoformat()
            except ValueError:
                submission_deadline = None

    links = _all_links(soup, source_url)
    _, substitution_template_url = _first_link_matching(
        links, "substitution", "template")
    _, substitution_rule_url = _first_link_matching(
        links, "0520-05-01")
    _, publisher_distr_list_url = _first_link_matching(
        links, "this form")
    _, schedule_f_url = _first_link_matching(
        links, "schedule f")
    _, official_list_url = _first_link_matching(
        links, "official lists")
    _, adoption_process_url = _first_link_matching(
        links, "adoption process")

    cycles = []
    if cycle_year:
        cycles.append({
            "subject": "All subjects (substitution window)",
            "ay_start": ay_start,
            "ay_end": ay_end,
            "cycle_label": cycle_label,
            "commission_meeting": commission_meeting,
            "submission_deadline": submission_deadline,
            "substitution_template_url": substitution_template_url,
            "substitution_rule_url": substitution_rule_url,
            "publisher_distr_list_url": publisher_distr_list_url,
        })

    return {
        "state": STATE_CODE,
        "name": STATE_NAME,
        "source_url": source_url,
        "scraped_at": scraped_at,
        "cycle_year": cycle_year,
        "cycle_label": cycle_label,
        "cycle_count": len(cycles),
        "schedule_f_url": schedule_f_url,
        "official_list_url": official_list_url,
        "adoption_process_url": adoption_process_url,
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
