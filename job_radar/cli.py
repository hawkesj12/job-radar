"""Command-line interface: scan / apply / dismiss / list."""

from __future__ import annotations

import argparse
import importlib.resources as resources
import json
import sys
from pathlib import Path

from . import config, engine, funnel, llm, shortlist
from .dedup import dedup_key
from .util import today_et


def _packaged(name: str) -> str:
    """Read a file shipped in job_radar/data/ (works from a wheel or the repo)."""
    return (resources.files("job_radar") / "data" / name).read_text(encoding="utf-8")


def _resolve_config(path_arg):
    for cand in (path_arg, "job-radar.yaml", "job-radar.example.yaml"):
        if cand and Path(cand).exists():
            return config.load_config(cand)
    return config.load_config(None)  # generic defaults


def _tier(score: int, cfg) -> str:
    """The configurable quality tier for a score (the `scoring.tiers` knob)."""
    if score >= cfg.tier_strong:
        return "★ strong"
    if score >= cfg.tier_look:
        return "◆ worth a look"
    return ""


def _fmt(r, cfg) -> str:
    sc = r.get("llm_score") or r.get("score")
    tag = f"[{r.get('id')}]"
    head = f"  {tag} {str(sc):>3}  {r.get('title', '')[:52]:52}  {r.get('company', '')[:24]}"
    extra = []
    tier = _tier(shortlist._safe_int(sc), cfg)
    if tier:
        extra.append(tier)
    if r.get("salary"):
        extra.append(r["salary"])
    if r.get("age_days"):
        extra.append(f"{r['age_days']}d")
    if r.get("status") and r["status"] != "new":
        extra.append(r["status"].upper())
    line = head + ("  " + " · ".join(extra) if extra else "")
    note = r.get("llm_note")
    if note:
        line += f"\n        ↳ {note}"
    return line


def cmd_scan(args, cfg):
    # Poll (and auto-grow) only a REAL watchlist.json — never the shipped template.
    # On first run, seed watchlist.json from the packaged starter list so discovery
    # writes there, not into a git-tracked *.example.json.
    wl = args.watchlist or "watchlist.json"
    if not Path(wl).exists():
        try:
            Path(wl).write_text(_packaged("watchlist.example.json"), encoding="utf-8")
            print(f"note: no watchlist found — seeded {wl} from the starter list.")
        except (OSError, ModuleNotFoundError, FileNotFoundError):
            print("note: no watchlist — running breadth sources only.")
            wl = None

    print("scanning…")
    rows, discovered, errors = engine.harvest(cfg, wl)
    # The engine finds new companies but no longer persists them — it is a library and
    # this file is the app that owns watchlist.json. Append here so the CLI keeps its
    # self-growing behaviour, and never into the packaged *.example.json.
    if discovered and wl:
        try:
            discovered = funnel.append_watchlist(Path(wl), discovered)
        except (OSError, json.JSONDecodeError) as e:
            # JSONDecodeError is a ValueError, not an OSError, so a corrupt
            # watchlist.json used to escape this handler entirely — AFTER the whole
            # network harvest above and BEFORE the shortlist write below, throwing
            # away a good run over a file we only wanted to APPEND to. Growing the
            # watchlist is a nice-to-have; the harvest is the point. engine.harvest
            # already treats this same file the same way (a reported soft error).
            msg = f"could not grow {wl} ({type(e).__name__})"
            print(f"note: {msg}")
            errors.append(msg)
            discovered = []
    # Total failure (nothing harvested, but sources errored) — do NOT let upsert
    # write an empty store: that would wipe "new" roles and reset first_seen,
    # corrupting the "remembers what you've seen" history. Keep the prior file.
    if not rows and errors:
        existing = shortlist.load_all(args.out)
        print(
            f"⚠ all sources failed ({len(errors)} errors) — keeping your existing "
            f"shortlist ({len(existing)} roles). Nothing overwritten."
        )
        if args.verbose:
            for e in errors:
                print(f"    {e}")
        else:
            print("  (run with --verbose to see which sources failed)")
        for r in shortlist.surface(existing, cfg)[: args.limit]:
            print(_fmt(r, cfg))
        # Nonzero so a scheduled/cron wrapper can detect a dead run.
        raise SystemExit(1)
    by_key = {(p.get("dedup_key") or dedup_key(p)): p for p in rows}
    today = today_et()
    # When the LLM re-rank runs we annotate in memory and write ONCE at the end;
    # otherwise upsert does the single write itself.
    llm_on = cfg.llm.enabled
    merged = shortlist.upsert(args.out, rows, today, write=not llm_on)

    surfaced = shortlist.surface(merged, cfg)
    targets = surfaced[: cfg.llm.rerank_top_n]

    if llm_on:
        items = [
            {
                "key": r["dedup_key"],
                "title": r["title"],
                "company": r["company"],
                "text": (by_key.get(r["dedup_key"], {}) or {}).get("text", ""),
            }
            for r in targets
        ]
        ann = llm.rerank(items, cfg)
        for r in merged:
            a = ann.get(r.get("dedup_key"))
            if a:
                r["llm_score"], r["llm_note"] = a["llm_score"], a["llm_note"]
        shortlist.write_all(args.out, merged)  # the single write for the LLM path
        surfaced = shortlist.surface(merged, cfg)

    new_n = sum(1 for r in surfaced if r.get("_is_new"))
    err_tail = f"{len(errors)} feed errors"
    if errors and not args.verbose:
        err_tail += " (--verbose to see them)"
    print(
        f"\n{len(merged)} roles tracked · {len(surfaced)} on the shortlist · "
        f"{new_n} new since last run · {err_tail}"
    )
    if args.verbose and errors:
        for e in errors:
            print(f"    {e}")
    if discovered:
        print(
            f"+{len(discovered)} companies auto-discovered: "
            + ", ".join(f"{d['name']}({d['ats']})" for d in discovered[:10])
        )
    print()
    for r in surfaced[: args.limit]:
        print(_fmt(r, cfg))
    print(f"\nFull list: {args.out}  ·  apply: job-radar apply <id>")
    if args.strict and errors:  # opt-in: a partial failure is a failure
        raise SystemExit(1)


