"""
Validate every URL in registry/sources.json by fetching it live.

Uses the `requests` library with browser-realistic headers, cookie persistence,
and automatic redirect handling. This matches how a real user's browser would
access these state DOE sites, which is necessary because many state servers
return 403 to urllib's default User-Agent or bounce through session cookies
that stdlib urllib drops on the floor.

Install once:
    pip3 install requests

Exit codes:
    0   all priority-1 (active adoption) state URLs returned 200
    1   one or more priority-1 URLs failed
    2   unexpected error (registry file missing, malformed, etc.)

Outputs:
    registry/verification_report.json   per-URL status, timestamp, response time
    registry/sources.json (in-place)    last_verified stamped on each state that
                                         had every non-null URL return 200

Usage:
    python3 scripts/validate_registry.py              # verify + stamp
    python3 scripts/validate_registry.py --dry        # verify without stamping
    python3 scripts/validate_registry.py --quiet      # only print failures
    python3 scripts/validate_registry.py --verbose    # print every URL attempt
"""

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

try:
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
except ImportError:
    print("FATAL: requests library not installed.", file=sys.stderr)
    print("Run: pip3 install requests", file=sys.stderr)
    sys.exit(2)

ROOT = Path(__file__).resolve().parent.parent
REGISTRY = ROOT / "registry" / "sources.json"
REPORT = ROOT / "registry" / "verification_report.json"

# Browser-realistic headers. State DOE sites routinely 403 anything that looks
# scripted. This matches Chrome 120 on macOS. Do not get creative here, the
# header set is load-bearing for about a third of the 57 URLs.
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,image/apng,*/*;q=0.8,"
        "application/signed-exchange;v=b3;q=0.7"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}

TIMEOUT = 30  # generous, some state sites are genuinely slow
MAX_WORKERS = 8
MAX_RETRIES = 2  # in addition to the initial attempt


def build_session():
    """Session with browser headers, cookie jar, and retry on transient errors."""
    s = requests.Session()
    s.headers.update(BROWSER_HEADERS)

    # Retry on the common transient codes. Do not retry on 403/404, those are
    # deterministic and retrying just wastes time.
    retry = Retry(
        total=MAX_RETRIES,
        backoff_factor=1.0,
        status_forcelist=[500, 502, 503, 504],
        allowed_methods=["GET", "HEAD"],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    s.mount("http://", adapter)
    s.mount("https://", adapter)
    return s


def fetch_status(url, timeout=TIMEOUT):
    """Return (status_code, final_url, elapsed_ms, error_msg)."""
    started = time.perf_counter()
    session = build_session()
    try:
        # Some state sites (e.g. WVDE) reject HEAD but allow GET. Skip HEAD
        # and go straight to GET with stream=True so we don't actually
        # download the body.
        r = session.get(url, timeout=timeout, allow_redirects=True, stream=True)
        r.close()
        elapsed = int((time.perf_counter() - started) * 1000)
        return r.status_code, r.url, elapsed, None
    except requests.exceptions.SSLError as e:
        # Retry once ignoring SSL. Some state sites have expired or
        # misconfigured certs but the page itself is up.
        try:
            r = session.get(url, timeout=timeout, allow_redirects=True,
                            stream=True, verify=False)
            r.close()
            elapsed = int((time.perf_counter() - started) * 1000)
            return r.status_code, r.url, elapsed, "SSL warning (ignored)"
        except Exception as e2:
            elapsed = int((time.perf_counter() - started) * 1000)
            return None, url, elapsed, f"SSLError: {e2}"
    except requests.exceptions.Timeout:
        elapsed = int((time.perf_counter() - started) * 1000)
        return None, url, elapsed, f"Timeout after {timeout}s"
    except requests.exceptions.ConnectionError as e:
        elapsed = int((time.perf_counter() - started) * 1000)
        return None, url, elapsed, f"ConnectionError: {e}"
    except requests.exceptions.RequestException as e:
        elapsed = int((time.perf_counter() - started) * 1000)
        return None, url, elapsed, f"{type(e).__name__}: {e}"


def collect_urls(registry):
    """Flatten the registry into (state_code, source_type, url, skip) tuples.

    skip is True when the source entry carries skip_validation: true. Skipped
    URLs are reported but do not count toward pass/fail. Some state WAFs
    hard-block any data-center IP (including GitHub Actions), so a 403 from
    the validator does not mean the URL is dead. skip_validation marks those
    so the validator does not false-alarm.
    """
    jobs = []
    for state in registry.get("states", []):
        code = state["code"]
        sources = state.get("sources", {})
        for stype, info in sources.items():
            if stype == "secondary":
                for i, sec in enumerate(info or []):
                    if sec.get("url"):
                        jobs.append((code, f"secondary[{i}]", sec["url"],
                                     bool(sec.get("skip_validation"))))
                continue
            info = info or {}
            url = info.get("url")
            if url:
                jobs.append((code, stype, url, bool(info.get("skip_validation"))))
    return jobs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true",
                    help="Validate without writing last_verified stamps")
    ap.add_argument("--quiet", action="store_true",
                    help="Only print failures, suppress per-URL 200s")
    ap.add_argument("--verbose", action="store_true",
                    help="Print every URL attempt with full context")
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

    skip_count = sum(1 for _, _, _, skip in jobs if skip)
    fetch_count = len(jobs) - skip_count
    print(f"Verifying {fetch_count} URLs across "
          f"{len(registry['states'])} states "
          f"(max_workers={MAX_WORKERS}, timeout={TIMEOUT}s, retries={MAX_RETRIES}); "
          f"{skip_count} skip_validation entries")
    started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    # Suppress the noisy InsecureRequestWarning from urllib3 for fallback
    # unverified requests.
    import warnings
    warnings.filterwarnings("ignore", message="Unverified HTTPS request")

    results = {}

    # Record skipped entries up front so they appear in the report.
    for code, stype, url, skip in jobs:
        if skip:
            results.setdefault(code, {})[stype] = {
                "url": url,
                "final_url": None,
                "status": "SKIPPED",
                "elapsed_ms": 0,
                "error": "skip_validation: true (WAF-blocked from data-center IPs)",
            }
            if args.verbose or (not args.quiet):
                print(f"  {code:3s} {stype:30s} SKIP          skip_validation")

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {ex.submit(fetch_status, url): (code, stype, url)
                   for code, stype, url, skip in jobs if not skip}
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
            if args.verbose or (not args.quiet) or status != 200:
                tag = f"{status:>3}" if isinstance(status, int) else "ERR"
                redir = f"  -> {final}" if (final and final != url) else ""
                err_str = f"  {err}" if err else ""
                print(f"  {code:3s} {stype:30s} {tag} {elapsed:>5}ms{redir}{err_str}")

    # Summarize. Skipped entries do not count as pass or fail; they are
    # intentionally opted out of validation and do not block state stamping.
    per_state_pass = {}
    priority_one_failures = []
    total_ok = 0
    total_fail = 0
    total_skipped = 0
    for state in registry["states"]:
        code = state["code"]
        state_results = results.get(code, {})
        state_pass = True
        for stype, r in state_results.items():
            if r["status"] == "SKIPPED":
                total_skipped += 1
                continue
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
            "skipped": total_skipped,
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
    print(f"Total URLs: {len(jobs)}   OK: {total_ok}   "
          f"Failed: {total_fail}   Skipped: {total_skipped}")
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
