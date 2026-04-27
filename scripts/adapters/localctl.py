"""Shared adapter for local-control states.

Local-control states do not run a state-level adoption cycle the way
Florida or Texas do. Districts choose their own materials. What state
DOEs *do* publish is a hub of curriculum frameworks, recommended
material lists, review rubrics, HQIM portals, and procurement
guidance. From a publisher's standpoint that hub is the closest thing
to a state adoption signal: when a new framework PDF lands, when a
recommended-materials Airtable goes live, when a review rubric gets
revised, those are the events worth tracking.

This module fetches a state's curriculum/IM landing page and emits one
synthetic "cycle" record per linked document (PDF, DOCX, XLSX, or
external page hosted off the DOE domain). The coordinator
(run_adapters.py) treats cycle_count == 0 as a hard failure, so even
quiet states need at least one record to keep the pipeline green.

Each per-state stub in scripts/adapters/<code>.py imports this module
and supplies a STATE_CODE, STATE_NAME, and SOURCE_URL. Optional knobs:

    WARMUP_URL     Pass through to base.fetch_html for WAF'd sites.
    DOCUMENT_EXTS  Override the default set of trackable extensions.
    EXTRA_HOSTS    Whitelist additional offsite hosts (e.g. an
                   Airtable form, a separate review portal) that
                   should count as tracked documents.

The shared parser is intentionally permissive. A local-control state
might publish 5 docs or 200; we capture them all and let the diff
layer in run_adapters.py surface what changed run over run.

Usage from a state stub:

    from scripts.adapters import localctl

    STATE_CODE = "NY"
    STATE_NAME = "New York"
    SOURCE_URL = "https://www.nysed.gov/curriculum-instruction"

    def fetch_html(url=SOURCE_URL):
        return localctl.fetch_html(url)

    def parse(html, source_url=SOURCE_URL):
        return localctl.parse(
            html, source_url,
            state_code=STATE_CODE, state_name=STATE_NAME,
        )
"""

import hashlib
import re
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from scripts.adapters import base

# Default file extensions we treat as trackable documents. Anything in
# this set gets emitted as its own synthetic cycle. Adapters can extend
# the set when a state publishes resources in unusual formats.
DEFAULT_DOCUMENT_EXTS = (
    ".pdf",
    ".docx", ".doc",
    ".xlsx", ".xls",
    ".pptx", ".ppt",
    ".csv",
)

# Keyword fallback for HTML pages. Many state DOEs publish frameworks,
# rubrics, and HQIM lists as plain HTML pages, not PDFs. When an anchor
# does not have a document extension we still keep it if its visible
# text matches a strong adoption-relevant phrase. The keywords are case-
# insensitive and matched against the trimmed anchor text only.
ADOPTION_KEYWORDS = (
    "framework",
    "rubric",
    "standards",
    "approved list",
    "approved materials",
    "hqim",
    "high-quality instructional materials",
    "high quality instructional materials",
    "recommended materials",
    "recommended list",
    "curriculum guide",
    "curriculum framework",
    "instructional materials",
    "review tool",
    "evaluation criteria",
    "adoption schedule",
    "review cycle",
    "selection criteria",
)

# Bucket every captured link into one of these categories by inspecting
# its visible text. The category becomes the synthetic cycle's "subject"
# so downstream consumers can group by intent rather than file name.
# Order matters: the first matching bucket wins.
CATEGORY_PATTERNS = (
    ("HQIM",          re.compile(r"\b(hqim|high[- ]quality|approved (?:list|materials)|recommended (?:list|materials))\b", re.I)),
    ("Framework",     re.compile(r"\b(framework|standards?|curriculum guide)\b", re.I)),
    ("Rubric",        re.compile(r"\b(rubric|review tool|evaluation criteria|edreports)\b", re.I)),
    ("Adoption",      re.compile(r"\b(adoption|call for bids?|invitation to submit|rfp|rfb|bid packet)\b", re.I)),
    ("Review",        re.compile(r"\b(review|vetting|advisory committee)\b", re.I)),
    ("Guidance",      re.compile(r"\b(guidance|guide|handbook|procurement|selection)\b", re.I)),
    ("Subject",       re.compile(r"\b(math|reading|english|ela|elar|literacy|science|social studies|history|civics|world language|fine arts|computer science|cte|career|health|physical education)\b", re.I)),
)
DEFAULT_CATEGORY = "General"

# Stop-words that strip a link from consideration: privacy policy,
# accessibility, sitemap, contact, etc. These appear in every
# state DOE footer and are not adoption-relevant.
NOISE_TEXTS = (
    "privacy",
    "accessibility",
    "sitemap",
    "contact us",
    "feedback",
    "subscribe",
    "follow us",
    "facebook",
    "twitter",
    "instagram",
    "youtube",
    "linkedin",
    "rss",
)


def fetch_html(url, *, warmup_url=None):
    """Thin wrapper around base.fetch_html.

    Local-control adapters can pass a warmup URL when the state's CDN
    or WAF requires a session-cookie warmup before the real GET.
    """
    return base.fetch_html(url, warmup_url=warmup_url)


