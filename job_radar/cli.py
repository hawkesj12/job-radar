"""Command-line interface: scan / apply / dismiss / list."""

from __future__ import annotations

import argparse
import importlib.resources as resources
from datetime import datetime
from pathlib import Path

from . import config, engine, llm, store
from .dedup import dedup_key


def _packaged(name: str) -> str:
    """Read a file shipped in job_radar/data/ (works from a wheel or the repo)."""
    return (resources.files("job_radar") / "data" / name).read_text()


def _resolve_config(path_arg):
    for cand in (path_arg, "job-radar.yaml", "job-radar.example.yaml"):
        if cand and Path(cand).exists():
            return config.load_config(cand)
    return config.load_config(None)  # generic defaults


def _fmt(r) -> str:
    sc = r.get("llm_score") or r.get("score")
    tag = f"[{r.get('id')}]"
    head = f"  {tag} {str(sc):>3}  {r.get('title', '')[:52]:52}  {r.get('company', '')[:24]}"
    extra = []
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
            Path(wl).write_text(_packaged("watchlist.example.json"))
            print(f"note: no watchlist found — seeded {wl} from the starter list.")
        except (OSError, ModuleNotFoundError, FileNotFoundError):
            print("note: no watchlist — running breadth sources only.")
            wl = None

    print("scanning…")
    rows, discovered, errors = engine.harvest(cfg, wl)
    by_key = {dedup_key(p): p for p in rows}
    today = datetime.now().strftime("%Y-%m-%d")
    merged = store.upsert(args.out, rows, today)

    surfaced = store.surface(merged, cfg)
    targets = surfaced[: cfg.llm.rerank_top_n]

    if cfg.llm.enabled:
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
        if ann:
            for r in merged:
                a = ann.get(r.get("dedup_key"))
                if a:
                    r["llm_score"], r["llm_note"] = a["llm_score"], a["llm_note"]
            store.write_all(args.out, merged)
            surfaced = store.surface(merged, cfg)

    new_n = sum(1 for r in surfaced if r.get("_is_new"))
    print(
        f"\n{len(merged)} roles tracked · {len(surfaced)} on the shortlist · "
        f"{new_n} new since last run · {len(errors)} feed errors"
    )
    if discovered:
        print(
            f"+{len(discovered)} companies auto-discovered: "
            + ", ".join(f"{d['name']}({d['ats']})" for d in discovered[:10])
        )
    print()
    for r in surfaced[: args.limit]:
        print(_fmt(r))
    print(f"\nFull list: {args.out}  ·  apply: job-radar apply <id>")


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
        Path(dst).write_text(_packaged(src))
        wrote.append(dst)
    if wrote:
        print("✓ created " + ", ".join(wrote))
    for s in skipped:
        print(f"  kept existing {s} (not overwritten)")
    print("edit job-radar.yaml to make it yours, then run `job-radar`.")


def cmd_status(args, cfg, status):
    ok = store.mark_status(args.out, args.id, status)
    print(
        f"{'✓' if ok else 'no match for id'} {args.id} -> {status}"
        if ok
        else f"no role with id {args.id} in {args.out}"
    )


def cmd_list(args, cfg):
    rows = store.load_all(args.out)
    if not rows:
        print(f"no shortlist yet — run `job-radar` first ({args.out} not found).")
        return
    if args.all:
        rows = sorted(rows, key=lambda r: int(r.get("score") or 0), reverse=True)
    else:
        rows = store.surface(rows, cfg)
    for r in rows[: args.limit]:
        print(_fmt(r))
    print(f"\n{len(rows)} shown · full file: {args.out}")


def main(argv=None):
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
    for name in ("apply", "dismiss"):
        s = sub.add_parser(name, parents=[common], help=f"mark a role {name}")
        s.add_argument("id")
    pl = sub.add_parser("list", parents=[common], help="show the current shortlist")
    pl.add_argument("--all", action="store_true", help="include applied/dismissed")
    sd = sub.add_parser(
        "seed",
        parents=[common],
        help="bulk-add companies from Common Crawl (build the universe)",
    )
    sd.add_argument(
        "ats", choices=["greenhouse", "lever", "ashby", "workable", "smartrecruiters"]
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
    n = seed.seed_universe(args.ats, wl, limit=args.limit, verify=args.verify)
    print(
        f"✓ added {n} {args.ats} companies to {wl} "
        f"(raise --limit to add more; run `job-radar` to scan them)"
    )


if __name__ == "__main__":
    main()
