"""Indiana adapter (live verification).

Indiana state-adoption coverage. The cycles in adoption_data.json are
hand-curated; this adapter keeps last_verified fresh by parsing the
DOE hub for any new framework, rubric, or HQIM document. promote_scraped
Rule 1 then bumps last_verified across the curated cycles.

Usage:
    python3 scripts/adapters/in.py
    python3 scripts/adapters/in.py --fixture FILE
    python3 scripts/adapters/in.py --out scraped/IN.json
"""

import argparse
import json
from pathlib import Path

from scripts.adapters import localctl

STATE_CODE = "IN"
STATE_NAME = "Indiana"
SOURCE_URL = "https://www.in.gov/doe/students/high-quality-curricular-materials-advisory-lists/"
WARMUP_URL = None

EXTRA_HOSTS = ()


def fetch_html(url=SOURCE_URL):
    """Fetch the Indiana DOE IM hub."""
    return localctl.fetch_html(url, warmup_url=WARMUP_URL)


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
