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

**Always pass `-rs`, so a skip prints its reason instead of a bare `s`.** A skip is
not a pass, and `pytest -q` gives you one character to tell them apart:

```bash
rm -rf /tmp/gate && git checkout-index -a -f --prefix=/tmp/gate/
cd /tmp/gate && ruff check . && mypy && python -m pytest -q -rs
```

This gate used to have a hole worth remembering even though it is now closed. The
export has no `.git`, and the `department` byte-identity test reconstructed the 0.6.0
blob with `git show` — so deleting `.git` made that test's own guard condition False
and it skipped **silently**, which its docstring called out as worse than not having
the check at all: *"a compatibility gate that quietly does not run reads as
assurance."* The fix was `cp -R .git /tmp/gate/.git`. 0.9.0 removed `department` and
that gate with it, and nothing in `tests/` or `job_radar/` shells out to git any
more, so the copy is no longer needed. **If you ever add a test that reads git
history, add the copy back** — and know that its failure mode is a silent skip, not a
red run.

**An UNMERGED path is silently omitted from the export, and the gate still reports
green.** This is the most dangerous of the three, because its output is
indistinguishable from success. Mid-conflict — or after resolving a conflict's content
but before `git add` marks it resolved — `git checkout-index -a -f` **skips the file,
writes nothing, and exits 0**. Measured on a deliberately conflicted `tests/test_core.py`:

```
exit code                0
file present in export   no, silently omitted, no warning
export gate reports      314 passed        <- against a real 564
```

250 tests vanished and the run looked clean. Nothing in `pytest -q` says "I collected
fewer files than last time."

**So before trusting any export count, read `git status --short` for `U` (unmerged) and
for unstaged `M`.** Resolving a conflict's text is not resolving the conflict — `git add`
is. An alternative that sidesteps all of this: `git archive <rev> | tar -x -C /tmp/gate`
refuses to invent a tree and always exports exactly one commit.

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

**Naming a path on `git add` does not narrow what `git commit` writes.** The index may
already hold someone else's staged work — or your own from another task — and `commit`
takes all of it. This happened here: `git add CONTRIBUTING.md && git commit` swept five
files belonging to a concurrent task into a docs commit. Nothing was lost (`git reset
--soft`, unstage theirs, recommit, restage theirs), but the message described one file
and the commit held six.

Two habits that prevent it:

```bash
git status --short          # BEFORE you stage: is anything already in the index?
git commit -o <paths>       # --only: commit exactly these paths, whatever else is staged
```

And read the staged diff **in its own command**, not chained onto the commit. Printing
`git diff --cached --stat && git commit` shows you the problem *after* the commit
exists, which is how this one got through despite the rule two paragraphs up.

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

## Thirteen ways a check comes back clean for the wrong reason

Every one of these produced a confident, wrong number in this repo:

| # | form | what it looked like |
| --- | --- | --- |
| 1 | wrong function signature | called a mutate-in-place function as if it returned a value — got `0`, nearly reported a working fix as a no-op |
| 2 | wrong function entirely | used a whole-string mapper where a token search was needed — got `0` |
| 3 | rows rebuilt outside the pipeline | produced 16,150 impossible rows |
| 4 | adapter output ≠ record output | skipped `engine._coerce`; saw `state='California'` where the record says `CA` |
| 5 | stale tree claim | "clean at X, 503 tests" when HEAD was Y at 514 |
| 6 | **a grep count read as a membership test** | `grep -c '"department",' engine.py` returned 2 and was reported as "present in `_CONTRACT_FIELDS`". The two hits were two *other* tuples; the field was never in that one. Also: a name-grep reported a rewritten function as "0 changed lines", and a search for `emit` matched the English word in prose |
| 13 | **a guard that reads as total and covers only its own anchor** | `assert old in src` passed against a *contaminated* tree, because the mutant had been built from the previous mutant's directory and the two anchors did not overlap. The check is necessary, not sufficient |
| 12 | **a mutation that survives because the environment agrees with it** | `.astimezone()` with no argument uses the SYSTEM zone. On a machine already in Eastern, `.astimezone(_ET)` and `.astimezone()` return the same answer — so both mutants passed a full 572-test run locally and failed under `TZ=UTC`. The surviving mutant was the exact line the commit message called "the guard is the fix" |
| 11 | **a true measurement read against the wrong baseline** | a field measured absent from a tuple, read as damage — it had never been in that tuple. The number was right; "absent means something broke" was the error |
| 7 | a threshold that passed for the wrong reason | 10-under-12 passed while 9 of the 10 were wrong |
| 8 | **a zero accepted because it came with an explanation** | an explained zero reads as *more* rigorous than a bare number. It is not. |
| 9 | **a correct explanation applied outside the scope it was derived in** | true of every row measured, false for a class that was not |
| 14 | **an approval invalidated by a later change to its shared input** | a rule was approved on a measurement against the NARROW cue list. Widening the list was a separate, correct change — and against the wider list the same rule cost 5 rows net and moved every stratum it touched the wrong way. Nobody re-opened the approval, because the edit was to a different object |

Forms 8, 9, 11 and 14 are the dangerous ones, because all four look like good work —
a real measurement is involved in each. (There is no form 10; the numbering is
append-only so an existing reference never changes meaning.)

**Two rules that fall out, narrow enough to actually follow:**

