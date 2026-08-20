# Contributing

Thanks for your interest. This is a small, focused tool — contributions are
welcome, but please **open an issue first** to discuss anything non-trivial so we
don't both build the same thing twice.

## Development setup

```bash
git clone https://github.com/hawkesj12/job-radar
cd job-radar
pip install -e ".[dev]"
```

`uv.lock` is committed but is **not** what you install. job-radar is a library and
ships loose version ranges, so `pip install job-radar` resolves fresh — the lockfile
exists so the weekly CVE scan has an exact dependency set to check, which it cannot
do from ranges alone. Regenerate it with `uv lock` if you change a dependency in
`pyproject.toml`; otherwise leave it to Dependabot.

## Before you open a PR

All three must pass. CI runs them on **Linux, macOS, and Windows** across Python
3.10–3.13, then builds the wheel, installs it into a clean virtualenv, and runs
`job-radar init` from outside the repo:

```bash
ruff check .
mypy
pytest -q
```

`mypy` reads its config from `pyproject.toml`, so no flags are needed. It is not
strict — this codebase passes untyped dicts between modules on purpose — but it
does catch calls that cannot work and containers built two different ways.

Windows is in the matrix as a real detector, not a formality: it defaults text I/O
to the system locale, and this tool prints `✓ ⚠ ↳ ★` and harvests job titles in
every language. If you touch anything that prints or writes a file, expect Windows
to be the cell that catches you.

### The live canary

`pytest` never touches the network. The tests in `tests/test_live_canary.py` do,
so they are marked `live` and deselected by default — a contributor working offline
must not see failures caused by someone else's outage. They run weekly from
`canary.yml`:

```bash
pytest -m live -rs      # ask the real APIs whether our parsers still fit
```

They exist because the fixture tests cannot catch vendor drift. A captured payload
freezes an API's shape as it was the day it was written, so if a source renames a
field tomorrow, every fixture test still passes while the live harvest silently
returns blank rows. The canary distinguishes **unreachable** (skip — their outage)
from **reachable but unparseable** (fail — real drift), so a red run means
something actually changed.

- Keep the tool **stdlib-first** — a new runtime dependency needs a real
  justification. The three we have earn their place: `pyyaml` and `rapidfuzz`
  unconditionally, and `tzdata` on Windows only, because Windows ships no system
  time-zone database and `zoneinfo` has nothing to read without it.
- **Adding a job source?** Use the provider's documented public API — no scraping.
  Add a parser test with a captured sample response (see the `sources` tests).
- Match the existing style; `ruff` enforces formatting and imports.

## Run the gate in an isolated export, not the working tree

A working tree passes on files you never staged. The export proves the commit stands on
its own:

```bash
rm -rf /tmp/gate && git checkout-index -a -f --prefix=/tmp/gate/
cd /tmp/gate && ruff check . && mypy && python -m pytest -q
```

Every commit, not every PR.

**This gate has one known hole, and there is a one-line fix.** The export has no
`.git`, so the `department` byte-identity compatibility test — which reconstructs the
0.6.0 blob with `git show` — takes its own skip branch instead of running. That is the
repo's most important backward-compatibility check, and its own docstring names the
danger: *"a compatibility gate that quietly does not run reads as assurance, which is
worse than not having it."* Its guard only fires when `.git` **exists**, so deleting
`.git` makes the guard's condition False and the test skips silently. Copy the history
in, and always pass `-rs` so a skip prints its reason instead of a bare `s`:

```bash
rm -rf /tmp/gate && git checkout-index -a -f --prefix=/tmp/gate/
cp -R .git /tmp/gate/.git     # or the compatibility gate cannot run
cd /tmp/gate && ruff check . && mypy && python -m pytest -q -rs
```

Verified at the same commit, in the same minute:

```
export as-is        536 passed, 1 skipped, 36 deselected
export + .git       537 passed, 0 skipped, 36 deselected
```

**`git checkout-index` exports the INDEX, not HEAD — and always print the rev you
measured.** Staged-but-uncommitted content is what you get, which is usually right
before a commit (you are testing what you are about to commit) and wrong afterwards. To
gate a *landed* commit, name it: `git archive <rev> | tar -x -C /tmp/gate`.

The trap is not the index, though. It is that **HEAD moves under you.** A count of 564
was once read as an index-vs-HEAD gap here when it was nothing of the kind — HEAD had
advanced two commits mid-measurement, and the tidy explanation fit well enough that it
was nearly written into this file as fact. Capture the rev in the same breath as the
number (`git rev-parse --short HEAD`) and quote them together, or a number from one
tree gets compared against a number from another and the difference gets a story
attached.

**A skip is not a pass.** If the export shows more than that one skip, or the working
tree shows any, find out which test stopped running before you trust the green.