def _is_document_link(href, document_exts, extra_hosts, *, anchor_text=""):
    """True if href points to a trackable document or whitelisted host.

    Three independent paths qualify a link:
      1. The path ends in a document extension (.pdf, .docx, etc.)
      2. The host is in the EXTRA_HOSTS whitelist (e.g. airtable.com)
      3. The visible anchor text contains an ADOPTION_KEYWORDS phrase.
         This is the HTML-page fallback for state DOEs that publish
         their frameworks and HQIM lists as web pages, not PDFs.
    """
    parsed = urlparse(href)
    path = (parsed.path or "").lower()
    for ext in document_exts:
        if path.endswith(ext):
            return True
    host = (parsed.netloc or "").lower()
    for h in extra_hosts:
        if h and h.lower() in host:
            return True
    text_low = (anchor_text or "").lower()
    if text_low:
        for kw in ADOPTION_KEYWORDS:
            if kw in text_low:
                return True
    return False


def _is_noise(text):
    """True if the link text matches a footer/social/utility pattern."""
    low = (text or "").strip().lower()
    if not low:
        return True
    for n in NOISE_TEXTS:
        if low == n or low.startswith(n + " ") or low.endswith(" " + n):
            return True
    return False


def _categorize(text):
    """Bucket a link into a coarse subject category by its visible text."""
    if not text:
        return DEFAULT_CATEGORY
    for label, pattern in CATEGORY_PATTERNS:
        if pattern.search(text):
            return label
    return DEFAULT_CATEGORY


def _nearest_section_heading(anchor, max_walk=8):
    """Walk up and back from an anchor to find its enclosing section title.

    Looks for the closest preceding h1-h4 within `max_walk` ancestors. If
    none found, returns None. Used purely as descriptive context for the
    synthetic cycle; not load-bearing.
    """
    node = anchor
    walked = 0
    while node and walked < max_walk:
        for sib in node.find_previous_siblings():
            name = getattr(sib, "name", None)
            if name in ("h1", "h2", "h3", "h4"):
                return sib.get_text(" ", strip=True)
        node = node.parent
        walked += 1
    return None


def _page_title(soup):
    """Pull the page's primary heading. Prefer h1, fall back to <title>."""
    h1 = soup.find("h1")
    if h1:
        text = h1.get_text(" ", strip=True)
        if text:
            return text
    t = soup.find("title")
    if t:
        return t.get_text(" ", strip=True)
    return None


def _content_hash(documents):
    """Stable hash of the document set so callers can detect any change.

    Uses the (text, href) pairs sorted for determinism. The page itself
    might shuffle blocks day to day; we only flag a change when the
    underlying link set actually moves.
    """
    pairs = sorted({(d["title"], d["url"]) for d in documents})
    h = hashlib.sha256()
    for t, u in pairs:
        h.update(t.encode("utf-8", errors="replace"))
        h.update(b"\x00")
        h.update(u.encode("utf-8", errors="replace"))
        h.update(b"\n")
    return h.hexdigest()[:16]


def parse(html, source_url, *,
          state_code, state_name,
          document_exts=DEFAULT_DOCUMENT_EXTS,
          extra_hosts=()):
    """Parse a local-control DOE page and return a normalized snapshot.

    Walks every anchor on the page, keeps the ones that point at a
    document or whitelisted external host, and emits one synthetic
    cycle record per kept link. The wrapper carries a content_hash and
    the page title so downstream tools can spot change at the page level
    even before drilling into individual links.
    """
    soup = BeautifulSoup(html, "html.parser")
    scraped_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    page_title = _page_title(soup)

    seen_urls = set()
    documents = []
    for a in soup.find_all("a"):
        href = (a.get("href") or "").strip()
        if not href or href.startswith("#") or href.lower().startswith("mailto:"):
            continue
        absolute = urljoin(source_url, href)
        if absolute in seen_urls:
            continue
        text = a.get_text(" ", strip=True)
        if _is_noise(text):
            continue
        if not _is_document_link(absolute, document_exts, extra_hosts,
                                  anchor_text=text):
            continue
        seen_urls.add(absolute)

        section = _nearest_section_heading(a)
        category = _categorize(text) if text else _categorize(section or "")

        documents.append({
            "title": text or "(untitled)",
            "url": absolute,
            "section": section,
            "category": category,
        })

    # Sort for stable diffs. URL sort is more reliable than title sort
    # because state pages frequently reword anchor text without changing
    # the underlying file.
    documents.sort(key=lambda d: d["url"].lower())

    cycles = []
    for i, doc in enumerate(documents, start=1):
        cycles.append({
            # Subject lines up with the per-doc category, not the state
            # name. promote_scraped matches scraped cycles to
            # adoption_data cycles by subject; using the category lets a
            # single placeholder Monitoring cycle in adoption_data line
            # up against any tracked doc.
            "subject": doc["category"],
            "title": doc["title"],
            "section": doc["section"],
            "document_url": doc["url"],
            "document_index": i,
        })

    return {
        "state": state_code,
        "name": state_name,
        "source_url": source_url,
        "scraped_at": scraped_at,
        "page_title": page_title,
        "document_count": len(documents),
        "content_hash": _content_hash(documents),
        # Local-control states are never in an active state-level call.
        # The flag is emitted as False for shape parity with the state-
        # adoption adapters; promote_scraped Rule 3 needs an ISO dl to
        # flip ac True so this stays inert by design.
        "has_active_cycle": False,
        "call_for_bids_url": None,
        "cycle_count": len(cycles),
        "cycles": cycles,
    }
