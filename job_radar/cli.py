"""Command-line interface: scan / apply / dismiss / list."""

from __future__ import annotations

import argparse
import importlib.resources as resources
import json
import sys
from pathlib import Path

from . import __version__, attribution, config, emit, engine, funnel, llm, shortlist
from .dedup import dedup_key
from .util import today_et


def _packaged(name: str) -> str:
    """Read a file shipped in job_radar/data/ (works from a wheel or the repo)."""
    return (resources.files("job_radar") / "data" / name).read_text(encoding="utf-8")


def _resolve_config(path_arg):
    """Load the config, honoring the REDIRECT keys only from an explicit --config.

    `./job-radar.yaml` is picked up automatically, which is the documented workflow
    and worth keeping. But it means a config file you did not write is honored just
    because you `cd`'d into a directory containing one, and two of its keys are not
    ordinary settings: `llm.base_url` chooses the host a request goes to, and the
    `*_key_env` names choose which environment variable travels with it. Together
    they are enough to make job-radar POST your ANTHROPIC_API_KEY to a stranger's
    server -- no network attacker required.

    So a DISCOVERED config keeps everything except those keys, which revert to the
    defaults with a notice (see config.without_redirects). Naming the file with
    --config is the opt-in.
    """
    # A named config that is not there is a TYPO, not a cue to look elsewhere. This
    # used to fall through to ./job-radar.yaml, so `--config ~/tuned.yaml` with a
    # slip in the path ran happily against a different config and reported nothing —
    # the same trust problem as the auto-discovery below, on the path where the user
    # was explicit about what they wanted.
    if path_arg and not Path(path_arg).exists():
        print(f"error: --config {path_arg}: no such file", file=sys.stderr)
        raise SystemExit(2)
    explicit = bool(path_arg)
    # `job-radar.example.yaml` used to be a third candidate here, for the case of
    # running straight from a clone where a repo-root copy sat beside the code. That
    # copy is gone -- the packaged one under job_radar/data/ is now the only copy, and
    # `init` is how it reaches a user's folder -- so the candidate could only ever
    # match a file the user put there themselves. Falling through to the generic
    # defaults below is the same configuration anyway: the example file encodes the
    # defaults, it does not change them.
    for cand in (path_arg, "job-radar.yaml"):
        if cand and Path(cand).exists():
            cfg = config.load_config(cand)
            if not explicit:
                cfg = config.without_redirects(cfg)
            return cfg
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
            for err in errors:
                print(f"    {err}")
        else:
            print("  (run with --verbose to see which sources failed)")
        for r in shortlist.surface(existing, cfg)[: args.limit]:
            print(_fmt(r, cfg))
        # Nonzero so a scheduled/cron wrapper can detect a dead run.
        raise SystemExit(1)
    by_key = {(p.get("dedup_key") or dedup_key(p)): p for p in rows}
    today = today_et()
    # ALWAYS persist the harvest here, LLM or not. This used to pass
    # `write=not llm_on` to save one rewrite, and that optimization cost the whole
    # scan: with the LLM enabled the merged rows were never written, and the
    # annotate() call below re-reads the file — which therefore did not contain
    # them — and writes that back. The harvest evaporated while the CLI printed
    # that it had tracked it. Two writes are cheap; a silently discarded scan is not.
    llm_on = cfg.llm.enabled
    merged = shortlist.upsert(args.out, rows, today)

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
        # NOT write_all(merged): `merged` was read before the LLM request, which can
        # take many seconds, so writing it back would discard anything that changed
        # during the call — an `apply` from another terminal, or a second scan.
        # annotate() re-reads under the lock and grafts the scores on by dedup_key.
        shortlist.annotate(args.out, ann)
        surfaced = shortlist.surface(merged, cfg)

    # NDJSON goes to STDOUT and everything else to STDERR, so `job-radar --format
    # ndjson > jobs.ndjson` produces a clean file and the human progress lines still
    # reach the terminal. A DB feed that has to be grepped out of a status report is
    # not a feed.
    # getattr, not args.format: tests and library callers construct a Namespace by
    # hand, and a new CLI flag must not turn "your caller predates this option" into
    # an AttributeError mid-scan, AFTER the network harvest has already happened.
    if getattr(args, "format", "text") == "ndjson":
        _emit_ndjson(args, cfg, merged, surfaced, errors, discovered, by_key)
        return

    new_n = sum(1 for r in surfaced if r.get("_is_new"))
    err_tail = f"{len(errors)} feed errors"
    if errors and not args.verbose:
        err_tail += " (--verbose to see them)"
    print(
        f"\n{len(merged)} roles tracked · {len(surfaced)} on the shortlist · "
        f"{new_n} new since last run · {err_tail}"
    )
    if args.verbose and errors:
        for err in errors:
            print(f"    {err}")
    if discovered:
        print(
            f"+{len(discovered)} companies auto-discovered: "
            + ", ".join(f"{d['name']}({d['ats']})" for d in discovered[:10])
        )
    print()
    for r in surfaced[: args.limit]:
        print(_fmt(r, cfg))
    # ATTRIBUTION. The terminal is job-radar's own display surface, so this is the
    # one obligation it can discharge itself rather than hand downstream: Remote OK
    # and Remotive both state they will revoke API access if their name is not shown
    # as the source. Only the sources that actually contributed are credited --
    # crediting a source that returned nothing is noise, and noise gets ignored.
    # `emit._sources`, not an inline `r.get("sources") or [r.get("source")]`. THE THIRD
    # EXIT of the same defect, missed when the other two were fixed. `merged` holds
    # STORE rows, whose `source` column is `", ".join(sorted(tokens))` -- so on a
    # cross-source merge the inline form yielded one token literally named
    # "adzuna, greenhouse", which resolves against no attribution entry and is dropped
    # in silence. 12 such fabricated tokens in a 7,568-row local harvest.
    #
    # Nobody is under-credited on that corpus, and the reason is luck rather than
    # design: every source in a merged row also appears in at least one single-source
    # row, so it gets credited there. A low-volume source whose rows ALL merged would
    # go uncredited with no error -- and adzuna and himalayas, both present in merged
    # rows here, are among the sources that require attribution as a condition of API
    # access. Latent, not harmless.
    credit = attribution.credit_line(
        {s for r in merged for s in (emit._sources(r) or ()) if s}
    )
    if credit:
        print(f"\n{credit}")
    print(f"\nFull list: {args.out}  ·  apply: job-radar apply <id>")
    if args.strict and errors:  # opt-in: a partial failure is a failure
        raise SystemExit(1)


