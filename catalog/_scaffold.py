#!/usr/bin/env python3
"""Turn probe evidence into a profile draft: `_out/<name>.txt` + `_raw/<name>.json` -> `<name>.md`.

Every mechanical section is assembled from the evidence files rather than retyped,
because the schema requires `## A real record` to be a genuine capture — "never
hand-written from memory or reconstructed from the fields: table" — and a human
copying JSON between files is exactly how a reconstruction sneaks in.

What it CANNOT write, and marks `TODO` so a draft can never be mistaken for a profile:
`verdict`, `license` (a contract: terms read, clauses quoted, `read_at` stamped),
`traps`, and the two prose sections that need judgement. `--check` lists what is still
outstanding across the folder.

    python catalog/_scaffold.py                # draft every probed source
    python catalog/_scaffold.py --check        # what is still TODO
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

import yaml

HERE = pathlib.Path(__file__).parent
OUT, RAW = HERE / "_out", HERE / "_raw"
TODO = "TODO"

# Which lane and status a source is in — the one thing here that is a fact about this
# package rather than about the API, so it cannot come from a probe.
LANES = {
    "greenhouse": ("depth", "wired"),
    "ashby": ("depth", "wired"),
    "lever": ("depth", "wired"),
    "rippling": ("depth", "evaluated"),
    "teamtailor": ("depth", "evaluated"),
    "remotive": ("breadth", "wired"),
    "jobicy": ("breadth", "wired"),
    "remoteok": ("breadth", "wired"),
    "himalayas": ("breadth", "wired"),
    "braintrust": ("breadth", "wired"),
    "techtree": ("breadth", "wired"),
    "arbeitnow": ("breadth", "wired"),
    "4dayweek": ("breadth", "rejected"),
    "devitjobs": ("breadth", "rejected"),
    "weworkremotely": ("breadth", "evaluated"),
    "nodesk": ("breadth", "evaluated"),
    "jobspresso": ("breadth", "evaluated"),
    "workingnomads": ("breadth", "evaluated"),
    "landing.jobs": ("breadth", "evaluated"),
}


def parse_out(text: str) -> tuple[dict, list[str], str]:
    """Split a probe run into (measured values, probe log, body-chars note)."""
    values, log, body = {}, [], ""
    for line in text.splitlines():
        if line.startswith("#   "):
            log.append(line[4:])
        elif line.startswith("# body:"):
            body = line[2:]
        elif m := re.match(r"^\s{2}(\w+): (.*)$", line):
            values[m.group(1)] = m.group(2).split("  #")[0].strip()
    return values, log, body


def truncate(record: dict, limit: int = 600) -> dict:
    """Long text truncated with an EXPLICIT marker, as the schema's example does."""
    out = {}
    for k, v in record.items():
        if isinstance(v, str) and len(v) > limit:
            out[k] = v[:limit] + f"…[truncated, {len(v)} chars total]"
        else:
            out[k] = v
    return out


TARGETS = json.loads((HERE / "_targets.json").read_text(encoding="utf-8"))


def target(name: str) -> dict:
    for lane in ("breadth", "depth"):
        if name in TARGETS[lane]:
            return TARGETS[lane][name]
    return {}


# Keys whose meaning is unambiguous across vendors, so the record can fill them. The
# ones deliberately absent are the ones the schema says get mis-mapped by name:
# `department` is an org unit in one API and the EMPLOYER in another, so it is never
# auto-assigned — see _SCHEMA.md "Why function, org_unit and employer_org are three keys".
COMMON = {
    "title": ("title", "jobTitle", "position", "text", "name"),
    "company": ("company_name", "companyName", "company", "employer"),
    "body": (
        "description",
        "jobDescription",
        "content_html",
        "role_description",
        "content",
        "descriptionHtml",
    ),
    "posted": (
        "publication_date",
        "pubDate",
        "date",
        "created_at",
        "createdAt",
        "posted",
        "published_at",
        "date_published",
        "pub_date",
        "activeFrom",
    ),
    "url": ("url", "applicationLink", "application_url", "hostedUrl", "absolute_url"),
    "tags": ("tags", "job_skills", "main_skills", "filterTags"),
    "salary": (
        "salary",
        "salaryMin",
        "minSalary",
        "annualSalaryFrom",
        "gross_salary_low",
        "budget_minimum_usd",
    ),
    "employment_type": (
        "job_type",
        "jobType",
        "employmentType",
        "contract_type",
        "schedule_type",
    ),
}


