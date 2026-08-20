"""The store: a single upserted CSV (shortlist.csv).

One inspectable file, keyed by dedup_key. Each scan UPSERTS -- refresh score /
age on roles it's seen before, PRESERVE their status and first_seen, add new
roles. Applied/dismissed roles are 'sticky': they persist even after they leave
the market, so your application history is never lost. Written atomically
(temp -> os.replace), so an interrupted write leaves the previous file intact.

Columns: id, first_seen, posted, age_days, score, llm_score, llm_note, status,
salary, company, industry, title, employment_type, location,
source, url, signals, dedup_key

THIS IS NOT THE RECORD CONTRACT, and the difference is deliberate. These 19 columns
are a HUMAN shortlist you open in a spreadsheet; `emit.py`'s NDJSON is the machine
contract, and a test asserts every `engine._CONTRACT_FIELDS` member appears there.
So contract fields land here only when a person reading a row wants them —
`seniority`, `seniority_raw`, `category`, `team` and `tags` are all deliberately
absent, and `seniority_raw` in particular would be incoherent without the
`seniority` beside it that this file also does not carry. Adding a column is a
schema change for every existing CSV, which is a real cost to a CLI user; decide it
here on purpose rather than letting a field appear in one surface and vanish from
the other by accident.
"""

from __future__ import annotations

import codecs
import contextlib
import csv
import hashlib
import io
import os
import sys
import time
from pathlib import Path

from .dedup import dedup_key
from .util import age_int, atomic_write_text, today_et


class ShortlistEncodingError(Exception):
    """The store exists but isn't text we can read. Its own type so a caller can
    tell "your file is in the wrong encoding" (actionable, the user fixes it) from
    "the file is corrupt" -- and so the CLI can print the advice instead of a
    codec traceback."""


@contextlib.contextmanager
def _exclusive(path):
    """Hold an exclusive advisory lock across a read-modify-write of the store.

    The write itself is already atomic, so the file can never be torn -- but atomic
    is not the same as serialized. `upsert` and `mark_status` both LOAD the whole
    file, change it in memory, and write it back, so two overlapping processes both
    read the pre-change state and the second write silently discards the first.
    Measured: `job-radar apply <id>` running during a scan loses the applied status
    and the role resurfaces -- which is exactly the thing this store exists to
    prevent, and the promise the README leads with.

    An OS lock on a descriptor, deliberately, not a lock FILE. The comment in
    funnel.append_watchlist rejects locking because "a lock file only risked getting
    stuck after a crash" -- correct about lock files, and not true here: this lock is
    held by the file DESCRIPTOR, so the kernel drops it when the process dies, crash
    included. There is nothing to get stuck.

    BOTH platforms are covered. POSIX gets fcntl.flock; Windows has no fcntl, so it
    gets msvcrt.locking over a one-byte range, polled to a deadline because its
    blocking mode gives up after ten seconds and a scan can run longer than that.
    The first version here fell straight through to "unlocked" on Windows, which
    meant a Windows user silently kept the very bug this function exists to fix --
    and the Windows CI cells caught it, because the test asserts the guarantee rather
    than the implementation.

    Still best-effort at the end: a filesystem that refuses the lock (some network
    mounts) proceeds unserialized rather than refusing to run. Losing the guarantee
    is bad; refusing to run at all is worse.
    """
    lock_path = Path(str(path) + ".lock")
    fd = None
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        _acquire(fd)
    except (ImportError, OSError, AttributeError):
        pass  # unlockable filesystem — carry on unserialized
    try:
        yield
    finally:
        if fd is not None:
            with contextlib.suppress(OSError):
                _release(fd)
            with contextlib.suppress(OSError):
                os.close(fd)


# `sys.platform` rather than try/ImportError, because mypy NARROWS on it: each
# branch is type-checked only on the platform that runs it. CI runs mypy in every
# matrix cell, so the Windows branch is checked on Windows and the POSIX branch on
# Linux and macOS -- an ImportError fallback would leave one of them unchecked
# everywhere.
if sys.platform == "win32":

    def _acquire(fd, deadline_s: float = 120.0) -> None:
        import msvcrt

        # LK_LOCK's own blocking mode gives up after ~10s, which is shorter than a
        # scan. Poll the non-blocking variant so a long scan is waited out instead.
        end = time.monotonic() + deadline_s
        while True:
            try:
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                return
            except OSError:
                if time.monotonic() >= end:
                    raise
                time.sleep(0.05)

    def _release(fd) -> None:
        import msvcrt

        os.lseek(fd, 0, os.SEEK_SET)  # release the same byte range that was locked
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)

else:

    def _acquire(fd, deadline_s: float = 120.0) -> None:
        import fcntl  # blocks until the holder releases

        fcntl.flock(fd, fcntl.LOCK_EX)

    def _release(fd) -> None:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_UN)


