"""
Validate every URL in registry/sources.json by fetching it live.

Runs in GitHub Actions (where outbound .gov access works) and locally on any
machine with internet. Produces a verification report and optionally stamps
last_verified on each state entry when all its URLs return 200.

Exit codes:
  0   all priority-1 (active adoption) state URLs returned 200
  1   one or more priority-1 URLs failed
  2   unexpected error (registry file missing, malformed, etc.)

Outputs:
  registry/verification_report.json   per-URL status, timestamp, response time
  registry/sources.json (in-place)    last_verified stamped on each state that
                                       had every non-null URL return 200

Usage:
  python scripts/validate_registry.py           # verify + stamp
  python scripts/validate_registry.py --dry     # verify without stamping
  python scripts/validate_registry.py --quiet   # only print failures
"""

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
import ssl

ROOT = Path(__file__).resolve().parent.parent
REGISTRY = ROOT / "registry" / "sources.json"
REPORT = ROOT / "registry" / "verification_report.json"

# Honest UA so state webmasters can trace traffic back to us if they wonder.
USER_AGENT = (
    "Mozilla/5.0 (compatible; KitabooAdoptionScraper/1.0; "
    "+https://kitaboo.com; adoption-intel@kitaboo.com)"
)

TIMEOUT = 20
MAX_WORKERS = 8  # low enough to stay polite to small state DOE servers


def fetch_status(url, timeout=TIMEOUT):
    """Return (status_code, final_url, elapsed_ms, error_msg).

    status_code is None on network failure. Final URL is the landing page after
    redirects. Elapsed time helps spot pages that are nominally up but so slow
    they'd break a nightly scraper run.
    """
    ctx = ssl.create_default_context()
    # Some state sites serve expired certs. Do not fail validation for that.
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    started = time.perf_counter()
    try:
        req = Request(url, headers={"User-Agent": USER_AGENT})
        with urlopen(req, timeout=timeout, context=ctx) as r:
            elapsed = int((time.perf_counter() - started) * 1000)
            return r.status, r.geturl(), elapsed, None
    except HTTPError as e:
        elapsed = int((time.perf_counter() - started) * 1000)
        return e.code, url, elapsed, f"HTTP {e.code}: {e.reason}"
    except URLError as e:
        elapsed = int((time.perf_counter() - started) * 1000)
        return None, url, elapsed, f"URLError: {e.reason}"
    except Exception as e:
        elapsed = int((time.perf_counter() - started) * 1000)
        return None, url, elapsed, f"{type(e).__name__}: {e}"


def collect_urls(registry):
    """Flatten the registry into one list of (state_code, source_type, url) tuples."""
    jobs = []
    for state in registry.get("states", []):
        code = state["code"]
        sources = state.get("sources", {})
        for stype, info in sources.items():
            if stype == "secondary":
                for i, sec in enumerate(info or []):
                    if sec.get("url"):
                        jobs.append((code, f"secondary[{i}]", sec["url"]))
                continue
            url = (info or {}).get("url")
            if url:
                jobs.append((code, stype, url))
    return jobs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true",
                    help="Validate without writing last_verified stamps")
    ap.add_argument("--quiet", action="store_true",
                    help="Only print failures, suppress per-URL 200s")
    args = ap.parse_args()

    if not REGISTRY.exists():
        print(f"FATAL: {REGISTRY} not found", file=sys.stderr)
        sys.exit(2)

    try:
        with REGISTRY.open() as f:
            registry = json.load(f)
    except json.JSONDecodeError as e:
        print(f"FATAL: {REGISTRY} is not valid JSON: {e}", file=sys.stderr)
        sys.exit(2)

    jobs = collect_urls(registry)
    if not jobs:
        print("No URLs to verify in registry. Exiting.")
        sys.exit(0)

    print(f"Verifying {len(jobs)} URLs across "
          f"{len(registry['states'])} states "
          f"(max_workers={MAX_WORKERS}, timeout={TIMEOUT}s)")
    started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    results = {}
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {ex.submit(fetch_status, url): (code, stype, url)
                   for code, stype, url in jobs}
        for fut in as_completed(futures):
            code, stype, url = futures[fut]
            status, final, elapsed, err = fut.result()
            results.setdefault(code, {})[stype] = {
                "url": url,
                "final_url": final if final != url else None,
                "status": status,
                "elapsed_ms": elapsed,
                "error": err,
            }
            if not args.quiet or status != 200:
                tag = f"{status:>3}" if isinstance(status, int) else "ERR"
                redir = ""
                if final and final != url:
                    redir = f"  -> {final}"
                err_str = f"  {err}" if err else ""
                print(f"  {code:3s} {stype:30s} {tag} {elapsed:>5}ms{redir}{err_str}")

    # Summarize
    per_state_pass = {}
    priority_one_failures = []
    total_ok = 0
    total_fail = 0
    for state in registry["states"]:
        code = state["code"]
        state_results = results.get(code, {})
        state_pass = True
        for stype, r in state_results.items():
            if r["status"] == 200:
                total_ok += 1
            else:
                total_fail += 1
                state_pass = False
                if state.get("priority") == 1:
                    priority_one_failures.append((code, stype, r))
        per_state_pass[code] = state_pass

    # Write verification report
    report = {
        "generated_at": started_at,
        "summary": {
            "total_urls": len(jobs),
            "ok": total_ok,
            "failed": total_fail,
            "priority_one_failures": len(priority_one_failures),
        },
        "per_state": results,
    }
    if not args.dry:
        REPORT.write_text(json.dumps(report, indent=2))
        print(f"\nReport: {REPORT}")

    # Stamp last_verified on fully-passing states
    if not args.dry:
        today = datetime.now(timezone.utc).date().isoformat()
        stamped = 0
        for state in registry["states"]:
            if per_state_pass.get(state["code"]):
                state["last_verified"] = today
                stamped += 1
        registry["last_updated"] = today
        REGISTRY.write_text(json.dumps(registry, indent=2))
        print(f"Stamped last_verified={today} on {stamped} fully-passing states")

    print(f"\n{'='*60}")
    print(f"Total URLs: {len(jobs)}   OK: {total_ok}   Failed: {total_fail}")
    print(f"Priority-1 failures: {len(priority_one_failures)}")
    print(f"{'='*60}")

    if priority_one_failures:
        print("\nPRIORITY-1 FAILURES (these block Phase 2 scrapers):")
        for code, stype, r in priority_one_failures:
            print(f"  {code} {stype}: {r['status']} {r['error']}")
            print(f"    url: {r['url']}")
        sys.exit(1)

    sys.exit(0)


if __name__ == "__main__":
    main()
