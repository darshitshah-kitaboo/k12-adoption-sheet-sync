"""Tests for subject_groups() — the subject-bucket mapping that powers
the front-end's per-subject filtering at kitaboo.com/<subject>-publishers/.

Test cases cover:
  - The three explicit examples the user gave on 2026-04-27
  - Every distinct cycle subject in the current adoption_data.json
  - Edge cases: empty input, all-subjects sentinels, multi-bucket strings

Run:
    python3 scripts/test_subject_groups.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.refresh_and_push import subject_groups, SUBJECT_BUCKETS


def _fail(msg):
    print(f"FAIL: {msg}")
    sys.exit(1)


def _ok(msg):
    print(f"ok  {msg}")


# Hard cases the user explicitly called out. If any of these regress,
# the front-end's subject pages will misroute states.
EXPLICIT_CASES = [
    ("Digital Literacy & Computer Science", "Science",
     "Alabama: per user, this is Science (CS + digital literacy bucket here)"),
    ("ELA/ELD Follow-up Adoption", "ELA/RLA",
     "California: ELD + ELA both ELA/RLA"),
    ("Mathematics & Computer Science", "Math, Science",
     "Florida: per user, show in both Math and Science"),
]


def run():
    for subj, expected, note in EXPLICIT_CASES:
        got = subject_groups(subj)
        if got != expected:
            _fail(f"{subj!r} expected {expected!r}, got {got!r} ({note})")
    _ok(f"all {len(EXPLICIT_CASES)} explicit user examples matched")

    # Single-bucket sanity
    cases = [
        ("Mathematics", "Math"),
        ("Math", "Math"),
        ("Algebra II", "Math"),
        ("Science", "Science"),
        ("Biology", "Science"),
        ("Computer Science", "Science"),
        ("ELA", "ELA/RLA"),
        ("RLA", "ELA/RLA"),
        ("Reading", "ELA/RLA"),
        ("English Language Arts", "ELA/RLA"),
        ("Social Studies", "Social Studies"),
        ("US History", "Social Studies"),
        ("Civics", "Social Studies"),
        ("CTE", "Others"),
        ("Career and Technical Education", "Others"),
        ("World Languages", "Others"),
        ("Fine Arts", "Others"),
        ("Physical Education", "Others"),
    ]
    for subj, expected in cases:
        got = subject_groups(subj)
        if got != expected:
            _fail(f"{subj!r} expected {expected!r}, got {got!r}")
    _ok(f"all {len(cases)} single-bucket cases matched")

    # Multi-bucket
    multi = [
        ("Mathematics & Computer Science", "Math, Science"),
        ("Social Studies, CTE: Business Mgmt & Admin", "Social Studies, Others"),
        ("PE/Health, CTE: Adv Manufacturing & IT", "Others"),
        ("Arts, World Languages, CS, CS Apps, Driver's Ed, CTE", "Science, Others"),
        ("9-12 ELA, SLA, World Languages, ELD, SLD", "ELA/RLA, Others"),
        ("CTE and Visual and Performing Arts", "Others"),
    ]
    for subj, expected in multi:
        got = subject_groups(subj)
        if got != expected:
            _fail(f"{subj!r} expected {expected!r}, got {got!r}")
    _ok(f"all {len(multi)} multi-bucket cases matched")

    # All-subjects sentinels
    sentinels = [
        "All Subjects",
        "All Subjects (Local)",
        "All Subjects (HQIM)",
        "All Subjects (Rolling)",
        "All Subjects (Single-District)",
        "All Subjects (Local Selection)",
        "General",
    ]
    full = ", ".join(SUBJECT_BUCKETS)
    for subj in sentinels:
        got = subject_groups(subj)
        if got != full:
            _fail(f"{subj!r} expected all-buckets {full!r}, got {got!r}")
    _ok(f"all {len(sentinels)} all-subjects sentinels expand to every bucket")

    # Empty / None input falls back to Others (never empty string).
    if subject_groups("") != "Others":
        _fail(f"empty string should fall back to Others, got {subject_groups('')!r}")
    if subject_groups(None) != "Others":
        _fail(f"None should fall back to Others, got {subject_groups(None)!r}")
    _ok("empty and None inputs fall back to Others")

    # Final sweep: every cycle subject in the live adoption_data.json
    # produces a non-empty, well-formed bucket string.
    repo = Path(__file__).resolve().parent.parent
    data = json.loads((repo / "adoption_data.json").read_text(encoding="utf-8"))
    bad = []
    for s in data["states"]:
        for c in s.get("cycles", []):
            su = c.get("su", "")
            sg = subject_groups(su)
            if not sg:
                bad.append((s["code"], c.get("id"), su, "empty"))
            else:
                # Every component must be one of the canonical buckets.
                parts = [p.strip() for p in sg.split(",")]
                for p in parts:
                    if p not in SUBJECT_BUCKETS:
                        bad.append((s["code"], c.get("id"), su,
                                     f"unknown bucket {p!r}"))
                        break
    if bad:
        print("FAIL: live data produced malformed bucket strings:")
        for row in bad[:10]:
            print(f"  {row}")
        sys.exit(1)
    cycles = sum(len(s.get("cycles", [])) for s in data["states"])
    _ok(f"all {cycles} live-data cycles produce well-formed bucket strings")

    print("\nAll subject_groups() tests passed.")


if __name__ == "__main__":
    run()
