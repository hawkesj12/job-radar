"""The store: a single upserted CSV (shortlist.csv).

One inspectable file, keyed by dedup_key. Each scan UPSERTS -- refresh score /
age on roles it's seen before, PRESERVE their status and first_seen, add new
roles. Applied/dismissed roles are 'sticky': they persist even after they leave
the market, so your application history is never lost. Written atomically
(temp -> os.replace), so an interrupted write leaves the previous file intact.

Columns: id, first_seen, posted, age_days, score, llm_score, llm_note, status,
salary, company, industry, title, department, employment_type, location,
source, url, signals, dedup_key
"""

from __future__ import annotations

import csv
import hashlib
import io
from pathlib import Path

from .dedup import dedup_key
from .util import age_int, atomic_write_text, today_et

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
    "department",
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
# Free-text columns fed by untrusted job-API data -- guarded against CSV/formula
# injection so a hostile title like `=cmd|...` can't execute in Excel/Sheets.
TEXT_COLS = {
    "salary",
    "company",
    "industry",
    "title",
    "department",
    "employment_type",
    "location",
    "source",
    "url",
    "signals",
    "llm_note",
}


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
    with p.open(newline="", encoding="utf-8") as f:
        return [dict(row) for row in csv.DictReader(f)]


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
                c: (_csv_safe(r.get(c, "")) if c in TEXT_COLS else r.get(c, ""))
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
        "department": p.get("department", ""),
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


def mark_status(path, job_id: str, status: str) -> bool:
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
