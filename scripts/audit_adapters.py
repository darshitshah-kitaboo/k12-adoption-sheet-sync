"""Live audit of every registered adapter.

Runs each adapter in scripts/run_adapters.ADAPTERS against its live
SOURCE_URL and produces a triage report grouping outcomes into:
  GREEN: fetched cleanly, returned non-zero cycles
  YELLOW: fetched cleanly but returned zero cycles (URL or selector
          drift; needs EXTRA_HOSTS or a different page)
  RED: fetch itself failed (DNS, 403, timeout, WAF)

The intent is one-shot diagnosis. Run from a developer machine with
real-world network access, paste the output back to follow up on per-
state fixes. Does not modify scraped/ or adoption_data.json.

Usage:
    python3 scripts/audit_adapters.py                  # audit all
    python3 scripts/audit_adapters.py --only NY OH WA  # audit subset

Outputs:
    Stdout summary table.
    logs/adapter_audit_<timestamp>.json with full per-state detail.
"""

import argparse
import importlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
LOG_DIR = ROOT / "logs"


def audit_one(state_code, module_name):
    """Run one adapter live; return a dict describing the outcome."""
    started = time.monotonic()
    out = {
        "state": state_code,
        "module": module_name,
        "fetch_status": None,
        "fetch_bytes": None,
        "cycle_count": 0,
        "document_count": None,
        "error": None,
        "elapsed_ms": 0,
        "source_url": None,
    }
    try:
        mod = importlib.import_module(module_name)
    except Exception as e:
        out["error"] = f"import: {type(e).__name__}: {e}"
        out["elapsed_ms"] = int((time.monotonic() - started) * 1000)
        return out

    out["source_url"] = getattr(mod, "SOURCE_URL", None)

    try:
        html = mod.fetch_html()
        out["fetch_status"] = "ok"
        out["fetch_bytes"] = len(html or "")
    except Exception as e:
        out["fetch_status"] = "fail"
        out["error"] = f"fetch: {type(e).__name__}: {str(e)[:200]}"
        out["elapsed_ms"] = int((time.monotonic() - started) * 1000)
        return out

    try:
        data = mod.parse(html)
        out["cycle_count"] = data.get("cycle_count", 0)
        if "document_count" in data:
            out["document_count"] = data["document_count"]
    except Exception as e:
        out["error"] = f"parse: {type(e).__name__}: {str(e)[:200]}"

    out["elapsed_ms"] = int((time.monotonic() - started) * 1000)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="+", help="Audit only these state codes")
    args = ap.parse_args()

    from scripts import run_adapters  # noqa: E402

    selected = (set(c.upper() for c in args.only) if args.only
                else set(run_adapters.ADAPTERS))

    print(f"Auditing {len(selected)} adapter(s)...\n")
    results = []
    for code in sorted(selected):
        cfg = run_adapters.ADAPTERS[code]
        out = audit_one(code, cfg["module"])
        results.append(out)
        bucket = bucket_for(out)
        cc = out["cycle_count"]
        dc = out["document_count"]
        cc_str = f"cc={cc}" + (f" docs={dc}" if dc is not None else "")
        err = out["error"] or ""
        print(f"  [{bucket}] {code:<3} {cc_str:<20} {out['elapsed_ms']:>5}ms  {err[:90]}")

    # Summary
    by_bucket = {"GREEN": [], "YELLOW": [], "RED": []}
    for r in results:
        by_bucket[bucket_for(r)].append(r["state"])
    print()
    print(f"GREEN  ({len(by_bucket['GREEN'])}): {by_bucket['GREEN']}")
    print(f"YELLOW ({len(by_bucket['YELLOW'])}): {by_bucket['YELLOW']}")
    print(f"RED    ({len(by_bucket['RED'])}): {by_bucket['RED']}")

    # Persist
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    path = LOG_DIR / f"adapter_audit_{ts}.json"
    path.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "summary": {b: len(s) for b, s in by_bucket.items()},
        "results": results,
    }, indent=2), encoding="utf-8")
    print(f"\nWrote detailed report to {path.relative_to(ROOT)}")


def bucket_for(out):
    if out["fetch_status"] == "fail":
        return "RED"
    if out["error"]:
        return "RED"
    if out["cycle_count"] == 0:
        return "YELLOW"
    return "GREEN"


if __name__ == "__main__":
    main()
