"""New Jersey adapter (local-control).

New Jersey is a local-control state. Districts pick instructional
materials. The state DOE publishes standards, frameworks, recommended
material lists, and review rubrics from a single curriculum hub. This
adapter scrapes that hub and emits one synthetic cycle per linked
document so the coordinator can diff what changes from run to run.

The actual parsing lives in scripts.adapters.localctl. This file only
binds the state code, name, and source URL.

Usage:
    python3 scripts/adapters/nj.py
    python3 scripts/adapters/nj.py --fixture FILE
    python3 scripts/adapters/nj.py --out scraped/NJ.json
"""

import argparse
import json
from pathlib import Path

from scripts.adapters import localctl

STATE_CODE = "NJ"
STATE_NAME = "New Jersey"
SOURCE_URL = "https://www.nj.gov/education/standards/"

# Whitelisted offsite hosts. Add Airtable, Smartsheet, EdReports, or any
# other domain the state publishes recommended materials on. localctl
# rejects offsite links by default to keep noise out of the change log.
EXTRA_HOSTS = ()


def fetch_html(url=SOURCE_URL):
    """Fetch the New Jersey curriculum hub through the shared helper."""
    return localctl.fetch_html(url)


def parse(html, source_url=SOURCE_URL):
    """Delegate to the shared local-control parser."""
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
