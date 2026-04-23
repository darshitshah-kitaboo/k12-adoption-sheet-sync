"""Texas adapter.

Scrapes the Texas SBOE "Current IMRA Cycle" page and returns a normalized
dict of subjects under review in the current Instructional Materials Review
and Approval (IMRA) cycle.

Texas is structured differently from Florida. FLDOE publishes a subject by
subject bid table per adoption year. Texas publishes a single cycle page
that lists subjects grouped into three tiers:
    - Full-subject, Tier one instructional materials
    - Partial-subject, Tier one instructional materials
    - Supplemental instructional materials

Each cycle also has shared cycle-level artifacts: the IMRA Process PDF,
the Request for Instructional Materials (RFIM) PDF, a Suitability Rubric,
and a set of Quality Rubrics keyed by subject area.

To stay compatible with the coordinator's diffing model, every subject is
emitted as its own cycle record. The cycle-wide artifacts (process, RFIM,
suitability) are copied onto every record so consumers can treat each row
as self-contained. Quality rubrics are matched to subjects by keyword
(math, ELAR, SLAR, CTE, fine arts) so downstream tools can jump straight
from a subject to its rubric.

The adapter does not attempt to track IMRA publisher submissions or SBOE
meeting votes. Those live in separate PDFs linked from other SBOE pages
and would need their own adapters.

Usage:
    python3 scripts/adapters/tx.py                    # fetch live and print
    python3 scripts/adapters/tx.py --fixture FILE     # parse a local HTML file
    python3 scripts/adapters/tx.py --out scraped/TX.json
"""

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from bs4 import BeautifulSoup

from scripts.adapters import base

STATE_CODE = "TX"
STATE_NAME = "Texas"
SOURCE_URL = "https://sboe.texas.gov/state-board-of-education/imra/current-imra-cycle"

# "IMRA Cycle 2026" or "IMRA 2026" etc.
CYCLE_YEAR_RE = re.compile(r"IMRA\s+Cycle\s+(\d{4})", re.IGNORECASE)

# Tier keys we emit on cycle records. Keeping them short and stable so
# downstream filters do not break when the page wording drifts.
TIER_FULL = "full-subject-tier-one"
TIER_PARTIAL = "partial-subject-tier-one"
TIER_SUPPLEMENTAL = "supplemental"

# Keyword map used to attach a quality rubric to a subject. The adapter
# lowercases both sides before matching. Families are intentionally broad
# so umbrella terms route correctly: the "reading" family covers ELAR,
# SLAR, RLA, phonics, and generic "language arts" so that a K-3 phonics
# subject still picks up ELAR/SLAR K-3 rubrics, and a supplemental
# "ELAR and SLAR" subject still picks up the "Supplemental RLA" rubric.
RUBRIC_KEYWORDS = {
    "reading": ["elar", "slar", "rla", "language arts", "phonics", "reading"],
    "math": ["math"],
    "fine arts": ["fine arts"],
    "cte": ["cte", "career and technical"],
}


def fetch_html(url=SOURCE_URL):
    """Fetch the SBOE current cycle page through the shared helper.

    sboe.texas.gov accepts plain scripted GETs, no warmup needed.
    """
    return base.fetch_html(url)


def _match_rubric(subject, rubric_links):
    """Return the rubric href that best matches a subject, or None.

    Matches by keyword family. A subject can match multiple rubrics
    (e.g. "K-5 ELAR and SLAR" hits both ELAR and SLAR rubrics) so we
    return every rubric whose family appears in the subject text.
    """
    subj_lower = subject.lower()
    matches = []
    for _family, keywords in RUBRIC_KEYWORDS.items():
        if not any(kw in subj_lower for kw in keywords):
            continue
        for text, href in rubric_links:
            tl = text.lower()
            if any(kw in tl for kw in keywords):
                if href not in matches:
                    matches.append(href)
    return matches


