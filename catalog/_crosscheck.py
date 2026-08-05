#!/usr/bin/env python3
"""Diff INDEX.md against the profiles, which are the authority.

`_rollup.py` will eventually GENERATE the index from frontmatter and make this
unnecessary. Until it exists the index is hand-maintained, which means it drifts — and
it drifted badly the first time this ran: eleven rows carried `rights: unknown` for
sources whose terms had been read and quoted, and three carried `title?: no` for
sources with a working keyword filter. A reader trusts the table, so a stale table is
worse than an empty one.

    python catalog/_crosscheck.py           # report drift
    python catalog/_crosscheck.py --fix     # rewrite the drifted cells

The column layouts differ per table and two of them are the same WIDTH, so the header
row is parsed rather than assumed — reading `rights` from a fixed offset silently
compared the ceiling column against a licence on the first attempt.
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys

import yaml

HERE = pathlib.Path(__file__).parent
INDEX = HERE / "INDEX.md"

ICON = {
    "wired": "🔵",
    "evaluated": "🟡",
    "rejected": "🔴",
    "pending-key": "🔑",
    "no-public-api": "⛔",
    "dead": "💀",
}


def profiles() -> dict:
    out = {}
    for f in sorted(HERE.glob("*.md")):
        if f.name.startswith("_") or f.name == "INDEX.md":
            continue
        d = yaml.safe_load(f.read_text().split("---")[1])
        out[d["name"]] = d
    return out


def cells(line: str) -> list[str]:
    return [c.strip() for c in line.split("|")][1:-1]


def want_values(d: dict) -> dict:
    """What each column SHOULD say, given the profile."""
    ts = str(d["query"]["title_search"]).lower()
    return {
        "status": ICON[d["status"]],
        "rights": str(d["license"]["commercial_use"]),
        "title?": {"true": "yes", "false": "no"}.get(ts, ts),
        "junk params": {
            "ignores_unknown": "silently ignores",
            "rejects_unknown_400": "rejects (400)",
        }.get(str(d["query"]["param_validation"]), str(d["query"]["param_validation"])),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fix", action="store_true", help="rewrite drifted cells in place")
    a = ap.parse_args()

    prof = profiles()
    lines = INDEX.read_text().split("\n")
    header: list[str] = []
    drift, fixed = [], 0

    for i, ln in enumerate(lines):
        if not ln.startswith("|"):
            continue
        c = cells(ln)
        if c and c[0] == "source":  # a new table starts; remember ITS columns
            header = c
            continue
        if not header or set(c[0]) <= set("- ") or len(c) != len(header):
            continue
        key = re.sub(r"\[([^\]]+)\].*", r"\1", c[0])
        d = prof.get(key)
        if not d:
            continue
        want = want_values(d)
        for col, value in want.items():
            if col not in header:
                continue
            j = header.index(col)
            got = c[j].replace("*", "").strip()
            # The measured cells are prose; only compare the short factual columns,
            # and treat the icon columns as "contains" rather than "equals".
            ok = value in c[j] if col == "status" else got == value
            if ok:
                continue
            drift.append(f"{key:16} {col:12} index={c[j]!r:28} profile={value!r}")
            if a.fix:
                c[j] = f"**{value}**" if value in ("prohibited", "yes") else value
                lines[i] = "| " + " | ".join(c) + " |"
                fixed += 1

    if drift:
        print("\n".join("  DRIFT  " + d for d in drift))
    else:
        print("  no drift — INDEX.md agrees with every profile")
    print(f"\nchecked {len(prof)} profiles")

    if a.fix and fixed:
        INDEX.write_text("\n".join(lines))
        print(f"rewrote {fixed} cells in INDEX.md")
    return 1 if drift and not a.fix else 0


if __name__ == "__main__":
    sys.exit(main())
