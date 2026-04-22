"""
Push adoption intelligence data to a Google Sheet.

Invoked by the Cowork scheduled task `adoption-intel-nightly`.

Reads:
  .sheet_config.json  (sheet_id, service_account_path, timezone)
  adoption_data.json  (the scraped payload; same shape as adoption_data.json
                       generated from the kitaboo-adoption-intelligence JSX)

Writes to the sheet's 8 tabs: README (skipped), Summary, States, Cycles,
Timeline, Sources, Tips, Enrollment. All tabs except README are fully
rewritten below the header on each run.

Install once:
    pip3 install --break-system-packages \
        google-api-python-client google-auth

Run manually:
    python3 scripts/refresh_and_push.py
"""

import json
import os
import sys
import logging
from datetime import datetime, date
from pathlib import Path

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
)
LOG = logging.getLogger("refresh_and_push")

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / ".sheet_config.json"
DATA_PATH = ROOT / "adoption_data.json"

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# Column schemas per tab. Must match the headers in K12_Adoption_Intelligence_Live.xlsx.
STATES_COLS = [
    "State Code", "State", "Governance", "Status", "Priority",
    "Total Enrollment", "Last Verified", "Cycles Count", "Authority", "Summary",
]
CYCLES_COLS = [
    "Cycle ID", "State Code", "State", "Governance",
    "Subject", "Subject Group", "Grade Band",
    "Status", "Confidence", "Confidence Tier",
    "Adoption Year", "Implementation Year",
    "Contract Start", "Contract End",
    "Next Deadline", "Deadline Label", "Active Call Open",
    "Student Scale",
    "Accessibility", "NIMAS", "Digital", "Packaging", "HQIM", "Change Pending",
    "Primary Source URL", "Last Verified",
]
TIMELINE_COLS = ["Cycle ID", "State Code", "State", "Subject", "Event Date", "Milestone", "Passed"]
SOURCES_COLS = ["Cycle ID", "State Code", "State", "Subject", "Source Type", "Title", "URL"]
TIPS_COLS = ["Cycle ID", "State Code", "State", "Subject", "Category", "Tip"]
ENROLLMENT_COLS = ["State Code", "Total Enrollment", "Year", "Source", "Confidence", "K-8 (CA only)"]

# Header rows already exist on the sheet. Data starts on row 2.
DATA_START = 2


def _bool(v):
    if v is True: return "Yes"
    if v is False: return "No"
    return ""


def load_config():
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(
            f"{CONFIG_PATH} is missing. See CONNECT.md step 1."
        )
    with CONFIG_PATH.open() as f:
        return json.load(f)


def load_data():
    if not DATA_PATH.exists():
        raise FileNotFoundError(
            f"{DATA_PATH} is missing. The nightly task should produce it before pushing."
        )
    with DATA_PATH.open() as f:
        return json.load(f)


def recompute_time_fields(data):
    """Recompute Next Deadline, Deadline Label, and Passed flags against today's date.

    Adoption cycles don't move every day, but deadline countdowns do. This keeps the
    'next event' row accurate as dates roll forward, so a 14d countdown on the front-end
    becomes 13d tomorrow without a data refresh.
    """
    today = date.today().isoformat()
    for s in data.get("states", []):
        for c in s.get("cycles", []):
            ke = c.get("ke") or []
            for ev in ke:
                d = ev.get("d", "")
                if d:
                    ev["p"] = d < today
            future = [ev for ev in ke if ev.get("d") and ev.get("d") >= today]
            future.sort(key=lambda ev: ev.get("d", ""))
            if future:
                nxt = future[0]
                c["dl"] = nxt.get("d", c.get("dl", ""))
                c["dlL"] = nxt.get("l", c.get("dlL", ""))