- **When you rule that two error classes are NOT equally bad, every metric from that
  point on must separate them.** A composite score silently re-equalises exactly what the
  ruling separated. The instance cost two rounds, three rulings and a suspension:
  `salary_kind` was ruled precision-over-recall — a missing label costs a user nothing, a
  wrong one is the defect the field exists to prevent — and then every configuration was
  argued on raw agreement, which weights the two identically. Split out, the two
  candidates were **21 wrong assertions and 21**, tied on the metric that mattered and
  differing only in refusals. The composite said `126 > 121` and pointed at a real
  difference that was **not the one under debate**. A ruling changes what you must
  measure, and nobody changes instruments when the question changes; the tell is a
  headline number that cannot express the distinction you just made.

- **When you change a shared input, re-read every approval that input supported.** An
  approval is attached to the configuration it was measured in, and nothing warns you when
  a later edit moves that configuration out from under it. The instance: three rules were
  approved one at a time against the narrow cue list; widening the list made the first of
  them a net negative, and it was two rounds from being built before anyone re-measured it.
  **The tell is that the invalidating edit is to a DIFFERENT object** — a cue table, a
  threshold, a vocabulary — so nobody thinks to re-open the decision it silently governs.
  This is form 11's sibling: there, a true number was read against the wrong tree; here, a
  true approval is carried into the wrong configuration.

- **A grep count is never a membership test.** For "is X in collection Y", the only
  valid checks are runtime membership (`X in module.Y`) or reading the literal. Grep
  answers "does this string appear in this file" — a different question that happens to
  return a number, and the number is what makes it feel like an answer.
- **A measurement is evidence only about the tree it was taken on. Name that tree, then
  check what changed between it and the code you are arguing about.** This one has
  recurred three times in a single release, in three costumes: a field measured empty on
  a corpus whose *source mix* excluded the only adapter that fills it; a guard
  mutation-tested on a machine whose *timezone* happened to match it; and a leak measured
  on the *released version* while two commits earlier in the same unreleased branch had
  already closed it. Every one was a real number, correctly computed, describing
  something other than the thing under discussion.

  The mechanical form, and it is two commands:

  ```bash
  git log --oneline <tree-the-measurement-came-from>..HEAD -- <the-files-it-touches>
  git merge-base --is-ancestor <the-fix-you-think-is-missing> HEAD && echo "already in"
  ```

  If anything comes back, the measurement predates the code and cannot justify a change
  to it. Production is a *tree*: a number from a live store is evidence about the version
  that store runs, not about `HEAD`.

- **Before reading a number as damage, ask what it would look like if nothing were
  wrong — then measure *that*.** Compare against the prior commit (`git show <rev>^:path`),
  never against your expectation. This is the general form of the wrong-denominator
  trap one level up: there the population was wrong, here the comparison point is.

**Mutation testing has its own version of this.** A mutant that survives is only
evidence when the environment cannot be quietly supplying the right answer. Run
zone-, locale- and clock-sensitive mutations under a pinned environment — CI sets
`TZ: UTC` for exactly this reason. And check *which* mutant went red: disarming a
neighbouring line and watching a test fail proves something failed, not that the
guard is tested.

**Before committing a removal, `git grep` the whole repository for the removed name and
classify every hit** — shipped package, `catalog/`, CI config, tests, docs. `catalog/` is
committed and CI-gated, and it is documentation the same way a docstring is. Scoping the
sweep to `job_radar/` produced three doc-only follow-up commits in one phase, from one
mistake made twice.

**And grep the VOCABULARY that pointed at the removed field, not only its name.** A
removal orphans the words that referred to it, and those words are a different search. The
name-sweep above is necessary and it would not have found this: `catalog/`'s `employer_org`
slot contains no instance of the string `department`, yet removing `department` left
USAJOBS' `DepartmentName` with nowhere to go. Swept across 22 profiles, eight map something
into `employer_org` and seven of those are the company name, which still has a home in
`company` — **one of eight was made homeless by the removal, and only a vocabulary sweep
surfaces which.**

**A passing gate is not evidence about anything the gate does not read.** Both catalog
gates stayed green across a profile that was wrong in five places, one of them inside
machine-readable frontmatter — `_scaffold.py --check` validates frontmatter *keys* and
`_crosscheck.py` compares `INDEX.md` against the profiles. **Nothing checks whether a
profile is true.** Demonstrated by mutation: revert a `note` to a false claim plus
obvious garbage and both gates still exit 0.

**Three mechanical traps, each of which produced a wrong answer here:**

- **`git checkout-index` gates the INDEX**, which is right immediately before a commit and
  wrong for a landed one. `git archive <rev>` is the only form that cannot lie about which
  tree you measured. Pin the artifact, not the pointer — a correctly pinned HEAD still
  reads the working tree.
- **Build a mutation tree from an absolute path to the repo, and assert the *other* sites
  unmutated before touching anything.** See form 13.
- **The auto-format hook reflows the whole README on any `Edit`** — a 12-line change
  produced 100 lines of churn. Apply doc edits through Bash, which the hook does not
  intercept.

**What actually catches these:** in every instance above that was caught in time, the
catch came from **executing something** — running the disarm, checking runtime
membership, diffing against the parent commit. Not one was caught by re-reading the
output. Staring harder at a grep result has never once worked here.

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
