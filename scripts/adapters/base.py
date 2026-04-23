"""Shared utilities for state DOE adapters.

Every adapter in this folder shares three concerns: fetching HTML from a
state site that may be behind a WAF, walking anchor tags on the page, and
matching links by their surrounding text or heading. This module lifts
those out so each adapter only contains state-specific parsing.

Design notes

- BROWSER_HEADERS is the full Chrome header set. FL/TX/LA used to send a
  three-header minimum; TN and OK needed the full set to clear a WAF.
  Sending the full set everywhere costs nothing and makes adapters
  resilient if any state turns on a WAF later.

- fetch_html takes an optional `warmup_url`. With a warmup set, the
  function opens a requests.Session, hits the warmup URL first so any
  WAF cookie sticks, then requests the real URL with Sec-Fetch-Site:
  same-origin and a Referer header. This is the pattern proven against
  tn.gov and oklahoma.gov. Without a warmup, it does a single plain
  GET. Either way, TIMEOUT applies per request.

- Link helpers are parameterised on `base_url` so urljoin works without
  each adapter needing to hardcode SOURCE_URL inside the helper body.

- collect_bullets(... clean=True) reproduces the LA cleanup: strips
  trailing commas, periods, and a dangling " and" that Louisiana's list
  bullets tend to carry. Other states pass clean=False and get plain
  bullet text.
"""

import sys
import time
from urllib.parse import urljoin

try:
    import requests
    from bs4 import BeautifulSoup  # noqa: F401  (re-exported for adapters)
except ImportError:
    print("FATAL: requests and beautifulsoup4 required.", file=sys.stderr)
    print("Run: pip3 install requests beautifulsoup4", file=sys.stderr)
    sys.exit(2)

# --------------------------------------------------------------------------
# Fetch configuration
# --------------------------------------------------------------------------

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
    "sec-ch-ua": (
        '"Chromium";v="122", "Not(A:Brand";v="24", '
        '"Google Chrome";v="122"'
    ),
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
}

TIMEOUT = 30
MAX_ATTEMPTS = 3
RETRY_SLEEP_SECONDS = 3


def fetch_html(url, *, warmup_url=None, extra_headers=None, timeout=TIMEOUT):
    """Fetch a URL and return its HTML body.

    If `warmup_url` is set, uses a Session that first hits the warmup URL
    (so any WAF cookie lands in the session) and then requests the target
    with Sec-Fetch-Site: same-origin and a Referer pointing at the warmup.
    Retries transient connection errors up to MAX_ATTEMPTS with a short
    pause between attempts.

    If `warmup_url` is None, makes a single unretried GET.

    Raises the last requests.RequestException if every attempt fails.
    """
    headers = dict(BROWSER_HEADERS)
    if extra_headers:
        headers.update(extra_headers)

    if warmup_url is None:
        r = requests.get(url, headers=headers, timeout=timeout)
        r.raise_for_status()
        return r.text

    last_err = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            with requests.Session() as s:
                s.headers.update(headers)
                try:
                    s.get(warmup_url, timeout=timeout)
                except requests.RequestException:
                    # Warmup failure is non-fatal. The WAF may still let
                    # the real request through, so try it.
                    pass
                real_headers = {
                    "Sec-Fetch-Site": "same-origin",
                    "Referer": warmup_url,
                }
                r = s.get(url, headers=real_headers, timeout=timeout)
                r.raise_for_status()
                return r.text
        except requests.RequestException as e:
            last_err = e
            if attempt < MAX_ATTEMPTS:
                time.sleep(RETRY_SLEEP_SECONDS)
    raise last_err


# --------------------------------------------------------------------------
# Link utilities
# --------------------------------------------------------------------------

def all_links(soup, base_url):
    """Return list of (text, absolute_href) pairs for every anchor on the page.

    Anchors with empty hrefs are dropped. Hrefs are passed through urljoin
    so relative paths become absolute against `base_url`.
    """
    out = []
    for a in soup.find_all("a"):
        href = a.get("href", "") or ""
        if not href:
            continue
        txt = a.get_text(" ", strip=True)
        out.append((txt, urljoin(base_url, href)))
    return out


def first_link_matching(links, *needles, avoid=()):
    """Return first (text, href) where link text contains all `needles`.

    Case-insensitive substring match. Optional `avoid` is a tuple of
    strings; if any appears in the link text, the link is skipped. Useful
    for excluding side-nav duplicates that share phrasing with in-body links.

    Returns (None, None) if nothing matches.
    """
    for text, href in links:
        low = text.lower()
        if avoid and any(a.lower() in low for a in avoid):
            continue
        if all(n.lower() in low for n in needles):
            return text, href
    return None, None