def get_students(enrollment, code, grade_band):
    e = enrollment.get(code)
    if not e:
        return ""
    total = e.get("total", 0)
    if code == "CA" and grade_band in ("K-8", "K-5"):
        return e.get("k8") or round(total * 0.685)
    if grade_band == "K-8":
        return round(total * 0.685)
    if grade_band == "K-5":
        return round(total * 0.46)
    if grade_band == "6-12":
        return round(total * 0.54)
    if grade_band == "9-12":
        return round(total * 0.315)
    return total


def build_states_rows(data):
    rows = []
    for s in data["states"]:
        total = data["enrollment"].get(s["code"], {}).get("total", "")
        rows.append([
            s["code"], s["name"], s["governance"], s["status"], s.get("priority", 0) or "",
            total if total else "", s["last_verified"], len(s["cycles"]),
            s["authority"], s["summary"],
        ])
    return rows


def build_cycles_rows(data):
    rows = []
    for s in data["states"]:
        for c in s["cycles"]:
            students = c.get("students") or get_students(data["enrollment"], s["code"], c.get("gd", ""))
            rows.append([
                c.get("id", ""), s["code"], s["name"], s["governance"],
                c.get("su", ""), c.get("gr", ""), c.get("gd", ""),
                c.get("st", ""), c.get("cf", ""), c.get("tier", ""),
                c.get("ay", ""), c.get("iy", ""),
                c.get("cs", ""), c.get("ce", ""),
                c.get("dl", ""), c.get("dlL", ""), _bool(c.get("ac", False)),
                students or "",
                _bool(c.get("acc", False)), _bool(c.get("nim", False)),
                _bool(c.get("dig", False)), c.get("pk", ""),
                _bool(c.get("hq", False)), _bool(c.get("ch", False)),
                c.get("src", ""), c.get("v", ""),
            ])
    return rows


def build_timeline_rows(data):
    rows = []
    for s in data["states"]:
        for c in s["cycles"]:
            for ev in (c.get("ke") or []):
                rows.append([
                    c.get("id", ""), s["code"], s["name"], c.get("su", ""),
                    ev.get("d", ""), ev.get("l", ""), _bool(ev.get("p", False)),
                ])
    return rows


def build_sources_rows(data):
    rows = []
    for s in data["states"]:
        for c in s["cycles"]:
            if c.get("src"):
                rows.append([c.get("id", ""), s["code"], s["name"], c.get("su", ""),
                             "Primary", "Primary source", c.get("src", "")])
            for src in (c.get("src2") or []):
                rows.append([c.get("id", ""), s["code"], s["name"], c.get("su", ""),
                             src.get("ty", "Secondary"), src.get("t", ""), src.get("u", "")])
    return rows


def build_tips_rows(data):
    rows = []
    for s in data["states"]:
        for c in s["cycles"]:
            for tip in (c.get("tips") or []):
                rows.append([c.get("id", ""), s["code"], s["name"], c.get("su", ""),
                             tip.get("cat", ""), tip.get("note", "")])
    return rows


def build_enrollment_rows(data):
    rows = []
    for code, e in sorted(data["enrollment"].items()):
        rows.append([code, e.get("total", ""), e.get("y", ""), e.get("src", ""),
                     e.get("cf", ""), e.get("k8", "")])
    return rows


def build_summary_rows(data):
    total_states = len(data["states"])
    total_cycles = sum(len(s["cycles"]) for s in data["states"])
    active = sum(1 for s in data["states"] for c in s["cycles"] if c.get("ac"))
    upcoming = sum(1 for s in data["states"] for c in s["cycles"] if c.get("st") == "Upcoming")
    published = sum(1 for s in data["states"] for c in s["cycles"] if c.get("st") == "Published schedule")
    active_students = sum((c.get("students") or 0) for s in data["states"] for c in s["cycles"] if c.get("ac"))
    pipeline_students = sum((c.get("students") or 0) for s in data["states"] for c in s["cycles"]
                            if c.get("ac") or c.get("st") == "Upcoming")
    now = datetime.now().strftime("%Y-%m-%d %H:%M UTC")
    rows = [
        ["Generated", now, "Refresh every 24 hours"],
        ["States tracked", total_states, "Live + watchlist"],
        ["Total adoption cycles", total_cycles, "Includes active, upcoming, published schedule, rolling"],
        ["Active call open", active, "Submission windows currently open"],
        ["Upcoming", upcoming, "Schedule published, window not yet open"],
        ["Long-range published", published, "Planning window only"],
        ["Active pipeline students", active_students, "Sum of enrollment across active cycles"],
        ["Total pipeline students", pipeline_students, "Active plus upcoming"],
    ]
    return rows