COLUMNS = [
    "id",
    "first_seen",
    "posted",
    "age_days",
    "score",
    "llm_score",
    "llm_note",
    "status",
    "salary",
    "company",
    "industry",
    "title",
    "employment_type",
    "location",
    "source",
    "url",
    "signals",
    "dedup_key",
]

# Statuses the user set by hand -- these rows persist even if they leave the feed.
STICKY = {"applied", "dismissed", "interviewing", "screen", "offer", "rejected"}
# Statuses hidden from the surfaced shortlist (you don't want these resurfacing).
# interviewing/offer stay VISIBLE -- those are live and worth seeing.
HIDDEN = {"applied", "dismissed", "rejected"}
# There is deliberately NO "untrusted columns" list here any more. There was one --
# TEXT_COLS -- and `posted` was missing from it while being fed straight from vendor
# data through `to_date`, so a hostile board could land a live formula in the sheet.
# Curating which columns are dangerous is a judgement that has to be re-made every
# time a column is added, and it was already wrong. write_all now sanitizes every
# column unconditionally; the cost is a leading apostrophe on a value that could
# never have been a formula anyway.


def _safe_int(v, default: int = 0) -> int:
    """Tolerate hand-edited/decimal/garbage cells (the CSV is user-editable)."""
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


def _csv_safe(v) -> str:
    """Neutralize spreadsheet formula injection in untrusted text cells."""
    s = "" if v is None else str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


def short_id(key: str) -> str:
    return hashlib.sha1(key.encode()).hexdigest()[:7]


def load_all(path) -> list[dict]:
    p = Path(path)
    if not p.exists():
        return []
    # utf-8-SIG, not utf-8. Excel writes a UTF-8 BOM when it saves a CSV, and with a
    # plain utf-8 read those three bytes become part of the FIRST HEADER NAME:
    # the column is then "﻿id" rather than "id", so every row's `id` lookup
    # returns None and `apply`/`dismiss` report "no role with id ..." forever. The
    # file looks perfect in a spreadsheet, which is what makes it so hard to
    # diagnose. utf-8-sig strips a BOM if present and is identical to utf-8 if not.
    #
    # A store that isn't UTF-8 at all used to raise a bare UnicodeDecodeError out of
    # this line, and load_all is on the path of EVERY command -- so `list`, `apply`
    # and `dismiss` all died with a codec traceback naming a byte offset. The user
    # could not even reach their own file to fix it. Excel on Windows will happily
    # save a CSV as UTF-16 ("Unicode Text"), so this is a two-click mistake, not an
    # exotic one. Sniff the BOM and decode it; anything genuinely undecodable
    # raises a message that names the file and what to do about it. Fail loud, but
    # fail USEFULLY.
    raw = p.read_bytes()
    for bom, enc in ((codecs.BOM_UTF16_LE, "utf-16"), (codecs.BOM_UTF16_BE, "utf-16")):
        if raw.startswith(bom):
            text = raw.decode(enc)
            break
    else:
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError as e:
            raise ShortlistEncodingError(
                f"{p}: not UTF-8 or UTF-16 ({e.reason} at byte {e.start}). "
                "Re-save it as CSV UTF-8 -- in Excel, File > Save As > "
                '"CSV UTF-8 (Comma delimited)".'
            ) from e
    return [dict(row) for row in csv.DictReader(io.StringIO(text, newline=""))]


def write_all(path, rows: list[dict]) -> None:
    """Atomic write: render to a string, then a unique-temp-file + os.replace via
    atomic_write_text (so concurrent runs can't collide on a fixed temp name)."""
    rows = sorted(rows, key=lambda r: _safe_int(r.get("score")), reverse=True)
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=COLUMNS, extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow(
            {
                # Sanitize EVERY column, not a curated subset. The subset was the
                # bug: `posted` was left out, and it is fed from vendor data, so a
                # board could land a live formula in the sheet. A maintained list of
                # "the untrusted ones" is a list that will be wrong again.
                c: _csv_safe(r.get(c, ""))
                for c in COLUMNS
            }
        )
    atomic_write_text(path, buf.getvalue())


def _build_row(p: dict, today: str) -> dict:
    key = p.get("dedup_key") or dedup_key(p)  # reuse the engine's key when present
    a = age_int(p.get("posted", ""))
    src = ", ".join(
        sorted(p.get("sources") or ([p.get("source")] if p.get("source") else []))
    )
    return {
        "id": short_id(key),
        "first_seen": today,
        "posted": p.get("posted", ""),
        "age_days": "" if a is None else str(a),
        "score": p.get("score", 0),
        "llm_score": "",
        "llm_note": "",
        "status": "new",
        "salary": p.get("salary", ""),
        "company": p.get("company", ""),
        "industry": p.get("industry", ""),
        "title": (p.get("title", "") or "").strip(),
        "employment_type": p.get("employment_type", ""),
        "location": (p.get("location", "") or "").strip(),
        "source": src,
        "url": p.get("url", ""),
        "signals": p.get("signals", ""),
        "dedup_key": key,
    }