def first_link_matching_any(links, groups, avoid=()):
    """Return first link whose text matches any of several needle groups.

    `groups` is a list. Each entry is itself a list of substrings that
    must all appear together in the link text. The first group that
    yields a hit wins. Lets an adapter try several phrasings in priority
    order (e.g. "out-of-cycle" then "out of cycle").
    """
    for group in groups:
        text, href = first_link_matching(links, *group, avoid=avoid)
        if href:
            return text, href
    return None, None


def find_heading_containing(soup, phrase,
                            tag_names=("h1", "h2", "h3", "h4")):
    """Return first heading whose text contains `phrase` (case-insensitive).

    Returns None if no heading matches.
    """
    needle = phrase.lower()
    for name in tag_names:
        for h in soup.find_all(name):
            if needle in h.get_text(" ", strip=True).lower():
                return h
    return None


def first_link_under(start, base_url, *, stop_tags=("h1", "h2"),
                     href_prefix=None, link_text_contains=None):
    """Return (text, absolute_href) for the first anchor after `start`.

    Walks `start`'s forward siblings and stops at the first sibling whose
    tag is in `stop_tags`. Anchors are converted to absolute URLs with
    urljoin.

    Optional filters:
      - `href_prefix`: only accept anchors whose absolute href begins
        with this string (useful for picking a specific domain, e.g.
        "https://airtable.com/").
      - `link_text_contains`: only accept anchors whose visible text
        contains this substring (case insensitive).

    Returns (None, None) if nothing matches.
    """
    needle = (link_text_contains or "").lower()
    for sib in start.find_next_siblings():
        name = getattr(sib, "name", None)
        if name in stop_tags:
            break
        if not hasattr(sib, "find_all"):
            continue
        for a in sib.find_all("a"):
            href = a.get("href", "") or ""
            if not href:
                continue
            absolute = urljoin(base_url, href)
            if href_prefix and not absolute.startswith(href_prefix):
                continue
            txt = a.get_text(" ", strip=True)
            if needle and needle not in txt.lower():
                continue
            return txt, absolute
    return None, None


def collect_links_under(start, base_url, stop_tags=("h1", "h2")):
    """Return every (text, absolute_href) pair under `start` in document order.

    Same traversal as `first_link_under` but accumulates every anchor
    rather than stopping at the first. Useful when a section has a list
    of related links (e.g. a bucket of rubric PDFs) and the adapter
    wants to route each one by its own text.
    """
    out = []
    for sib in start.find_next_siblings():
        name = getattr(sib, "name", None)
        if name in stop_tags:
            break
        if not hasattr(sib, "find_all"):
            continue
        for a in sib.find_all("a"):
            href = a.get("href", "") or ""
            if not href:
                continue
            txt = a.get_text(" ", strip=True)
            out.append((txt, urljoin(base_url, href)))
    return out


def link_under_heading(soup, base_url, heading_phrase, *,
                       href_prefix=None, stop_tags=("h1", "h2"),
                       link_text_contains=None):
    """Return the first anchor href located under a heading matching `heading_phrase`.

    Walks forward from the heading and stops at the first sibling whose
    tag is in `stop_tags` (the next section break). Useful when an
    anchor's own text is generic (e.g. a bare "form") but its section
    heading is distinctive.

    If `href_prefix` is set, only anchors whose absolute href starts with
    it qualify. Helps when a section contains multiple anchors and only
    one is the real target (e.g. an Airtable form link among descriptive
    links).

    If `link_text_contains` is set, the anchor's text must also contain
    that substring (case insensitive).

    Returns None if no match. (Returns only the href, not the text, for
    backward compatibility. Use `first_link_under` if you need both.)
    """
    heading = find_heading_containing(soup, heading_phrase,
                                      tag_names=("h2", "h3", "h4"))
    if not heading:
        return None
    _, href = first_link_under(
        heading, base_url,
        stop_tags=stop_tags,
        href_prefix=href_prefix,
        link_text_contains=link_text_contains,
    )
    return href


def collect_bullets(start, stop_tags, *, clean=False):
    """Collect plain-text <li> items that follow `start` up to any `stop_tags` tag.

    If `clean` is True, applies the Louisiana cleanup: strips trailing
    commas/periods and a dangling " and" at the end of the bullet text.
    The loop runs twice because an LA bullet can end with ", and" where
    stripping the "and" first leaves a stray comma.

    Returns a list of strings in document order.
    """
    bullets = []
    for sib in start.find_next_siblings():
        name = getattr(sib, "name", None)
        if name in stop_tags:
            break
        if not hasattr(sib, "find_all"):
            continue
        for li in sib.find_all("li"):
            txt = li.get_text(" ", strip=True)
            if clean:
                for _ in range(2):
                    txt = txt.rstrip(",.").strip()
                    if txt.lower().endswith(" and"):
                        txt = txt[:-4].strip()
            if txt:
                bullets.append(txt)
    return bullets