def parse(html, source_url=SOURCE_URL):
    """Parse SBOE IMRA current cycle HTML and return a normalized dict."""
    soup = BeautifulSoup(html, "html.parser")
    scraped_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    # Pull cycle year from any heading or paragraph mentioning "IMRA Cycle YYYY".
    page_text = soup.get_text(" ", strip=True)
    m = CYCLE_YEAR_RE.search(page_text)
    cycle_year = int(m.group(1)) if m else None

    # Cycle-wide artifacts. We look them up by their heading or anchor text so
    # a page tweak that reorders sections still works.
    process_heading = base.find_heading_containing(
        soup, "imra process", tag_names=("h2", "h3", "h4"))
    process_url = None
    if process_heading:
        _, process_url = base.first_link_under(
            process_heading, source_url,
            stop_tags=("h2", "h3"),
            link_text_contains="imra process")

    rfim_heading = base.find_heading_containing(
        soup, "request for instructional materials",
        tag_names=("h2", "h3"))
    rfim_url = None
    if rfim_heading:
        _, rfim_url = base.first_link_under(
            rfim_heading, source_url,
            stop_tags=("h2",),
            link_text_contains="rfim")

    # Rubric section. Suitability rubric is a single link. Quality rubrics
    # are grouped into two h4 buckets: tier-one (full or partial) and
    # supplemental. Grouping them separately avoids matching a supplemental
    # math rubric against a tier-one math subject just because both say
    # "math".
    rubrics_heading = base.find_heading_containing(
        soup, "rubrics", tag_names=("h2", "h3"))
    suitability_url = None
    tier_one_rubric_links = []
    supplemental_rubric_links = []
    if rubrics_heading:
        # Suitability: first link under the h3 "Suitability Rubric" subsection.
        suit_h = base.find_heading_containing(
            soup, "suitability rubric", tag_names=("h3", "h4"))
        if suit_h:
            _, suitability_url = base.first_link_under(
                suit_h, source_url, stop_tags=("h2", "h3", "h4"))

        # Quality rubrics live after an h3 "Quality Rubrics" heading. Walk
        # its h4 children and route links into the right bucket.
        qual_h = base.find_heading_containing(
            soup, "quality rubrics", tag_names=("h3", "h4"))
        if qual_h:
            for sib in qual_h.find_next_siblings():
                name = getattr(sib, "name", None)
                if name == "h2" or name == "h3":
                    break
                if name != "h4":
                    continue
                title = sib.get_text(" ", strip=True).lower()
                if "supplemental" in title:
                    bucket = supplemental_rubric_links
                else:
                    bucket = tier_one_rubric_links
                for text, href in base.collect_links_under(
                        sib, source_url, stop_tags=("h2", "h3", "h4")):
                    if "rubric" in text.lower():
                        bucket.append((text, href))

    # Subject lists. We walk each h4 under the RFIM section and categorize
    # by its heading.
    cycles = []
    if rfim_heading:
        tier_headings = []
        for sib in rfim_heading.find_next_siblings():
            name = getattr(sib, "name", None)
            if name == "h2":
                break
            if name == "h4":
                tier_headings.append(sib)

        for h4 in tier_headings:
            title = h4.get_text(" ", strip=True).lower()
            if "supplemental" in title:
                tier = TIER_SUPPLEMENTAL
            elif "partial" in title:
                tier = TIER_PARTIAL
            elif "full" in title:
                tier = TIER_FULL
            else:
                # Unknown heading shape. Skip rather than misclassify.
                continue

            for subject in base.collect_bullets(h4, ("h2", "h3", "h4")):
                pool = (supplemental_rubric_links
                        if tier == TIER_SUPPLEMENTAL
                        else tier_one_rubric_links)
                subj_rubric_urls = _match_rubric(subject, pool)
                cycles.append({
                    "subject": subject,
                    "tier": tier,
                    "ay_start": cycle_year,
                    "ay_end": cycle_year + 1 if cycle_year else None,
                    "cycle_label": f"IMRA Cycle {cycle_year}" if cycle_year else None,
                    "rfim_url": rfim_url,
                    "process_url": process_url,
                    "suitability_rubric_url": suitability_url,
                    "quality_rubric_urls": subj_rubric_urls,
                })

    return {
        "state": STATE_CODE,
        "name": STATE_NAME,
        "source_url": source_url,
        "scraped_at": scraped_at,
        "cycle_year": cycle_year,
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
