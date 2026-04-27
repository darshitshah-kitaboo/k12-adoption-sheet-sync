"""Oregon adapter (live verification).

Oregon runs state-adoption with a small number of subject cycles
already curated in adoption_data.json. This adapter's job is not to
parse cycle structure (the curation is good) but to confirm the DOE
hub is alive and capture any new framework, rubric, or RFP document
that lands on the page. promote_scraped Rule 1 then bumps last_verified
on the curated cycles when this adapter returns a non-empty snapshot.

The shared scripts.adapters.localctl module handles parsing. This
file only binds the state code, name, and source URL.

Usage:
    python3 scripts/adapters/or.py
    python3 scripts/adapters/or.py --fixture FILE
    python3 scripts/adapters/or.py --out scraped/OR.json
"""

import argparse
import json
from pathlib import Path

from scripts.adapters import localctl

STATE_CODE = "OR"
STATE_NAME = "Oregon"
SOURCE_URL = "https://www.oregon.gov/ode/educator-resources/teachingcontent/instructional-materials/pages/default.aspx"
WARMUP_URL = 'https://www.oregon.gov/'

# Whitelisted offsite hosts. Add EdReports, Airtable, or any external
# domain the state publishes adoption resources on.
EXTRA_HOSTS = ()


def fetch_html(url=SOURCE_URL):
    """Fetch the Oregon DOE IM hub through the shared helper."""
    return localctl.fetch_html(url, warmup_url=WARMUP_URL)


def parse(html, source_url=SOURCE_URL):
    """Delegate to the shared local-control parser.

    Local-control parsing works for live verification too: we only
    need cycle_count > 0 so the coordinator does not treat the run
    as a failure, and at least one document anchor on a state DOE
    page is a near-universal property.
    """
    return localctl.parse(
        html, source_url,
        state_code=STATE_CODE,
        state_name=STATE_NAME,
        extra_hosts=EXTRA_HOSTS,
    )


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
        print(f"Wrote {args.out} with {data['document_count']} docs")
    else:
        print(text)

    return data


if __name__ == "__main__":
    main()