def field_map(record: dict) -> str:
    """Map only what a key's NAME proves. Everything ambiguous stays TODO on purpose."""
    lines, claimed = [], set()
    for canon, candidates in COMMON.items():
        hit = next((c for c in candidates if c in record), None)
        if hit:
            claimed.add(hit)
            v = record[hit]
            extra = (
                f", median_chars: {len(v)}"
                if canon == "body" and isinstance(v, str)
                else ""
            )
            lines.append(
                f"  {canon}: {{ path: {hit}, type: {type(v).__name__}{extra} }}"
            )
        else:
            lines.append(f"  {canon}: null")
    for canon in ("function", "org_unit", "employer_org", "seniority"):
        lines.append(f"  {canon}: {TODO} # see _SCHEMA.md — never assign these by name")
    leftover = [k for k in record if k not in claimed]
    if leftover:
        lines.append(f"  # unmapped keys in the record: {', '.join(sorted(leftover))}")
    return "\n".join(lines)


def draft(name: str) -> str:
    values, log, body = parse_out((OUT / f"{name}.txt").read_text(encoding="utf-8"))
    record = json.loads((RAW / f"{name}.json").read_text(encoding="utf-8"))
    lane, status = LANES.get(name, ("breadth", TODO))
    date = values.get("measured_at", TODO)
    t = target(name)
    fields = field_map(record)
    base = t.get("base", TODO)
    display = name.replace(".", " ").replace("-", " ").title()
    return f"""---
name: {name}
display_name: {display}
status: {status}
lane: {lane}
verdict: >
  {TODO} — one line, why it is in that status.

auth:
  type: none
  env: []
  signup: null
  notes: keyless

license: # {TODO} — terms NOT read. Do not guess; see _SCHEMA.md "Rights are a contract"
  commercial_use: unknown
  commercial_terms: {TODO}
  attribution_required: unknown
  redistribution: unknown
  derivative_works: unknown
  cache_policy: unknown
  on_termination: unknown
  personal_data: unknown
  terms_url: {TODO}
  read_at: null # REQUIRED once read — an undated claim is not evidence
  read_depth: null
  verbatim: []

endpoint:
  base: {base}
  method: GET
  slug_pattern: {"null" if lane == "breadth" else TODO}

query:
  title_search: {values.get("title_search", "unknown")}
  location_search: unknown
  filters: []
  param_validation: {values.get("param_validation", "unknown")}

limits:
  page_size: {values.get("page_size", "unknown")}
  max_page: {values.get("max_page", "unknown")}
  reachable_per_query: {values.get("reachable_per_query", "unknown")}
  rate_limit: {values.get("rate_limit", "unknown")}
  quota: unknown
  concurrency_safe: unknown
{f"  requests_per_company: {TODO}{chr(10)}  cheap_liveness: {TODO}" if lane == "depth" else ""}
volume:
  advertised: unknown
  reachable: unknown
  measured_at: {date}

freshness:
  median_age_days: {values.get("median_age_days", "unknown")}
  pct_within_100d: {values.get("pct_within_100d", "unknown")}
  date_sorted: {values.get("date_sorted", "unknown")}
  measured_at: {date}

coverage:
  countries: [{TODO}]
  us_share: unknown
  sector_skew: {TODO}

location:
  shape: {TODO}
  primary_path: {TODO}
  type: {TODO}
  state_available: {TODO}
  state_path: null
  city_path: null
  country_path: null
  multi_value: unknown
  multi_delimiter: null
  free_text_fallback: null
  gazetteer_needed: unknown

remote:
  signal: {TODO}
  path: null
  rule: null
  reliability: unknown

fields: # mapped from the captured record below; ambiguous ones left TODO
{fields}

traps:
  - {TODO}
---

# {name}

## A real record

Captured {date} from the base endpoint. {"Long text truncated with a marker; rest verbatim." if any(isinstance(v, str) and len(v) > 600 for v in record.values()) else "Verbatim."}

```json
{json.dumps(truncate(record), indent=2, ensure_ascii=False)}
```

**What this record proves that the field table did not.** {TODO}

## How this was probed

`catalog/_probe.py` on {date}, one request per second, against the live API.
{body and chr(10) + body + chr(10)}
```
{chr(10).join(log)}
```

**Not checked:** the licence terms (step 7) — `license:` above is `unknown`, not
permissive. {TODO}: anything else left unprobed.
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="report outstanding TODOs")
    ap.add_argument("--force", action="store_true", help="overwrite existing drafts")
    ap.add_argument("names", nargs="*")
    a = ap.parse_args()

    if a.check:
        rows = []
        for f in sorted(HERE.glob("*.md")):
            if f.name.startswith("_") or f.name == "INDEX.md":
                continue
            text = f.read_text(encoding="utf-8")
            n = text.count(TODO)
            # Parse the frontmatter, because "no TODOs left" is not the same as "usable".
            # Three profiles reached zero TODOs carrying YAML that does not load: an
            # unquoted `departments[].name` inside a flow mapping starts a flow SEQUENCE,
            # and `{country}` inside a flow sequence starts a flow MAPPING. _rollup.py
            # reads these files, so an unparseable one is a broken profile that looks
            # finished.
            err = ""
            try:
                fm = yaml.safe_load(text.split("---")[1])
            except Exception as e:
                fm = None
                err = f"  !! YAML: {getattr(e, 'problem', e)}"
            # REQUIRED keys, per _SCHEMA.md. A panel review deleted `license.read_at`
            # -- which the schema marks REQUIRED -- and this still printed "complete",
            # because "no TODOs left" is not the same as "has what the schema demands".
            if isinstance(fm, dict) and not err:
                missing = [
                    k for k in ("name", "status", "lane", "license") if k not in fm
                ]
                lic = fm.get("license")
                if isinstance(lic, dict) and "read_at" not in lic:
                    missing.append("license.read_at")
                if missing:
                    err = f"  !! MISSING: {', '.join(missing)}"
            rows.append((f.name, n, err))
        width = max(len(n) for n, _, _ in rows)
        for name, n, err in rows:
            state = "complete" if not n else f"{n} TODO"
            print(f"  {name:<{width}}  {state}{err}")
        done = sum(1 for _, n, err in rows if not n and not err)
        print(f"\n{done}/{len(rows)} complete and parsing")
        # NONZERO when anything is incomplete. This returned 0 unconditionally, so
        # `--check` reported problems and still passed: a planted TODO, a planted YAML
        # break and a deleted required key were all printed with exit code 0. A gate
        # that always passes is not a gate, and CI could never have caught any of it.
        return 0 if done == len(rows) else 1

    names = a.names or sorted(p.stem for p in OUT.glob("*.txt"))
    for name in names:
        dest = HERE / f"{name}.md"
        if dest.exists() and not a.force:
            print(f"  skip {name} (exists)", file=sys.stderr)
            continue
        if not (RAW / f"{name}.json").exists():
            print(f"  skip {name} (no captured record)", file=sys.stderr)
            continue
        dest.write_text(draft(name), encoding="utf-8")
        print(f"  wrote {dest.relative_to(HERE.parent)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
