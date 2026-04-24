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
# textbook committee review" is the pending review list. "bid packet"
# titles mark an active Call for Bids and are the URL publishers need
# during an open submission window. On alabamaachieves.org the current
# bid packets live in a separate "Publisher's - Documents" section
# below the subject blocks, which is why we run a second page-wide scan
# (see _find_bid_packets_page_wide) rather than relying on subject
# siblings alone.
APPROVED_TERMS = ("state board approved", "approved/rejected")
PENDING_TERMS = ("submitted for state textbook committee review",)
BID_PACKET_TERMS = ("bid packet",)

# "Bid Packet" trailer we strip when deriving subject name from a bid
# packet anchor title. Order matters; longest variant first.
BID_PACKET_TITLE_SUFFIXES = (
    "letter and bid packet",
    "memo and bid packet",
    "bid packet",
)

# Words that should not count as part of a bid packet subject name.
# "k3", "k-3", "k12" are grade-band qualifiers on the ELA anchor.
_STOP_SUBJECT_TOKENS = {"k3", "k-3", "k12", "k-12", "grades", "grade"}


def fetch_html(url=SOURCE_URL):
    """Fetch the Alabama SDE page via the shared helper."""
    return base.fetch_html(url)


def _subject_from_heading(text):
    """If `text` matches 'Textbook by Subject - X', return X, else None."""
    m = SUBJECT_HEADING_RE.match(text.strip())
    return m.group(1).strip() if m else None


def _section_items(h3, source_url):
    """Collect anchor-and-description items for the section that starts at h3.

    Two shapes are supported:
      (a) Simple HTML used by the smoke test fixture. The h3 sits at the
          top level with <p> sibling elements following it.
      (b) The real alabamaachieves.org page uses a WPBakery VC_composer
          grid. The subject h3 is deeply nested inside a <div class="vc_row">
          wrapper, so h3.find_next_siblings() returns nothing. The data
          rows are vc_row siblings of the subject's vc_row, each with a
          two-column grid (left column holds the anchor, right column
          holds the description paragraph).
    """
    items = _section_items_siblings(h3, source_url)
    if items:
        return items
    vc_row = _enclosing_vc_row(h3)
    if vc_row is None:
        return items
    return _section_items_vcrow(vc_row, source_url)


def _section_items_siblings(h3, source_url):
    """Simple sibling walk used by the smoke test fixture."""
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


def _enclosing_vc_row(node):
    """Walk up parents until we find a div with class 'vc_row'. Returns
    None for top-level fixture HTML that has no WPBakery wrappers."""
    cur = node
    for _ in range(12):
        p = getattr(cur, "parent", None)
        if p is None:
            return None
        klass = p.get("class") or [] if hasattr(p, "get") else []
        if "vc_row" in klass:
            return p
        cur = p
    return None


def _section_items_vcrow(subject_row, source_url):
    """Walk vc_row siblings of `subject_row`, pairing each anchor in the
    left column with the description paragraph in the right column of
    the same row. Stops at the next vc_row that contains an h3 (which
    marks the start of the next subject or the start of the Adoption
    Process / Publishers blocks)."""
    items = []
    for sib in subject_row.find_next_siblings():
        if not hasattr(sib, "find_all"):
            continue
        # Any h3 inside this row means we've walked into the next section.
        if sib.find("h3") is not None:
            break
        anchors = [
            a for a in sib.find_all("a")
            if (a.get("href") or "").strip()
        ]
        # Description text lives in <p> elements that are NOT wrapping the
        # anchor. In the two-column grid, the left column's <p> holds the
        # anchor and the right column's <p> holds the description.
        descriptions = []
        for p in sib.find_all("p"):
            if p.find("a"):
                continue
            desc = p.get_text(" ", strip=True)
            if desc:
                descriptions.append(desc)
        row_items = []
        for a in anchors:
            href = a.get("href", "") or ""
            text = a.get("title") or a.get_text(" ", strip=True)
            row_items.append({
                "text": text,
                "href": urljoin(source_url, href),
                "description": "",
            })
        for i, it in enumerate(row_items):
            if i < len(descriptions):
                it["description"] = descriptions[i]
        items.extend(row_items)
    return items