def _emit_ndjson(args, cfg, merged, surfaced, errors, discovered, by_key):
    """Write the machine feed: rows to stdout, run manifest and status to stderr.

    `--all` emits every tracked role; the default emits only what `surface()` shows,
    which is the same set the human output prints. A store usually wants everything
    (it does its own filtering), so this is the one place `--all` is likely the
    normal choice rather than the exception.

    THE JOIN, and why it has to happen here. Two dicts describe the same role and
    each holds half of what the feed needs:

      * the HARVEST row (`by_key`) carries the 0.7.0 contract -- structured location,
        remote + basis, category/team/parent_company, salary, tags, seniority
      * the STORE row (`merged`) carries the HISTORY -- id, first_seen, status, and
        any llm_score

    The store is a nineteen-column CSV and deliberately does not persist the contract
    fields, so emitting straight from `merged` would produce a feed of nulls that
    looked structurally correct. Emitting straight from the harvest would lose
    `status`, so a consumer could not tell an applied role from a new one. Join them
    on dedup_key, store row first so history wins, contract fields grafted on top.
    """
    store_rows = merged if getattr(args, "all", False) else surfaced
    rows = []
    for r in store_rows:
        harvested = by_key.get(r.get("dedup_key")) or {}
        joined = dict(r)
        for k in engine._CONTRACT_FIELDS:
            joined[k] = harvested.get(k)
        # `text` is neither a contract field nor a store column -- the CSV has no room
        # for a 4 KB body and the contract treats it as plain text, not structure. So
        # nothing above grafts it, and the machine feed emitted "text": null on every
        # row while the human CSV never needed it. It is the entire input to the fit
        # score; a consumer re-scoring our rows cannot do it without the body.
        joined["text"] = harvested.get("text") or joined.get("text")
        joined["sources"] = harvested.get("sources") or joined.get("sources")
        rows.append(joined)
    # cfg, not args. The flags SET the config above; reading it back here means the
    # wire format and the library return are driven by one source of truth, and a
    # caller that set `cfg.include_text` in YAML gets the same shape without a flag.
    text = emit.records(rows, include_text=cfg.include_text, omit_empty=cfg.omit_empty)
    if text:
        print(text)
    print(emit.manifest(rows, errors, discovered, cfg), file=sys.stderr)
    if args.strict and errors:
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
        # getattr rather than a direct call: a replaced stdout (pytest's capture, a
        # StringIO in an embedding app) has no `reconfigure`, which the except below
        # already handled at runtime -- but only the lookup form is checkable, and
        # this file is now type-checked with check_untyped_defs.
        reconfigure = getattr(_stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except ValueError:
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
        "--format",
        choices=("text", "ndjson"),
        default="text",
        help="text (human, default) or ndjson (one JSON object per line, for a DB)",
    )
    common.add_argument(
        "--all",
        action="store_true",
        help="with --format ndjson: emit every tracked role, not just the shortlist",
    )
    # THE BODY IS 72% OF THE RECORD, and until now there was no way to ask for the
    # record without it. Measured on a 7,568-row local harvest `[0.9.0]`: the median
    # record is 8,717 bytes and 2,403 of them without `text` -- so anyone opening the
    # output to LOOK at it (rather than to score against it) is reading a job
    # description with a record buried in it.
    #
    # Opt-OUT, not opt-in: `text` is the entire input to the fit score, so a consumer
    # that re-scores our rows needs it and the default must keep it. This flag is for
    # reading, diffing, and eyeballing a harvest -- the case that had no answer.
    # `text_basis` goes with it, because it describes a field that is no longer there.
    common.add_argument(
        "--no-text",
        action="store_true",
        help="omit the description body (~72%% of the record)",
    )
    # THE PAIR. Ordering made the record scannable and removed nothing -- 19 of its 50
    # keys are null on a median row, so ordering alone delivers a tidier wall. These
    # two are what remove it, and neither does the job alone.
    common.add_argument(
        "--drop-empty",
        action="store_true",
        help="omit keys that are null or empty (a median record loses 19 of 50)",
    )
    common.add_argument(
        "--strict",
        action="store_true",
        help="exit nonzero if ANY source errored (for scheduled runs / CI)",
    )

    ap = argparse.ArgumentParser(
        prog="job-radar", description="Find roles that fit you.", parents=[common]
    )
    # Top level only, deliberately NOT on `common`: attaching it to every subparser
    # would make `job-radar list --version` print a version and exit, which reads
    # like the subcommand did something. It also reports the INSTALLED package's
    # version (job_radar.__version__ is what setuptools packages from), so running
    # this from a clone and from a wheel answer the same question honestly.
    ap.add_argument(
        "--version",
        action="version",
        version=f"job-radar {__version__}",
        help="print the installed version and exit",
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
    sub.add_parser("list", parents=[common], help="show the current shortlist")
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
    # THE FLAGS SET THE CONFIG, and the config is what everything downstream reads.
    # These are output SHAPE, and `engine.harvest` applies them to the flat rows it
    # RETURNS -- so a library caller gets them from YAML without going near argparse.
    # The first version of `--no-text` passed straight to `emit.records`, which made
    # the biggest lever on the record reachable from the CLI and from nowhere else;
    # `emit` is the one module the only known consumer imports nowhere.
    # Only override on an explicit flag, so a YAML setting is not silently reset.
    if getattr(args, "no_text", False):
        cfg.include_text = False
    if getattr(args, "drop_empty", False):
        cfg.omit_empty = True
    config.set_active(cfg)

    # An unreadable store is the one error that hits EVERY command -- list, apply,
    # dismiss and scan all load it -- so it is caught once, here, and printed as
    # advice rather than a codec traceback naming a byte offset.
    try:
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
    except shortlist.ShortlistEncodingError as e:
        print(f"⚠ {e}")
        raise SystemExit(1) from None


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