def get_service(sa_path):
    """Build a Sheets client from either an env var (GitHub Actions) or a file path (local)."""
    sa_json = os.environ.get("ADOPTION_SA_JSON")
    if sa_json:
        info = json.loads(sa_json)
        creds = Credentials.from_service_account_info(info, scopes=SCOPES)
    else:
        creds = Credentials.from_service_account_file(sa_path, scopes=SCOPES)
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


def clear_and_write(service, sheet_id, tab, num_cols, rows):
    last_col = _col_letter(num_cols)
    clear_range = f"{tab}!A{DATA_START}:{last_col}"
    service.spreadsheets().values().clear(
        spreadsheetId=sheet_id, range=clear_range, body={}
    ).execute()
    if not rows:
        LOG.info("  %-11s 0 rows (cleared only)", tab)
        return
    service.spreadsheets().values().update(
        spreadsheetId=sheet_id,
        range=f"{tab}!A{DATA_START}",
        valueInputOption="USER_ENTERED",
        body={"values": rows},
    ).execute()
    LOG.info("  %-11s %d rows written", tab, len(rows))


def _col_letter(n):
    s = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def main():
    # Env vars take precedence so GitHub Actions can run without a config file.
    env_sheet_id = os.environ.get("ADOPTION_SHEET_ID")
    env_sa_json = os.environ.get("ADOPTION_SA_JSON")

    sheet_id = env_sheet_id
    sa_path = None

    if not sheet_id:
        cfg = load_config()
        sheet_id = cfg["sheet_id"]
        sa_path = cfg["service_account_path"]
        if not os.path.isabs(sa_path):
            sa_path = str(ROOT / sa_path)

    if not sheet_id or sheet_id.startswith("PASTE_"):
        LOG.error("sheet_id not set. Provide ADOPTION_SHEET_ID env var or .sheet_config.json.")
        sys.exit(1)

    if not env_sa_json:
        if not sa_path or not os.path.exists(sa_path):
            LOG.error("Service account credentials missing. Set ADOPTION_SA_JSON env var or place the JSON file at %s.", sa_path)
            sys.exit(1)

    data = load_data()
    recompute_time_fields(data)
    service = get_service(sa_path)

    LOG.info("Pushing to sheet %s ...", sheet_id[:12] + "...")

    try:
        # Summary tab uses a different header layout; write from A2 anyway.
        clear_and_write(service, sheet_id, "Summary", 3, build_summary_rows(data))
        clear_and_write(service, sheet_id, "States", len(STATES_COLS), build_states_rows(data))
        clear_and_write(service, sheet_id, "Cycles", len(CYCLES_COLS), build_cycles_rows(data))
        clear_and_write(service, sheet_id, "Timeline", len(TIMELINE_COLS), build_timeline_rows(data))
        clear_and_write(service, sheet_id, "Sources", len(SOURCES_COLS), build_sources_rows(data))
        clear_and_write(service, sheet_id, "Tips", len(TIPS_COLS), build_tips_rows(data))
        clear_and_write(service, sheet_id, "Enrollment", len(ENROLLMENT_COLS), build_enrollment_rows(data))
    except HttpError as e:
        LOG.error("Sheets API error: %s", e)
        sys.exit(2)

    LOG.info("Done.")


if __name__ == "__main__":
    main()