def upsert(
    path, postings: list[dict], today: str | None = None, write: bool = True
) -> list[dict]:
    """Merge this run's scored postings into the store. Returns the merged rows,
    each tagged `_is_new` (True if first seen this run). Pass `write=False` to
    skip the file write when the caller will annotate + write once itself (the
    LLM path), avoiding a redundant full rewrite."""
    today = today or today_et()
    # The lock spans the whole read-modify-write, not just the write. A scan that
    # READ before a concurrent `apply` and WROTE after it would put the pre-apply
    # rows back, silently un-applying the role -- locking only the write does not
    # help, because the stale data was already in hand.
    with _exclusive(path) if write else contextlib.nullcontext():
        return _upsert_locked(path, postings, today, write)


def _upsert_locked(path, postings, today, write):
    existing = load_all(path)
    by_key = {r.get("dedup_key"): r for r in existing if r.get("dedup_key")}
    # A role's URL is stable even when a recruiter re-titles it (which changes its
    # dedup_key). Index by URL so a re-titled role inherits its prior status
    # instead of resurfacing as a brand-new row you might re-apply to.
    by_url = {r.get("url"): r for r in existing if r.get("url")}

    result: dict[str, dict] = {}
    # 1) carry forward sticky rows (applied/dismissed/etc.) even if not seen now
    for r in existing:
        if r.get("status") in STICKY and r.get("dedup_key"):
            r["_is_new"] = False
            result[r["dedup_key"]] = r
    # 2) upsert this run's postings
    for p in postings:
        row = _build_row(p, today)
        k = row["dedup_key"]
        old = by_key.get(k)
        if old is None and row.get("url"):
            old = by_url.get(row["url"])  # exact key missed -> try the stable URL
        if old:
            row["first_seen"] = old.get("first_seen") or today
            row["status"] = old.get("status") or "new"
            row["llm_score"] = old.get("llm_score", "")
            row["llm_note"] = old.get("llm_note", "")
            row["_is_new"] = False
            old_key = old.get("dedup_key")
            if old_key and old_key != k:  # re-titled: drop the stale old-key row
                result.pop(old_key, None)
        else:
            row["_is_new"] = True
        result[k] = row

    merged = list(result.values())
    if write:
        write_all(path, merged)
    return merged


def annotate(path, by_key: dict) -> None:
    """Apply LLM scores/notes to the stored rows, under the lock.

    The LLM path cannot use `upsert(write=True)`: it reads, then makes a network
    call that can take many seconds, then writes. Writing the rows it read would
    discard anything that changed during the call -- an `apply`, or a second scan.
    So re-read here, graft the annotations on by dedup_key, and write that.
    `by_key` maps dedup_key -> {"llm_score": ..., "llm_note": ...}.
    """
    if not by_key:
        return
    with _exclusive(path):
        rows = load_all(path)
        for r in rows:
            a = by_key.get(r.get("dedup_key"))
            if a:
                r["llm_score"] = a.get("llm_score", r.get("llm_score", ""))
                r["llm_note"] = a.get("llm_note", r.get("llm_note", ""))
        write_all(path, rows)


def mark_status(path, job_id: str, status: str) -> bool:
    # Read-modify-write under the lock: a concurrent scan reading the pre-change
    # rows and writing them back is what silently un-applied a role.
    with _exclusive(path):
        rows = load_all(path)
        hit = False
        for r in rows:
            if r.get("id") == job_id:
                r["status"] = status
                hit = True
        if hit:
            write_all(path, rows)
        return hit


def surface(
    rows: list[dict], cfg, only_new: bool = False, limit: int | None = None
) -> list[dict]:
    """Filter to the roles worth showing: not applied/dismissed, above min_score,
    within max_age_days; sorted by (llm_score if present, else score) desc."""
    out = []
    for r in rows:
        if r.get("status") in HIDDEN:  # applied / dismissed / rejected
            continue
        if _safe_int(r.get("score")) < cfg.min_score:
            continue
        a = r.get("age_days")
        if a not in (None, "") and _safe_int(a) > cfg.max_age_days:
            continue
        if only_new and not r.get("_is_new"):
            continue
        out.append(r)
    out.sort(
        key=lambda r: (
            _safe_int(r["llm_score"])
            if str(r.get("llm_score")).strip().isdigit()
            else _safe_int(r.get("score"))
        ),
        reverse=True,
    )
    return out[:limit] if limit else out