## Staging: `git add <path>` stages the WHOLE file

This is the single most expensive lesson in the repo's history. Naming an explicit path
does **not** protect you from including someone else's in-progress hunks in that same
file — or your own unrelated ones. On 2026-08-20 this swept a change into a commit whose
message read "Comment-only; no behaviour change."

- Use `git add -p`, or read `git diff --cached` **hunk by hunk** before committing.
- Never `git add -A` / `git add -a`.
- **Never `git commit --amend` when HEAD is not your own commit.** It rewrites someone
  else's history. Check `git log -1` first.

Two checks worth running on your own staged diff before you commit:

```bash
git diff --cached | grep -c '^+def test_'   # does the test count match what YOU wrote?
git diff --cached --stat                    # is every file here one you meant to touch?
```

## Mutation-test every guard you add

A passing test proves the test passes. Disarm the guard in an isolated export and
confirm a test **fails**:

```bash
rm -rf /tmp/mut && git checkout-index -a -f --prefix=/tmp/mut/
# delete the guard in /tmp/mut, then:
cd /tmp/mut && python -m pytest -q    # a green run here means the guard is untested
```

Six tests pinned `util.clean`'s entity-decode-before-strip order. **Four of them were
inert** and only mutation testing found it. Separately: three passing assertions on a
helper stayed green when the guard was deleted **from its call site** — testing a helper
is not testing the wiring.

## Nine ways a check comes back clean for the wrong reason

Every one of these produced a confident, wrong number in this repo:

| # | form | what it looked like |
| --- | --- | --- |
| 1 | wrong function signature | called a mutate-in-place function as if it returned a value — got `0`, nearly reported a working fix as a no-op |
| 2 | wrong function entirely | used a whole-string mapper where a token search was needed — got `0` |
| 3 | rows rebuilt outside the pipeline | produced 16,150 impossible rows |
| 4 | adapter output ≠ record output | skipped `engine._coerce`; saw `state='California'` where the record says `CA` |
| 5 | stale tree claim | "clean at X, 503 tests" when HEAD was Y at 514 |
| 6 | a name-grep as a symbol check | reported a rewritten function as "0 changed lines" |
| 7 | a threshold that passed for the wrong reason | 10-under-12 passed while 9 of the 10 were wrong |
| 8 | **a zero accepted because it came with an explanation** | an explained zero reads as *more* rigorous than a bare number. It is not. |
| 9 | **a correct explanation applied outside the scope it was derived in** | true of every row measured, false for a class that was not |

Forms 8 and 9 are the dangerous ones, because both look like good work.

## Measurements

**Tag every number with where it came from**, inline and next to the number:
`[local 94-board harvest, 0.9.0]`, `[live prod]`, `[fresh harvest]`. An untagged count
should be read as sloppy.

**Probe the live API before planning a fix.** Our own code and our own captured rows
cannot tell you what an API would do if asked correctly. Establish first whether it
validates unknown parameters: Adzuna 400s on junk, so its counts are trustworthy;
USAJOBS and The Muse silently ignore it, so a typo'd parameter is a permanent, silent
no-op.

**Cite symbols, never line numbers.** Line numbers go stale within the hour and get
inherited unchecked.

## Tests

Adapter tests monkeypatch the transport, not the network:

```python
monkeypatch.setattr(sources, "get_json", lambda url: fake)
monkeypatch.setattr(sources.time, "sleep", lambda *_: None)
```

Captured payloads live in `SAMPLES` in `tests/test_sources.py`.

**When the bug is in the URL that gets built** — a wrong parameter, a filter named but
never sent — assert on the **captured URL**, not on parsed output. Parsed output can look
right while the request was wrong.

Fixture tests freeze a vendor's shape as of the day they were written. Only
`pytest -m live` catches drift. That is the entire reason the canary exists.

## Docs move in the SAME commit as the code

README feature claims, `CHANGELOG.md` (`[Unreleased]`, Keep a Changelog), `_SCHEMA.md`,
the shipped example config in `job_radar/data/`, and the `catalog/` profile for a source
you changed. **A doc-only follow-up commit is a failure signal** — it means the earlier
commit was incomplete.

## Comment style

Comments explain **the bug they fixed, with the measurement that motivated it** — often
correcting what an earlier version of that same comment claimed. A wrong comment is
treated as a bug here. Match the house style.

## Reporting your results

**Lead with what you did NOT do, or could not verify.** A green gate proves conformance,
never substance. State accepted costs and regressions plainly in the commit message
rather than letting someone discover them later.

## Reporting bugs

Open an issue with the command you ran, what you expected, and what happened. For
security issues, see [SECURITY.md](SECURITY.md) instead.