def cmd_init(args, cfg):
    """Write a starter job-radar.yaml (+ watchlist.json) into the cwd from the
    packaged examples. Refuses to overwrite existing files."""
    wrote, skipped = [], []
    for src, dst in (
        ("job-radar.example.yaml", "job-radar.yaml"),
        ("watchlist.example.json", "watchlist.json"),
    ):
        if Path(dst).exists():
            skipped.append(dst)
            continue
        Path(dst).write_text(_packaged(src), encoding="utf-8")
        wrote.append(dst)
    if wrote:
        print("✓ created " + ", ".join(wrote))
    for s in skipped:
        print(f"  kept existing {s} (not overwritten)")
    print("edit job-radar.yaml to make it yours, then run `job-radar`.")


def cmd_status(args, cfg, status):
    ok = shortlist.mark_status(args.out, args.id, status)
    if ok:
        print(f"✓ {args.id} -> {status}")
    else:
        print(f"no role with id {args.id} in {args.out}")
        raise SystemExit(1)  # nonzero so a typo'd id in a script is detectable


def cmd_list(args, cfg):
    rows = shortlist.load_all(args.out)
    if not rows:
        print(f"no shortlist yet — run `job-radar` first ({args.out} not found).")
        return
    if args.all:
        rows = sorted(
            rows, key=lambda r: shortlist._safe_int(r.get("score")), reverse=True
        )
    else:
        rows = shortlist.surface(rows, cfg)
    for r in rows[: args.limit]:
        print(_fmt(r, cfg))
    print(f"\n{len(rows)} shown · full file: {args.out}")


def main(argv=None):
    # Force UTF-8 on our streams so the ✓/⚠/↳ glyphs and non-ASCII job titles never
    # crash a run on a cp1252-defaulted Windows console or a redirected stdout (a
    # scheduled task logging to a file). Worst case a glyph degrades to '?'.
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    # Shared options attached to BOTH the top level and each subcommand, so they
    # work in either position (`job-radar --limit 5` and `job-radar list --limit 5`).
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--config", help="path to job-radar.yaml (default: ./job-radar.yaml)"
    )
    common.add_argument(
        "--out", default="shortlist.csv", help="the shortlist CSV store"
    )
    common.add_argument(
        "--watchlist", help="watchlist.json (companies to poll directly)"
    )
    common.add_argument("--limit", type=int, default=25, help="how many to print")
    common.add_argument(
        "--verbose",
        action="store_true",
        help="print the per-source error list (which feeds failed and why)",
    )
    common.add_argument(
        "--strict",
        action="store_true",
        help="exit nonzero if ANY source errored (for scheduled runs / CI)",
    )

    ap = argparse.ArgumentParser(
        prog="job-radar", description="Find roles that fit you.", parents=[common]
    )
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser(
        "init",
        parents=[common],
        help="write a starter job-radar.yaml + watchlist.json into this folder",
    )
    sub.add_parser(
        "scan",
        parents=[common],
        help="poll all sources, score, update the shortlist (default)",
    )
    for name, past in (("apply", "applied"), ("dismiss", "dismissed")):
        s = sub.add_parser(name, parents=[common], help=f"mark a role {past}")
        s.add_argument("id")
    pl = sub.add_parser("list", parents=[common], help="show the current shortlist")
    pl.add_argument("--all", action="store_true", help="include applied/dismissed")
    sd = sub.add_parser(
        "seed",
        parents=[common],
        help="bulk-add companies from Common Crawl (build the universe)",
    )
    # Derived, never a literal: this list was hardcoded here while the miner's own
    # pattern table said something different, so `workday` was minable but not
    # selectable. One source of truth.
    from .discover import _PATTERNS as _MINEABLE

    sd.add_argument(
        "ats",
        choices=sorted(_MINEABLE),
        help="which ATS to enumerate (workday entries carry the host+site triple)",
    )
    sd.add_argument(
        "--max",
        type=int,
        default=500,
        help="max companies to add this run (its own limit, not the print --limit)",
    )
    sd.add_argument(
        "--verify",
        action="store_true",
        help="probe each slug (slower; only add live boards)",
    )
    args = ap.parse_args(argv)

    cfg = _resolve_config(args.config)
    config.set_active(cfg)

    if args.cmd == "init":
        cmd_init(args, cfg)
    elif args.cmd in (None, "scan"):
        cmd_scan(args, cfg)
    elif args.cmd == "apply":
        cmd_status(args, cfg, "applied")
    elif args.cmd == "dismiss":
        cmd_status(args, cfg, "dismissed")
    elif args.cmd == "list":
        cmd_list(args, cfg)
    elif args.cmd == "seed":
        cmd_seed(args, cfg)


def cmd_seed(args, cfg):
    from . import seed

    wl = args.watchlist or "watchlist.json"
    try:
        n = seed.seed_universe(args.ats, wl, limit=args.max, verify=args.verify)
    except seed.SeedError as e:
        print(f"seed unavailable ({e}) — try again later.")
        raise SystemExit(1) from None
    print(
        f"✓ added {n} {args.ats} companies to {wl} "
        f"(raise --max to add more; run `job-radar` to scan them)"
    )


if __name__ == "__main__":
    main()
