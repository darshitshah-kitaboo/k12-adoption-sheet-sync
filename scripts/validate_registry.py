"""
Validate every URL in registry/sources.json and in adoption_data.json by
fetching each one live.

Two pools of URLs get checked:

1.  Registry URLs (registry/sources.json). These are the top-level state DOE
    pages, SBE agenda pages, and procurement portals the pipeline uses as
    entry points. Priority-1 failures are fatal and cause a nonzero exit.

2.  Dashboard URLs (adoption_data.json). Every cycle carries a primary `src`
    and a list of `src2` secondary sources. These are what a publisher sees
    on the front end, so a rot here is visible to end users. Failures are
    reported but do not exit nonzero, so one flaky PDF cannot gate the
    weekly run.

Uses the `requests` library with browser-realistic headers, cookie persistence,
and automatic redirect handling. This matches how a real user's browser would
access these state DOE sites, which is necessary because many state servers
return 403 to urllib's default User-Agent or bounce through session cookies
that stdlib urllib drops on the floor.

Install once:
    pip3 install requests

Exit codes:
    0   all priority-1 registry URLs returned 200
    1   one or more priority-1 registry URLs failed
    2   unexpected error (registry file missing, malformed, etc.)

Outputs:
    registry/verification_report.json   per-URL status, timestamp, response time
                                        for both registry and dashboard URLs
    registry/sources.json (in-place)    last_verified stamped on each state that
                                        had every non-null URL return 200

Usage:
    python3 scripts/validate_registry.py              # verify + stamp
    python3 scripts/validate_registry.py --dry        # verify without stamping
    python3 scripts/validate_registry.py --quiet      # only print failures
    python3 scripts/validate_registry.py --verbose    # print every URL attempt
    python3 scripts/validate_registry.py --skip-dashboard
                                                      # registry only, legacy mode
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
DATA = ROOT / "adoption_data.json"

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
SLOW_TIMEOUT = 60  # used once as a second chance for genuinely slow pages
MAX_WORKERS = 8
MAX_RETRIES = 2  # in addition to the initial attempt

# Dashboard URLs that are known to be bot-blocked from data-center IPs but work
# in a real user's browser. The weekly validator otherwise reports them as 403
# failures every run, which is noise. Add URLs here only after a human has
# confirmed they open in a normal browser. The set is matched exactly against
# the URL string, so a renamed or moved URL will fall off this list and start
# failing again, which is the behavior we want for catching real rot.
DASHBOARD_SKIP_URLS = {
    # New Mexico PED. Tested 2026-04-24 by the user in a real browser; opens
    # fine. Serves 403 to GitHub Actions runners and to requests with browser
    # headers. Same pattern as registry's skip_validation for NM
    # doe_instructional_materials.
    "https://web.ped.nm.gov/wp-content/uploads/2025/01/adoption-cycle_02_19_24.pdf",
    "https://web.ped.nm.gov/bureaus/instructional-materials/publishers/",
    "https://web.ped.nm.gov/wp-content/uploads/2025/08/2026_RFA-for-9-12-ELA_SLA_WL_ELD_SLD.pdf",
    # Virginia DOE WAF tightened in late April 2026. The textbook subject
    # pages 403 from data-center IPs even with browser headers and a
    # warmup hit. The URLs are the canonical curated subject hubs and
    # work in a real browser; skip them rather than auto-replace with
    # less-specific parents.
    "https://www.doe.virginia.gov/teaching-learning-assessment/k-12-standards-instruction/english-reading-literacy/english-textbooks",
    "https://www.doe.virginia.gov/teaching-learning-assessment/k-12-standards-instruction/history-social-science/history-social-science-textbooks",
    "https://www.doe.virginia.gov/teaching-learning-assessment/k-12-standards-instruction/mathematics/mathematics-textbooks",
    "https://www.doe.virginia.gov/teaching-learning-assessment/k-12-standards-instruction/science/science-textbooks",
}


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
        # Second chance with a longer timeout. Some state procurement sites
        # (Oklahoma DCS, Louisiana LaPAC) routinely take 40+ seconds under
        # load. If the retry also times out, the URL really is unhealthy.
        if timeout < SLOW_TIMEOUT:
            try:
                r = session.get(url, timeout=SLOW_TIMEOUT,
                                allow_redirects=True, stream=True)
                r.close()
                elapsed = int((time.perf_counter() - started) * 1000)
                return r.status_code, r.url, elapsed, "slow-retry"
            except requests.exceptions.Timeout:
                elapsed = int((time.perf_counter() - started) * 1000)
                return None, url, elapsed, f"Timeout after {SLOW_TIMEOUT}s (slow-retry)"
            except Exception as e2:
                elapsed = int((time.perf_counter() - started) * 1000)
                return None, url, elapsed, f"{type(e2).__name__} on slow-retry: {e2}"
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


def collect_dashboard_urls(data):
    """Flatten adoption_data.json into (state_code, field_label, url) tuples.

    Walks every cycle's `src` primary source and every `src2[].u` secondary
    source. The field label encodes the cycle id so a failing row can be
    traced back to the exact cycle record. Dashboard URLs carry no
    skip_validation flag: they are curated sources promoted only after a
    human review, so any failure is worth surfacing.
    """
    jobs = []
    for state in data.get("states", []):
        code = state.get("code")
        if not code:
            continue
        for cycle in state.get("cycles", []):
            cid = cycle.get("id", "?")
            src = cycle.get("src")
            if src:
                jobs.append((code, f"{cid}:src", src))
            for i, sec in enumerate(cycle.get("src2", []) or []):
                u = sec.get("u")
                if u:
                    jobs.append((code, f"{cid}:src2[{i}]", u))
    return jobs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true",
                    help="Validate without writing last_verified stamps")
    ap.add_argument("--quiet", action="store_true",
                    help="Only print failures, suppress per-URL 200s")
    ap.add_argument("--verbose", action="store_true",
                    help="Print every URL attempt with full context")
    ap.add_argument("--skip-dashboard", action="store_true",
                    help="Skip the adoption_data.json URL sweep (legacy mode)")
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

    # ----- Dashboard URL sweep (adoption_data.json) -----
    # Runs after the registry sweep so the registry summary stays first in
    # the log output. Sharing the fetch pool would tangle progress lines, and
    # the dashboard set is small (~70 URLs), so a second serial pass is fine.
    dashboard_results = {}
    dashboard_ok = 0
    dashboard_fail = 0
    dashboard_skipped = 0
    dashboard_failures = []
    dashboard_total = 0
    if not args.skip_dashboard and DATA.exists():
        try:
            with DATA.open() as f:
                data = json.load(f)
        except json.JSONDecodeError as e:
            print(f"\nWARN: {DATA} is not valid JSON, skipping dashboard sweep: {e}",
                  file=sys.stderr)
            data = None

        if data is not None:
            dash_jobs = collect_dashboard_urls(data)
            dashboard_total = len(dash_jobs)
            if dash_jobs:
                # Split jobs into skipped vs. to-fetch based on DASHBOARD_SKIP_URLS.
                # Skipped entries still land in the report so you can see what
                # was opted out, but they do not count toward pass or fail.
                fetch_jobs = []
                for code, field, url in dash_jobs:
                    if url in DASHBOARD_SKIP_URLS:
                        row = {
                            "field": field,
                            "url": url,
                            "final_url": None,
                            "status": "SKIPPED",
                            "elapsed_ms": 0,
                            "error": "DASHBOARD_SKIP_URLS: bot-blocked, verified in browser",
                        }
                        dashboard_results.setdefault(code, []).append(row)
                        dashboard_skipped += 1
                        if args.verbose or (not args.quiet):
                            print(f"  {code:3s} {field:20s} SKIP          DASHBOARD_SKIP_URLS")
                    else:
                        fetch_jobs.append((code, field, url))

                fetch_count_dash = len(fetch_jobs)
                print(f"\nVerifying {fetch_count_dash} dashboard URLs from adoption_data.json "
                      f"({dashboard_skipped} skipped)")
                with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
                    futures = {ex.submit(fetch_status, url): (code, field, url)
                               for code, field, url in fetch_jobs}
                    for fut in as_completed(futures):
                        code, field, url = futures[fut]
                        status, final, elapsed, err = fut.result()
                        row = {
                            "field": field,
                            "url": url,
                            "final_url": final if final != url else None,
                            "status": status,
                            "elapsed_ms": elapsed,
                            "error": err,
                        }
                        dashboard_results.setdefault(code, []).append(row)
                        if status == 200:
                            dashboard_ok += 1
                        else:
                            dashboard_fail += 1
                            dashboard_failures.append((code, row))
                        if args.verbose or (not args.quiet) or status != 200:
                            tag = f"{status:>3}" if isinstance(status, int) else "ERR"
                            redir = f"  -> {final}" if (final and final != url) else ""
                            err_str = f"  {err}" if err else ""
                            print(f"  {code:3s} {field:20s} {tag} {elapsed:>5}ms{redir}{err_str}")

    # Write verification report
    report = {
        "generated_at": started_at,
        "summary": {
            "total_urls": len(jobs),
            "ok": total_ok,
            "failed": total_fail,
            "skipped": total_skipped,
            "priority_one_failures": len(priority_one_failures),
            "dashboard": {
                "total_urls": dashboard_total,
                "ok": dashboard_ok,
                "failed": dashboard_fail,
                "skipped": dashboard_skipped,
            },
        },
        "per_state": results,
        "dashboard": dashboard_results,
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
    print(f"Registry   Total: {len(jobs):>3}   OK: {total_ok:>3}   "
          f"Failed: {total_fail:>3}   Skipped: {total_skipped:>3}")
    if dashboard_total:
        print(f"Dashboard  Total: {dashboard_total:>3}   OK: {dashboard_ok:>3}   "
              f"Failed: {dashboard_fail:>3}   Skipped: {dashboard_skipped:>3}")
    print(f"Priority-1 registry failures: {len(priority_one_failures)}")
    print(f"{'='*60}")

    if priority_one_failures:
        print("\nPRIORITY-1 FAILURES (these block Phase 2 scrapers):")
        for code, stype, r in priority_one_failures:
            print(f"  {code} {stype}: {r['status']} {r['error']}")
            print(f"    url: {r['url']}")

    if dashboard_failures:
        # Print but do not exit nonzero. Dashboard URLs are curated sources
        # and a single bad PDF should not gate the weekly run.
        print(f"\nDASHBOARD URL FAILURES ({len(dashboard_failures)}):")
        for code, r in dashboard_failures:
            status_str = r["status"] if r["status"] is not None else "ERR"
            err_str = f" {r['error']}" if r["error"] else ""
            print(f"  {code} {r['field']}: {status_str}{err_str}")
            print(f"    url: {r['url']}")

    if priority_one_failures:
        sys.exit(1)

    sys.exit(0)


if __name__ == "__main__":
    main()