def _classify(anchor_text):
    """Return (kind, (ay_start, ay_end)) for an anchor title.

    kind is 'approved', 'pending', 'bid_packet', or None. Year range
    is None if the title did not include a YYYY-YYYY fragment.
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
    for term in BID_PACKET_TERMS:
        if term in low:
            return "bid_packet", year
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


def _bid_packet_subject(title):
    """Derive the subject name from a bid packet anchor title.

    Alabama titles look like:
      "2026 - 2027 Digital Literacy and Computer Science Bid Packet"
      "ELA 2022-23 K3 Letter and Bid Packet"
      "2022-2023 Career and Technical Education Bid Packet"

    Strategy: strip the year range and the trailing "Bid Packet" phrase,
    then drop grade-band qualifiers. Returns the subject string or None
    when nothing usable is left.
    """
    if not title:
        return None
    text = title.strip()
    low = text.lower()
    for suffix in BID_PACKET_TITLE_SUFFIXES:
        idx = low.rfind(suffix)
        if idx != -1:
            text = text[:idx].strip()
            break
    # Remove year range anywhere in the remaining text.
    text = YEAR_RANGE_RE.sub("", text)
    # Alabama sometimes uses compact "2022-23" which YEAR_RANGE_RE
    # does not match; clean that too.
    text = re.sub(r"\b\d{4}\s*[\u2013\-]\s*\d{2}\b", "", text)
    # Drop grade-band tokens and stray punctuation.
    tokens = [t for t in re.split(r"\s+", text) if t]
    tokens = [t for t in tokens if t.lower().strip(",.()") not in _STOP_SUBJECT_TOKENS]
    cleaned = " ".join(tokens).strip(" -,")
    return cleaned or None


def _bid_packet_year(title):
    """Extract (ay_start, ay_end) from a bid packet anchor title.

    Supports both "2026-2027" and "2022-23" styles. Returns None when
    no year range can be found.
    """
    m = YEAR_RANGE_RE.search(title or "")
    if m:
        return (int(m.group(1)), int(m.group(2)))
    m = re.search(r"(\d{4})\s*[\u2013\-]\s*(\d{2})\b", title or "")
    if m:
        start = int(m.group(1))
        end = int(str(start)[:2] + m.group(2))
        return (start, end)
    return None


def _find_bid_packets_page_wide(soup, source_url, today_year):
    """Collect the newest ACTIVE bid packet anchor per subject.

    The live alabamaachieves.org page places current bid packets in a
    "Publisher's - Documents" section far from the subject h3 that each
    packet is actually about, so a subject-sibling walk misses them.
    This scan covers the whole page, filters to bid packets whose AY
    start is current or future (so multi-year-old packets do not look
    active), and keeps the newest packet per subject.

    Returns {subject_lower: {"subject": str, "href": str, "text": str,
    "ay_start": int, "ay_end": int}}.
    """
    out = {}
    for a in soup.find_all("a", href=True):
        href = (a.get("href") or "").strip()
        if not href:
            continue
        text = a.get("title") or a.get_text(" ", strip=True)
        if not text or "bid packet" not in text.lower():
            continue
        years = _bid_packet_year(text)
        if years is None:
            continue
        if years[0] < today_year:
            # Historical bid packet, not an open call anymore.
            continue
        subject = _bid_packet_subject(text)
        if not subject:
            continue
        key = subject.lower()
        prior = out.get(key)
        if prior is None or years[0] > prior["ay_start"]:
            out[key] = {
                "subject": subject,
                "href": urljoin(source_url, href),
                "text": text,
                "ay_start": years[0],
                "ay_end": years[1],
            }
    return out


_SUBJECT_STOP_WORDS = {"and", "the", "of", "for", "or", "in", "a", "an"}


def _subject_tokens(s):
    """Lowercase content words used to score subject overlap."""
    if not s:
        return set()
    raw = re.split(r"[^a-z0-9]+", s.lower())
    return {t for t in raw if t and t not in _SUBJECT_STOP_WORDS}


def _match_subject(cycles, bid_subject):
    """Find the cycle whose subject best matches `bid_subject`.

    Exact case-insensitive match wins. Otherwise we require at least 2
    overlapping content words so a single shared token like "science"
    does not wrongly map a Digital Literacy and Computer Science bid
    packet onto the Science cycle. Among remaining candidates, prefer
    more overlap and the longer (more specific) cycle subject. Returns
    None when nothing overlaps cleanly, so the caller creates a new
    cycle for the bid packet's subject.
    """
    target = (bid_subject or "").lower()
    if not target:
        return None
    for cycle in cycles:
        if cycle["subject"].lower() == target:
            return cycle
    target_tokens = _subject_tokens(bid_subject)
    if not target_tokens:
        return None
    best = None
    best_score = 0
    best_len = 0
    for cycle in cycles:
        s = cycle["subject"]
        cycle_tokens = _subject_tokens(s)
        overlap = len(target_tokens & cycle_tokens)
        if overlap < 2:
            continue
        if (overlap > best_score
                or (overlap == best_score and len(s) > best_len)):
            best = cycle
            best_score = overlap
            best_len = len(s)
    return best


def parse(html, source_url=SOURCE_URL):
    """Parse the Alabama SDE page and return a normalized dict."""
    soup = BeautifulSoup(html, "html.parser")
    scraped_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    all_links = base.all_links(soup, source_url)
    # Drop the in-page TOC anchors (luckywp-table-of-contents on the real
    # Alabama page). Those links share their anchor text with the real
    # section headings lower on the page, so without this filter the
    # wrapper searches below return #-fragments instead of actual PDFs.
    external_links = [
        (t, h) for (t, h) in all_links
        if h.split("#", 1)[0] and h.split("#", 1)[0] != source_url.rstrip("/")
        and h.split("#", 1)[0].rstrip("/") != source_url.rstrip("/")
    ]

    # Wrapper URLs. The adoption cycle PDF lives under an "Adoption
    # Process - Schedule" heading. The title "Alabama Courses of Study
    # Standards and State Textbook Adoption Cycle" is stable.
    _, adoption_cycle_schedule_url = base.first_link_matching(
        external_links, "courses of study", "textbook adoption cycle")
    _, adoption_process_forms_url = base.first_link_matching(
        external_links, "alabama state textbooks adoption process forms")
    _, publishers_documents_url = base.first_link_matching_any(
        external_links, [
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
        bid_packet = _latest(items, "bid_packet")

        if not approved and not pending and not bid_packet:
            # A subject heading with no trackable cycle (Health/PE had
            # only pre-2016 entries at the time of writing). Skip it so
            # we do not emit a stale record with no urls.
            continue

        # Reference record used to stamp the cycle's AY. The newest
        # year among bid_packet, pending, approved wins because it
        # signals the most recent step in that subject's cycle. When
        # tied, bid_packet > pending > approved because a bid packet
        # is the actionable signal for an open call.
        ref = approved
        for candidate in (pending, bid_packet):
            if not candidate:
                continue
            if ref is None or candidate["ay_start"] >= ref["ay_start"]:
                ref = candidate

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
            "call_for_bids_url": bid_packet["href"] if bid_packet else None,
        })

    # Second pass: the live page keeps current bid packets in a
    # "Publisher's - Documents" block below the subject blocks, so a
    # subject-sibling walk misses them. Scan the whole page for bid
    # packet anchors with a current-or-future AY and attach them to
    # the matching subject cycle. If a subject has only a bid packet
    # and no approved/pending list yet, create a cycle for it so the
    # actionable URL still shows up downstream.
    today_year = datetime.now(timezone.utc).year
    bid_packets = _find_bid_packets_page_wide(soup, source_url, today_year)
    for bp in bid_packets.values():
        existing = _match_subject(cycles, bp["subject"])
        if existing is not None:
            # Prefer the newest cycle-level URL only when the bid packet
            # is newer than whatever was in the subject block (which is
            # almost always the case when the subject block shows a
            # historical approved list).
            if (not existing.get("call_for_bids_url")
                    or bp["ay_start"] >= existing["ay_start"]):
                existing["call_for_bids_url"] = bp["href"]
                if bp["ay_start"] > existing["ay_start"]:
                    existing["ay_start"] = bp["ay_start"]
                    existing["ay_end"] = bp["ay_end"]
                    existing["cycle_label"] = (
                        f"{bp['ay_start']}-{bp['ay_end']} Adoption")
                    if (newest_ay_start is None
                            or bp["ay_start"] > newest_ay_start):
                        newest_ay_start = bp["ay_start"]
                        newest_cycle_label = existing["cycle_label"]
        else:
            # Subject not present in the subject h3 loop. Emit a
            # minimal cycle record so the publisher still sees the
            # active call.
            cycle_label = f"{bp['ay_start']}-{bp['ay_end']} Adoption"
            cycles.append({
                "subject": bp["subject"],
                "ay_start": bp["ay_start"],
                "ay_end": bp["ay_end"],
                "cycle_label": cycle_label,
                "approved_list_url": None,
                "approved_board_meeting_date": None,
                "pending_list_url": None,
                "pending_board_meeting_date": None,
                "call_for_bids_url": bp["href"],
            })
            if newest_ay_start is None or bp["ay_start"] > newest_ay_start:
                newest_ay_start = bp["ay_start"]
                newest_cycle_label = cycle_label

    has_active_cycle = any(c.get("call_for_bids_url") for c in cycles)

    return {
        "state": STATE_CODE,
        "name": STATE_NAME,
        "source_url": source_url,
        "scraped_at": scraped_at,
        "cycle_year": newest_ay_start,
        "cycle_label": newest_cycle_label,
        "cycle_count": len(cycles),
        "has_active_cycle": has_active_cycle,
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
