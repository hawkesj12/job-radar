# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres
to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **`salary_kind` — what the figure measures, which nothing in the record recorded.**
  `salary_basis` says only HOW a figure was extracted and never WHAT quantity it is.
  That gap is why on-target-earnings bands and a **\$32,000–\$48,000 new-hire equity
  grant** sat in columns the README defines as what an employer committed to as base pay.
  Closed vocabulary in `vocab.SALARY_KINDS`: **`base` · `ote` · `equity` ·
  `unspecified`**, and `None` when there is no figure at all.

  **No new parsing** — it reads the ±90-character window `_adjacent_evidence` already
  uses for currency and period, because the label sits with the number. **Two rules
  decide among competing cues:** the nearest wins, with a longer *containing* match
  preferred as one phrase read at two precisions; and a quantity being **excluded**
  (`base salary (excluding equity and bonus)`) is discounted, because the excluded word
  is nearest by construction. Only the sentence the figure lives in is read — see the
  separate entry for why that rule was removed and restored.

  **`bonus` AND `total_comp` WERE REMOVED, on measured precision rather than judgement.**
  Rows the detector called each were drawn from the shipped classifier and labelled
  blind: **`bonus` 0 of 25 and 0 of 25** (two readers — one anchored, and reported as
  such), **`total_comp` 3 of 22 and 5 of 25.** Both far under the 40% line fixed in
  writing before the draw.

  **The cause is a class, not a threshold: `+ bonus` after a figure means the bonus is
  NOT IN IT.** The cue is anti-correlated with the label, and proximity makes it worse —
  the nearer the word, the more certain the exclusion. No threshold repairs a signal
  pointing the wrong way. **Removal is mostly not a refusal:** of the 1,815 rows,
  **1,171 land on `base`** — the right answer on 22 of 25 labelled rows — 600 on
  `unspecified` and 41 on `ote`.

  **`equity` was SCOPE-RESTRICTED, not removed, and the order mattered.** Unrestricted it
  fires on 81 rows and is right on 15; an equity mention on the next line of a benefits
  list (`"…base salary range is \$132,000–\$178,000, plus RSUs"`) reads as the figure
  BEING equity.

  **Two things count as a boundary, and the second is the one that finished it.** Clause
  breaks alone retain 29 and are right on 15 — 52%. **Adding an additive connector
  (`+`, `plus`, `and`) as a boundary retains 15 and is right on 15 — 100% on this
  corpus.** A label *precedes* its value (`"New hire equity: \$32,000–\$48,000"`); a term
  joined by `+` names a **separate item** — the identical construction that made `bonus`
  right 0 times in 50. Third member, third appearance, one shape.

  **Scoped to `equity` alone, and the asymmetry is semantic.** On-target earnings *are*
  base plus commission, so `"\$231,000–\$275,000+ OTE, Base + Commissions"` uses `+` to
  describe **the figure's own parts** — the same rule applied to `ote` destroys a true
  positive. Verified: `ote` unchanged at 809 rows. **The restriction had to land BEFORE the
  removal:** with `bonus` gone and `equity` unrestricted, **188 of its rows would have
  moved from one wrong label to another**. In the shipped order, 3 do.

  **A COLON IS NOT A CLAUSE BREAK, and that nearly cost the field its reason to exist.**
  Every true positive is `"New hire equity: \$32,000–\$48,000"` — colon included — so a
  boundary set of `.!?;:` keeps 11 rows and **zero** true positives. `.!?;` plus a
  newline keeps 26 and all 15. A colon *introduces* the figure it labels.

  **And the 100% figure was a human split, not a mechanical one.** A blind labelling put
  15 of the 81 in the figure's own clause and scored those at 100%; no punctuation rule
  reproduces it — the four tried give 11, 26, 26 and 61. **The code approximates the
  judgement and is published at its own rate**, never the labelling's.

  **All 15 true positives are one employer's template**, so the restriction is validated
  on one board's phrasing. Shipped anyway on an asymmetry: **too narrow costs recall,
  which is free; too loose costs precision, which is the defect this field exists to
  prevent.** A grant written differently is missed and emits `unspecified` — a refusal.

  **Measured rates** `[102,799-row harvest, 2026-08-20; 42,072 rows with a salary
  display]`: `base` 54.7% · `unspecified` 43.3% · `ote` 1.9% · `equity` 0.1%. **`base`
  may contain on-target-earnings rows** where the governing phrase sits in a prior
  sentence or an unread parenthetical — nine of fourteen wrong assertions on a
  hand-labelled sample. **Overall wrong-assertion rate 16 of 150 = 10.7%**, adjudicated
  across two independent readers.

  **A widened `ote` cue was measured and dropped.** Allowing a modifier between `+` and
  the noun (`"base salary + Uncapped commission"`) would have gained 9 rows and **broken
  5 that are currently right — including `"(Base + On-Target Commission)"`, the exact
  shape it was meant to strengthen.** Recorded rather than shipped.

  **It moves no number.** A SHA256 over all six salary fields across 102,799 rows is
  `d5799a52…` before and after, unchanged across every commit in this sequence.

- **`sections.section_text(record, kind)` — the reader for the spans the record already
  carries.** Everything that parses a posting's prose for one fact (pay, requirements,
  travel) should read the section that fact lives in rather than the whole body; this is
  that surface. Returns **three states**, and the middle one is the point: `None` when
  there is no section of that kind **or** its span could not be located, `""` when the
  section is present and **genuinely empty**, and the text otherwise.

  **`""` is not a near-miss for `None`.** `clean_with_sections` gives a header with
  nothing under it a zero-length span deliberately — "an empty section, not a failed
  lookup" — and that is **93,054 of 981,857 located spans** `[102,799-row harvest,
  2026-08-20]`. Collapsing them into `None` would destroy the same two-state distinction
  `sections: []` keeps against `sections: null`.

  **The collapse it DOES make is stated in the docstring with its count:** "no such
  section" and "the section exists but could not be located" both return `None` —
  **4,667 spans across 3,098 rows** — because a caller wanting to read that section
  behaves identically either way. A caller needing to tell them apart reads
  `record["sections"]` directly.

  **Bounds-checked, not just key-checked, and the silent failure is the reason.** The
  obvious guard is the missing `start` key, which raises. The dangerous one does not:
  `text[start:end]` with `end` past the end of `text` **returns a short slice, or `""`
  when `start` is also past** — indistinguishable from an empty section. That is **0
  spans against a body this engine produced and 36,270 against a downstream copy that
  truncated `text` without the spans that index it**, so it arrives from a consumer's
  data. A span that does not fit its text is a disagreement, and `None` is the honest
  answer to a disagreement.

  **KNOWN CEILING, inherited by every consumer:** `type` is `None` on **492,909 of
  986,524 spans — almost exactly half.** Those are real headers the classifier could not
  name, so a `kind` lookup reaches at most half the structure a posting has, and a `None`
  often means "we found a header and could not classify it" rather than "the employer
  wrote none". Published per-kind coverage (`requirements` 83.8%, `compensation` 22.5%)
  is a share of ROWS, not a claim about the other half of the spans.

### Fixed

- **A job SEEKER's post was ingested as a job, republishing a private individual's
  contact details.** One row carried a personal email address **and** a GitHub profile
  in `title`, with their location line in `company` and the same details again in the
  1,126 characters of `text`. `search_hn_whoishiring` already filters *threads* to
  "who is hiring", so this is a seeker using the seeker template **inside a hiring
  thread** — no thread-level filter can reach it, and it needs a per-comment shape
  test. `_hn_rows` now drops a comment carrying **three or more** of the seven seeker
  labels (`Willing to relocate:`, `Résumé/CV:`, `Location:`, `Remote:`, `Email:`,
  `Technologies:`, `Seeking:`).

  **Three, not one, and the threshold is measured.** Scored over every hn row in a
  102,799-row harvest — 219 comments, of which 1 is a seeker: the three-label rule and
  `Willing to relocate:` alone each fire on exactly that row, while
  `résumé/cv OR relocate` fires **twice** — the second a genuine listing that said
  "Resume:" because it was *asking* for one. So no single label may decide, including
  the most seeker-specific.

  **What the zero does not mean.** 0 of 218 genuine hiring posts is an upper bound of
  roughly **1.4% at 95% confidence**, not proof of zero — though no hiring post in that
  corpus reached even two labels, so the margin is real. **Recall rests on n=1:** the
  corpus contains exactly one seeker row, so the rule is measured against a single
  example of what it exists to catch, and a seeker using a different template is not
  covered. The whole comment is dropped rather than the fields scrubbed, because the
  details appear in `text` as well.

- **A Lever posting with no body at all claimed both "no body" and "a body with no
  headers".** `sections: []` means "we read a body and it had no headers" — a claim
  *about* a body. With every Lever field empty there is no body for it to be about:
  `clean_with_sections("")` returns `("", [])`, `""` normalizes to `None` at the engine
  boundary, and the row then asserted both states at once. **21 rows**
  `[102,799-row harvest, 2026-08-20]`, all Lever, because it is the only adapter that
  *builds* a body out of parts that can all be absent. Fixed in the adapter, not in
  `clean_with_sections`, which must keep returning `[]` for the **1,137** rows that
  genuinely carry a body with no headers — from inside that function the two cases are
  indistinguishable, and only the caller knows whether a body existed.

- **A section header was built by a second, disagreeing copy of the text pipeline.**
  `sections._detag` replaced every tag with a space — including an inline tag sitting
  **inside a word** — while `util._clean_decoded` deletes an inline run followed by
  lower case. Two pipelines over the same bytes, and `clean_with_sections` locates a
  section by searching for its header **in that text**, so the disagreement broke the
  lookup: the record published `"About t he Role"` beside a body reading
  `"About the Role"`, and `"Health can't wait ."` beside `"Health can't wait."`
  Measured `[20 live boards, 6,267 postings, 67,678 sections, 2026-08-21]`: **3,962
  headers were absent from their own text; 3,882 are repaired and 0 are lost.**

  **The spans were the real cost.** A header that cannot be found leaves the search
  position un-advanced, so the next section is searched for from too far back —
  **2,372 spans move**, 2,248 of them zero-length, i.e. empty sections anchored to the
  wrong place entirely. The guard that steps past a header was never broken; it was
  being handed a key that did not fit the lock, and its fixture uses a findable header
  so it could not reach this. Classification barely moves, which is the expected shape
  rather than a weak result — `classify` lowercases and strips before matching, so
  exactly **1 of 67,678** changes (`"A bout the Team:"` → `about_company`).

  **Named residual: 80 headers are still unfindable, and 50 of those are deliberate.**
  `_clean_decoded` turns a block end into a newline; adopting that here would find
  those 50 and put a literal newline inside a published `header` string in the NDJSON
  and the CSV. A header is one line by construction. The other 30 are a separate
  defect in `_header_at`'s tail absorption, not fixed here and not folded into the
  figures above.

- **`title` and `location` shipped with the vendor's edge whitespace on them.**
  `engine._coerce` forced every text field to `str` and never stripped one, so
  **9,874 titles (9.6%) and 1,894 locations** carried leading or trailing whitespace
  `[102,799-row harvest, 2026-08-20]` — and not only ordinary spaces: 9,791 space, but
  **57 non-breaking spaces, 21 U+202F, 14 tabs and 2 newlines**, none of which a person
  eyeballing the value would see.

  **The asymmetry is the actual defect.** `shortlist._build_row` has stripped exactly
  these two fields for releases, so `shortlist.csv` was clean the whole time while the
  record and the NDJSON shipped the raw value — a library consumer got dirt the CLI
  user never saw. Fixing only `title` would have left half of that in place with no
  reason recorded for the half left behind.

  **No user-facing job id moves.** `dedup_key` is the store's primary key and
  `id = short_id(dedup_key)` is what a user holds, so a moved key renames a job.
  `normalize_title` and `normalize_location` both collapse every non-alphanumeric run
  before keying, which already absorbed all five whitespace classes — **verified over
  all 9,874 rows, 0 keys change**, and pinned by a test rather than left as a claim.

  Stripping runs **before** the nullable pass, so a whitespace-only `location` becomes
  `None` instead of a string that looks present and holds nothing (0 such rows in that
  corpus; the ordering is free). `company` (24 rows), `url` and `source` (0 each) are
  deliberately untouched — on a depth adapter `company` comes from the watchlist rather
  than the vendor, so this boundary is the wrong layer for it.

  **There was no test for this either.** `test_coerce_strips_edge_whitespace_without_moving_the_dedup_key`
  is the first coverage and was mutation-tested three ways — removing the strip,
  narrowing it to `title` only, and widening it to `company`/`url` — each red on its
  own, every one run from a tree verified green first.

- **`title_root` emitted a string no employer wrote, on every title with an accented
  letter.** `vocab._WORD_RE` was `[A-Za-z0-9+#/&]+` and `title_root` is REBUILT by
  joining the tokens it finds — so a character outside the class is not ignored, it is
  **deleted, and the word splits at the hole**. `Sênior Security Engineer` came out
  `S nior Security Engineer`. This was the only field in the record manufacturing text
  rather than dropping it. **155 rows / 115 distinct titles, 101 getting a corrected
  root** `[102,799-row harvest, 2026-08-20]`.

  **Latin only, and the obvious fix was the wrong one.** `[\w+#/&]+` is Unicode-wide
  and also un-drops CJK, Hangul, Katakana and Cyrillic — which sounds strictly better
  and is not, because on a bilingual title the ASCII-only class had been accidentally
  acting as an English extractor and doing it well. Over all 450 distinct non-ASCII
  titles: Latin-only changes 115, full `\w` changes 200, and **all 85 extra are
  regressions** — `Cloud Infrastructure Engineer / クラウドインフラエンジニア` roots at
  `Cloud Infrastructure Engineer` today and would have rooted at the whole bilingual
  string. **The cost of scoping it this way is real:** 8 titles in that corpus have a
  root that is the entire unparsed title, and full `\w` repairs 6 of them where this
  repairs 0. Six forgone against eighty-five avoided.

  `×` (U+00D7) and `÷` (U+00F7) are excluded by the range breaks — they sit inside the
  Latin-1 letter block and are operators. `+#/&` still hold `C++`, `C#`, `and/or` and
  `R&D` together, and `_` is still a separator. U+0300–036F is included for **NFD**
  input, where `Sênior` is `S` + `e` + a combining circumflex and the class would
  otherwise split at the mark: zero rows in this corpus are NFD, and it changes nothing
  measurable here (115 either way, byte-identical output on all 450) — it is in because
  a corpus reaching 11 of 19 sources cannot prove no vendor sends it.

  **This does NOT fix `seniority`, and the earlier framing of this bug said it did.**
  `vocab._SENIORITY` is keyed on ASCII, so `sênior` is still not a member and those
  rows keep `seniority: None`. **Unchanged on every population, and the population is
  named because the three counts look contradictory otherwise:** 7 → 7 across the 115
  distinct titles this change touches, 61 → 61 across the 506 rows carrying a non-ASCII
  *letter*, and 772 → 772 across the 2,628 rows carrying any non-ASCII *character*.
  Accent-folding the lookup is a separate change with its own blast radius.

  **There was no test for any of this.** The full suite was green on both sides of the
  bug, so a fix could have arrived silently.
  `test_title_root_never_manufactures_a_word_no_employer_wrote` is the first coverage,
  and it was mutation-tested three ways — reverting to ASCII, dropping only the
  combining range, and widening to full `\w` — each of which turns it red on its own.
  (A substring check for fabricated root words reads 0 both before and after, so it
  measures that the fix introduces no NEW fabrication; it does not detect the original
  bug. The 101 corrected roots do.)

- **`posted` was one day too new on every source that sends a UTC timestamp** (and
  `expires` with it, though only braintrust's ten rows actually move).
  `util.to_date`'s epoch branch converted to Eastern; the string branch three lines
  below truncated to `str(val)[:10]`, keeping the **vendor's** calendar day. One column,
  two conventions, against a project rule of Eastern Time everywhere. Measured against
  live vendor APIs joined to a 102,799-row harvest: **ashby 16.3% of dates wrong**
  (26,218 rows, 25.5% of that corpus), **hn 10.5%**, greenhouse **0.00%**, lever
  **0.00%** — the correct two already sending an ET offset or an epoch. Estimated
  3,100–4,300 rows, always one day too **new**, so the role also dodged the staleness
  penalty; four rows were dated tomorrow. A date-only or zone-less string is *not* an
  instant and deliberately does not shift. Catches `OverflowError` as well as
  `ValueError` — `.astimezone()` raises it on `0001-01-01T00:00:00Z`, the .NET/Go zero
  value, which is in this repo's own `catalog/_raw/`. On Python 3.10 the narrower
  `fromisoformat` falls back to truncation for .NET 7-digit fractions and colon-less
  offsets — today's behaviour, never a new wrong answer.

- **An Ashby hybrid role no longer renders as `"(Remote)"`.** The location string
  appended the suffix from `isRemote`, which is **true on every hybrid row** — the
  measurement is recorded at the `remote_type` assignment, which was rewired to
  `workplaceType` in 0.9.0 while the display string was not. **7,435 rows** read
  `location: 'San Francisco (Remote)'` beside `remote_type: 'hybrid'`.
  **Not display-only:** `remote_scope_raw` is a byte-copy of `location`, and **775 rows**
  correctly go `remote_areas: ['US'] → None` because a hybrid role in Menlo Park never
  stated a remote boundary; **6,225 rows** drop 1–5 score points because
  `score_and_signals` scans `location` and "remote" is a scored keyword. **`dedup_key`
  does not move** — `normalize_location` already discards the word, verified identical
  on all 7,435. Anyone diffing two harvests will see these as regressions; they are the
  correction.

- **A named person's work email and staff number no longer reach `source_extra`.**
  `_gh_metadata` copied every Greenhouse `metadata[]` entry with no filter on the value.
  **1,280 rows** of a 102,799-row harvest carried `{name, email, user_id, employee_id}`
  objects under 17 key names — Hiring Manager 365, Recruiter 280, Job Approver 156 — at
  veeam.com, datavant.com, nice.com, celonis.com, x.ai and hasbro.com. The boards publish
  it, so nothing was breached; republishing it under a consumer's name was a default
  nobody chose. Two encodings are filtered: the person object, and **57 rows** carrying
  the address as a plain string (59 values; 25 of them embedded in longer text, which
  is why the test is a search rather than a whole-value match). Clustering every dict value in that harvest gives four
  shapes — salary, referral, a bare pay range, and the person object — so the person-key
  test drops **zero** legitimate values. **Scoped deliberately:** this removes emails and
  staff numbers; bare personal *names* still pass through, because filtering those needs
  a key list that would also drop legitimate values under the same keys.

### Added

- **Two output-shape levers: `output.include_text` and `output.omit_empty`** (CLI:
  `--no-text`, `--drop-empty`). The record was unreadable by hand and there was no way
  to ask for less of it. Measured per median record on a 7,568-row local harvest
  `[local 94-board harvest, 0.9.0]`:

  | | keys | bytes |
  | --- | --- | --- |
  | as emitted | 50 | 8,717 |
  | `omit_empty` | 31 | 8,313 |
  | both | 30 | 2,021 |

  The body is **~72% of a record's bytes** and **19 of its 50 keys are null** on a
  median row. Both levers **remove keys** rather than nulling them, because `null`
  already means something specific here — the source did not say — and collapsing a
  caller's display choice into that is the two-states-in-one-value lie the contract
  exists to remove.

  **They are `Config` fields, not CLI flags, and that placement is the feature.**
  `harvest` installs the caller's config process-wide, so `engine.harvest(cfg)` returns
  records already in that shape and a **library** consumer never touches argparse. The
  first cut of this put the text lever on `emit.records` alone — and `emit` is the one
  module the only known consumer imports nowhere, which would have made the largest
  lever on the record reachable from the CLI and from nothing else.

  **`omit_empty` defaults OFF** and is the one to think about before enabling: removing
  a key changes `"x" in record` and `record.keys()`, so a consumer written against the
  full key set can break on it — one record type becomes many shapes (1 distinct key-set
  across a 102,799-row corpus as emitted, **1,883** under `omit_empty`), and `_shape`
  runs inside `engine.harvest` rather than in `emit`, so this is a **library** contract a
  caller receives and not an output format. It is *not* about SQL: measured both ways,
  DuckDB reads a missing key as NULL, so `count(remote_type)` is identical whether the
  key was omitted or explicitly null. An earlier draft of this entry claimed otherwise. **`[]` and `{}` always
  survive** — `remote_areas: []` means the posting *stated* it is open anywhere and
  `sections: []` means the body carried no headers. Those are facts, not absences.

### Changed

- **A record's keys are now ordered for a person to read.** Key order previously
  carried no decision at all — it was whatever each adapter's dict literal listed, then
  whatever `_coerce` and `_consume` appended. Measured on the harvest output
  `[local 94-board harvest, 0.9.0]`: `company` arrived 21 fields in, **below** the
  6,870-character `text` body and its `sections` list; `title_root` sat 15 fields from
  the `title` it decomposes; `city`/`state`/`country` came after the `locations` list
  they summarize; the salary group was split in two.

  The order is now role → employer → place → time → money → terms → apply → provenance
  → **body last**, applied once at the end of `harvest`. **Ordering removes nothing** —
  50 keys before and after — so it is half the answer: it makes the record scannable,
  and `omit_empty` above is what removes the wall. Neither does the job alone. **No value, type, or key
  changes** — JSON object order is not semantically meaningful and no consumer can
  break on it. A key the order does not name is kept, sorted, at the end rather than
  dropped, so adding a contract field can never silently delete it from every record.

### Fixed

- **The cross-source merge record no longer disappears at the wire.** `sources` — the
  set of adapters that saw one role, and the only place in the record that a dedup
  merge is written down — reached the two NDJSON exits through two different readers,
  and they disagreed. `emit._nested` accepted a `set` and nothing else, so the same
  value after **any** JSON round trip is a `list` and emitted `null`. `emit.manifest`
  accepted set and list, then fell back to `[r["source"]]` — and a store row folds its
  merge into the singular `source` as `", ".join(sorted(tokens))`, because
  `shortlist.COLUMNS` has no `sources` column at all. So `--format ndjson --all`
  counted a row under a **fabricated source named `"adzuna, greenhouse"`**, a string
  no adapter has ever been called, in the one output a consumer reads to tell "the
  market was quiet" from "four adapters were down".

  **A third exit had the same defect and was missed the first time:** `cli`'s
  attribution credit line built its token set inline the same way, so a merged row
  contributed a fabricated name that `attribution.credit_line` drops in silence — 12
  of them in that harvest. No source was actually under-credited, and by luck rather
  than design: every source in a merged row also appeared in a single-source row. A
  low-volume source whose rows all merged would have gone uncredited with no error,
  and attribution is a **condition of API access** on five of the wired sources.

  **The root cause is upstream of all three and is now filed as a known limit** in
  `shortlist._build_row` and the README: `shortlist.COLUMNS` has `source` and no
  `sources`, so the store flattens a set into a comma-joined singular column and
  every reader needs a decoder. A fourth reader of that CSV — a spreadsheet, a
  `pandas.read_csv` — has no decoder at all. Adding the column would end the need
  for any decoder, but it changes the CSV header, so it is filed rather than made.

  All three exits now read one helper, `emit._sources`, which takes `sources` as a set,
  list or tuple, and otherwise falls back to splitting the singular `source` — where a
  store row's joined string always lives, since there is no `sources` column to hold it. Recovering the set from that string is lossless and
  provably so rather than by inspection: **no registered source token contains a comma
  or a space (19 of 19)**, so `", ".join` has exactly one inverse. The singular
  `source` on such a row now emits a real adapter token instead of the joined string —
  it resolved against no `attribution` entry and matched nothing a consumer could key
  on. What the store never recorded is **which** source won the merge, so that key is
  a **representative** — the first of the sorted tokens, chosen because it is
  deterministic — not a winner. On a merged row both adapters really did produce it,
  so one real token is lossy-but-true; `sources` beside it carries the whole set.

  **35 rows in a 7,568-row local harvest** `[local 94-board harvest, 0.9.0]` carry more
  than one source. Dedup is where a mistake deletes a job, so these are exactly the
  rows that must not lose their provenance. Single-source rows are unaffected.

- **An Adzuna model prediction no longer renders as a posted salary.** `_adzuna_pay`
  routes a `salary_is_predicted` row into `salary_estimated_min`/`_max` and leaves the
  commitment columns null, exactly as designed — but `salary`, the human-readable
  display string and the only pay field most interfaces show, was built one line
  earlier at the call site from the same raw figures and never entered that split. So
  a row whose numbers were correctly quarantined still displayed `$109,106`,
  indistinguishable from a figure an employer committed to.

  **221 of 277 adzuna rows (79.8%)** in a 7,568-row local harvest
  `[local 94-board harvest, 0.9.0]`. In the downstream consumer's production store,
  **7,150 of 7,150 adzuna rows carried a salary string while only 369 carried a
  `salary_min`** `[live prod, engine 0.8.2]` — roughly 6,781 rows showing an estimate
  as an offer. Verified against the live API after the fix: 253 predicted rows, **0**
  with a display string; 45 stated rows, **all 45** keeping theirs.

  `util.salary_range` had already stopped rendering the fake range
  `$109,106–$109,106`, and its comment names this exact failure — but removing the
  _range_ appearance is not the same as removing the _commitment_ appearance, and the
  bare figure kept it. `SALARY_BASES` deliberately has no `estimated` member so that a
  figure in `salary_min` is always one an employer committed to; `salary` is the
  display of those columns, so a row with no commitment now has no string. The figures
  are not lost — a consumer wanting to show an estimate reads `salary_estimated_*` and
  labels it deliberately. The construction moved **inside** `_adzuna_pay` so the
  predicted-vs-stated split is decided in one place rather than two.

### Removed — BREAKING

- **`department` is gone, one minor version earlier than this project published.** The
  README said "removed at 1.0 — not before, as published"; it goes at **0.9.0**, and
  that broken promise is the cost of the change rather than a detail. The record is
  **45 fields → 44**.

  **It was a duplicate of `team` almost everywhere.** Of 100,878 filled rows in a
  102,799-row harvest `[2026-08-20]`, **100,825 were byte-identical to `team` and 53 to
  `category`; zero carried a value absent from both.** That corpus only reached 11 of
  19 sources, so it is not on its own sufficient — the claim was re-established by
  reading **all nineteen adapters**, which is the only method that covers a source that
  did not run. Eleven assign a value, and every one of those eleven writes `team` or
  `category` **from the same expression in the same record literal**, differing only in
  whether absent is spelled `""` or `None`; the other eight assigned `""`. Enumerating
  every JSON type a vendor can send through `x.get(k, "")` versus `x.get(k) or None`
  finds no input where they carry different information.

  **THE ONE GENUINE LOSS IS USAJOBS, and there is no recovery for it.** That adapter
  assigned `DepartmentName` — the EMPLOYING DEPARTMENT, "Department of Veterans
  Affairs" — to `department`, while `team` holds `SubAgency` (a facility name such as
  *"Central Virginia VA Health Care System, Richmond, Virginia"*) and `category` holds
  the OPM occupational series. On a live store, 77 of 82 federal rows carry that
  facility `team` and 0 carry a `category`, so `team or category` returns a **wrong
  value rather than a null**. `parent_company`, the recovery this README used to name,
  was removed earlier in this same release. The employing department is now simply
  unmapped on that adapter, and `test_usajobs_parser_maps_nested_federal_shape` asserts
  the string appears nowhere in the record rather than leaving it to be discovered.

  **`shortlist.csv` keeps the information under a new name.** That file carried
  `department` and, deliberately, neither `team` nor `category` — so unlike the record,
  there it was not a duplicate but the **only** org-unit column, 98.1% filled. Deleting
  it would have dropped the employer's own group out of the CLI's primary output
  silently, in place, on the user's file, on the first scan after upgrade. The column
  is **renamed to `team`** instead: same position, 18 columns still, 98.1% fill. **So
  the `team or category` recovery describes the record and the NDJSON, not the CSV.** A
  script that greps the CSV header for `department` still breaks — it loses a name, not
  the data.

  **What a user sees the morning after, because a rename is not free.** On the first
  scan after upgrading, the header swaps and every row that gets **re-harvested** picks
  up its `team`. A **sticky** row does not: an `applied` or `dismissed` role that was
  not seen this run keeps its stored values, and its old `department` is not carried
  into the new column — so it comes back with **`team` blank while `status` and
  `first_seen` are intact**. Reproduced on a 0.8.x store: `status='applied'`,
  `first_seen='2026-05-01'`, `team=''`. It self-heals the next time that posting is
  harvested — but a role that has **left the market**, which is precisely what sticky
  status exists to preserve, will never be re-harvested and stays blank permanently.
  Unavoidable for any renamed column, and stated here rather than discovered in an
  applied list.

  **What the test suite loses, stated rather than left to be found.** The
  `department` byte-identity gate loaded 0.6.0's `sources.py` out of git and ran it
  against today's fixtures; with the field gone there is nothing to compare, so the
  suite no longer has an **equivalence-against-history harness**. That is intrinsic to
  the removal, not an oversight. Its anti-vacuity coverage survives in
  `test_every_adapter_honours_the_posting_contract`. Two consequences ride along:
  `ci.yml` no longer needs `fetch-depth: 0` (nothing in `tests/` or `job_radar/` shells
  out to git — checked, not assumed), and `CONTRIBUTING.md`'s `cp -R .git` step is
  retired, with the reason it existed kept because its failure mode was a **silent
  skip**.

  **A new guard replaces the old one**, because `engine._reorder` deliberately KEEPS a
  key it does not name: removing `department` from `_NULLABLE_TEXT` and `_READING_ORDER`
  does not stop an adapter putting it back — and since it is no longer in
  `_NULLABLE_TEXT`, a re-added `"department": ""` would arrive as a literal empty
  string, the exact absent-means-empty lie the contract exists to remove.
  `test_no_adapter_still_emits_department` is parametrized over all nineteen adapters
  and was mutation-tested by re-adding the line to SmartRecruiters.

- **`industry`, `parent_company`, `salary_estimated_min` and `salary_estimated_max` are
  gone.** The record is **49 fields → 45**. All four were empty on 102,799 of 102,799
  rows of a harvest across 7,360 boards — but **that number is only evidence for two of
  them**, and saying otherwise would be a population error a reader can falsify:

  - `industry` and `parent_company`: genuinely empty, and the corpus is fair evidence.
  - **`salary_estimated_min`/`_max` were empty because that harvest had no Adzuna keys**
    — the one adapter that fills them could not run. In the live consumer's store they
    hold **6,633 of 67,481 rows**, 92.8% of its Adzuna rows and 73% the size of the
    entire commitment column. They are **discarded deliberately**, not swept up as dead
    weight.

  - **`industry` was fillable only by hand-annotating a watchlist**, never from a
    posting — `engine` copied it off the caller's company entry. The shipped example
    watchlist did exactly that on ten companies and its `_comment` advertised the flag,
    so anyone who ran `init` and used that file **has a populated `industry` column**.
    Those ten annotations are removed with the field, and **a pre-0.9.0 `shortlist.csv`
    loses its `industry` values on the next scan** — silently, atomically. The merge
    itself is safe (verified: sticky rows keep `status`, `first_seen` and `llm_score`,
    ids unmoved) but that column's data is gone. It was
    also the record's only field using `""` rather than `null` for absent (it sat in
    `_REQUIRED_TEXT`, so it was str-coerced), which quietly broke the contract's own
    rule that `None` means "the source did not say" on every row. And it appeared
    nowhere in this README's field table. `funnel` and `seed` no longer stamp
    `"(discovered)"` / `"(seeded)"` into watchlist entries, and `shortlist.csv` loses a
    column.
  - **`parent_company` and the estimate pair were fillable, by adapters that are simply
    rare** — teamtailor and usajobs for the first, adzuna for the second. Cut on the
    judgement that two live columns beat four columns where two are theoretical.

  **The prediction protection survives the estimate columns.** `_adzuna_pay` used to
  route a `salary_is_predicted` row into `salary_estimated_*`, and `derive_salary`
  returned early when it saw one — that guard existed because the parser once wrote
  `109106.0` into `salary_min` on a row whose `109106.69` was a model output. A
  predicted row now emits **no salary at all**: `salary` stays `""`, so `derive_salary`
  returns at its display-string check and can never parse the figure into a commitment
  column. Same property, one fewer column, and both tests were re-pointed at the new
  mechanism rather than deleted. **The prediction is discarded, not relocated** — if you
  were reading Adzuna's estimates, they are gone — and a downstream store declaring its
  own `salary_estimated_*` columns sees them go permanently NULL on upgrade rather than
  error.

  **Why they were removable, stated carefully — the obvious argument is wrong.** The
  pair existed so a model's guess could never sit beside a commitment, and on the last
  release that *shipped* them the separation leaked anyway: **all 6,633 rows carrying an
  estimate also rendered it as `"$129,584–$129,584"`**, a point estimate shaped like a
  posted range, with **0** carrying a commitment figure `[live prod, engine 0.8.2]`.
  **But that leak was closed earlier in this same release** — by the display-string
  quarantine and the point-value fix listed under Fixed — so by the commit that removed
  the columns a predicted row already emitted no string and the separation was intact.
  The leak is the *history* of why the pair existed. **The reason it went is that
  nothing downstream ever read it:** the one known consumer writes both columns into its
  own schema and reads them back nowhere.

  **One migration path broke and it is called out above:** the `department`
  deprecation note recommended `parent_company` as the recovery on USAJOBS, because that
  adapter assigned `DepartmentName` to both. With `parent_company` removed, USAJOBS rows
  carry the employing department **only** in `department` — so it disappears at 1.0 with
  nothing to fall back to. Copy it first if you consume federal postings.

- **The `remote` boolean is gone; use `remote_type`.** It was exactly
  `None if remote_type is None else remote_type == "remote"` on **7,568 of 7,568 rows**
  `[local 94-board harvest, 0.9.0]` — two homes for one fact, and the bool was the
  weaker home. It cannot express `hybrid`, so **1,679 hybrid rows carried
  `remote: false`**: true, and indistinguishable from on-site to anything that only
  checked the flag. Nothing in the package ever read it — `scoring.is_remote` reads
  `remote_type` and derives its own flag, and its comment says why. `emit._nested`'s
  `remote.is_remote` key goes with it.

  **Migrating:** derive it, and keep the tri-state —
  `None if remote_type is None else remote_type == "remote"`. Collapsing `None` to
  `False` asserts "not remote" on every row nobody classified, which is **3,399 of
  7,568** in that harvest. (An earlier draft of this work proposed the two-term form
  `remote_type == "remote"` as "100% equivalent"; measured literally it agrees on only
  4,169 of 7,568 rows, because it turns every unknown into `False`.)

- **`locations[]` entries no longer carry a `url`.** Each entry is now
  `{raw, city, state, country}` — four keys, not five. The key held the posting's own
  apply url on **9,585 of 9,585 entries** in a 7,568-row harvest, with **zero**
  differing from the row's `url` `[local 94-board harvest, 0.9.0]`.

  Redundancy is not the reason it went. **It advertised a per-place apply link that
  not one of the nineteen sources publishes**, so a consumer could reasonably have
  built a per-office apply flow on a value that never varied. Ashby's own adapter
  docstring had said so for as long as the key existed: the vendor's
  `secondaryLocations[]` entries carry an address and nothing else.

  The one construction path that *could* produce a different value made it **worse**:
  `fetch_workable` builds the record's url from three fallback terms and built the
  entry's from two, so with both vendor keys absent the entry was `null` while the
  record had a working constructed link. Recovery for a consumer is the record's own
  `url`, which is what the entry always held.

### Changed — BREAKING

- **`remote_areas` is populated on far fewer rows, and that is the fix.** Three changes below
  under **Fixed** stop the field asserting a boundary nobody stated — an office address the
  location parser could not read, a search-mode token from google_jobs, and an English
  pronoun mined out of an HN comment body. The first two move **665 rows** from a populated
  `remote_areas` to `null` in a 7,545-row local harvest and **~2,502 rows** in a 67,481-row
  production store — roughly a **30% drop in fill rate**. The third empties it on far fewer
  rows and is **the only one of the three that changes what a consumer serves**.

  It is filed here rather than only under Fixed because a consumer can **filter** on this
  field, not merely display it, and a dashboard measuring "how many rows carry a boundary"
  will read the correction as a regression. **The old values were not conservative, they
  were wrong in the permissive direction:** through `scoring._region_allowed` an empty list
  satisfies every policy unconditionally, so a posting reading "Candidates must live in the
  United States" was admitted into a Germany-only filter.

  **MEASURED AGAINST THE REAL CONSUMER, and the answer is zero.** `remote_areas` is
  load-bearing in jobfitr's US-only intake — `store.py`'s own comment puts it at **18x the
  reach of the currency test** — so the obvious fear is that emptying it lets foreign rows
  through. Replaying jobfitr's actual `servable_in_us` over the **67,481-row production
  store**, before and after:

  ```
  rows with a populated remote_areas                 10,723
    ...this change empties (prose path only)          2,492
      short-circuited before `areas` is read              0
      actually reach the remote_areas branch          2,492
        intake decision UNCHANGED                     2,492
        kept today -> dropped after                       0
        dropped today -> kept after (a leak)              0
  ```

  **Zero, for this change, and the reason is structural rather than lucky:** every value it
  removes was _derived from the location text_, and jobfitr's fallback branch re-reads that
  same text (`place_evidence(job["location"])`). The evidence is not lost — it stops being
  laundered through a field whose contract says the posting **stated** it. The areas verdict
  and the text verdict agree on all 2,492.

  **But the HN location fix below is a different story, and 5 production rows do move.** It
  shortens `location`, which removes a spurious `US` that the English pronoun had put into
  `remote_areas` — and a `US` entry makes jobfitr's areas branch **short-circuit to KEEP**,
  skipping the text fallback entirely. So removing it is not neutral there. Replaying the
  same filter over the 172 production HN rows:

  ```
  rows whose remote_areas changes under the cap          21
    KEPT today -> DROPPED after                           5
    DROPPED today -> KEPT after (a leak)                  0
  ```

  All five leave correctly, and each is a job a US-based worker cannot take:
  `REMOTE (EU, Switzerland, Norway)` · `Toronto, Canada REMOTE (Canada only)` ·
  `HYBRID (Berlin) or REMOTE (CET ±2h)` · `Remote (Italy)` · `Madrid (ONSITE 60%)`. Every one
  was reaching a US-only board on the strength of the word "us" appearing in prose like
  "read more about us here".

  **Two caveats on that 5.** Production stores the old joined location, so the cap is applied
  to the joined string rather than per raw segment — this sizes the class rather than
  counting it exactly. And it measures the cap alone; the block-tag split that follows it
  removes further pronoun matches, so **5 is a floor**.

  **Nothing reaches a downstream consumer without a deliberate act.** jobfitr pins
  `job-radar>=0.8,<0.9`, so this cannot arrive through a `git pull` on its box — someone has
  to edit the pin. That quarantine is why a contract change can ship at all.

- **Deriving a country where the engine previously had none removes 2 rows from a US-only
  consumer's store** — both foreign jobs that were passing as unknown-country. jobfitr's
  intake lets a **blank** country through (`if country not in ("", "US")`), so a row that
  gains a real foreign code newly fails it: a Databricks role in `Madrid; Milan, Italy;
Paris, France` (now `IT`) and a MongoDB role in `Dublin; Ireland` (now `IE`).
  `[local 94-board harvest]`, reproduced independently. The two changes are **independent** —
  measured together against jobfitr's real filter, the country derivation neither cancels
  nor creates any of the boundary change's decisions.

### Added

- **`text_basis`** — a new record field saying what KIND of body `text` is, when it is
  not an ordinary one. `excerpt` where the SOURCE truncates it (Adzuna caps every
  description at 500 characters and ends it with an ellipsis: 275 of 275 rows locally,
  7,146 of 7,150 `[live prod, 2026-08-20]`, and a live probe confirms the API carries no
  fuller field — it reproduces on a non-tech query too). `synthesized` where there was no
  prose body at all and the adapter BUILT one from structured fields (Braintrust, 29 of
  29 rows, ~157 characters). `None` everywhere else.

  **There is deliberately no `full`.** Seventeen adapters would have to claim a
  completeness nobody has measured, which is exactly the plausible-looking guess
  `engine._coerce` exists to refuse; `None` — not characterized — is the truth. And
  `text is None` already carries "the source sent no body" (SmartRecruiters, 250 of 250
  rows), so that state needs no vocabulary entry. Closed vocabulary in `vocab.TEXT_BASES`
  beside the other four, enforced by the same source-reading test, and **set in the
  adapter rather than sniffed** — a `len == 500 and endswith("…")` detector would
  mislabel the first Greenhouse posting that happened to be that shape. Additive and
  `None`-defaulted. Without it a 500-character excerpt and a 6,870-character description
  are indistinguishable in the record, and 10.6% of production rows are excerpts.

- **Ashby postings now carry every office they name.** `secondaryLocations` is a
  structured per-place array Ashby has always sent — populated on **422 of 1,730 live
  postings (24.4%)**, each entry carrying its own `addressLocality` / `addressRegion` /
  `addressCountry` — in a response the adapter already fetched. `fetch_ashby` never read
  it (`grep` returned zero hits), so Ashby emitted no `locations` key and the engine fell
  back to splitting the display string, which on this source names a single place: a
  posting open in three offices reported one. Verified live after the change — **203 of
  906 postings now emit a real multi-place list**, up to six entries. The nested `state`
  is canonicalized in the adapter, because `_coerce` applies the US-state-is-a-code rule
  to the scalar only and builds `locations[]` only when an adapter left it `None`.

  Every other geography fix in this release parses a display string harder; this one
  stops parsing and reads the array. **No per-place url** — the entries carry an address
  and nothing else, which is the vendor's shape, not an omission here.

- **`seniority_raw`** — a new record field holding the vendor's level string verbatim,
  the partner `employment_type_raw` has had since 0.7.0. `None` on the `title` basis,
  because nobody quoted anything there. Additive and `None`-defaulted.

  It is emitted in NDJSON and **deliberately not** in `shortlist.csv`, whose 19
  columns are a human shortlist rather than the record contract — `seniority` is not
  there either, and a raw without its normalized partner would be incoherent. Stated
  in `shortlist.py` so the divergence is a decision rather than a discovery.

  Adding it to `_CONTRACT_FIELDS` without adding it to the emitter turned
  `test_the_machine_feed_carries_the_whole_contract` red within seconds. That test
  exists because 23 of 29 contract fields were once never emitted at all, and it is
  the clearest example in this repo of a check that can actually see its own failure
  — worth preserving exactly as it is.

### Fixed

- **Hacker News shipped the entire job posting in the `location` field.** An HN comment's
  pipe segments are `Company | Title | Location | …`, the convention is loose, and a comment
  that runs out of pipes put the whole body in the segment `_hn_rows` reads the location
  from. hn `location` averaged 461 characters and **reached 2,158**, against a maximum of
  331 for greenhouse, 121 for themuse and 34 for ashby — 82 of 196 rows over 100. Every
  prose rule in `vocab.remote_scope` is written for a short location string, so this
  produced wrong values rather than merely noisy ones:

  | the posting's own words                                 | recorded boundary                      |
  | ------------------------------------------------------- | -------------------------------------- |
  | `REMOTE (EU, Switzerland, Norway) … A lot of us have …` | `["CH","NO","US"]`                     |
  | `Toronto, Canada REMOTE (Canada only) …`                | `["CA","US"]`                          |
  | `ONSITE, NYC … backends are global scale built on AWS`  | `[]` — stated worldwide                |
  | `Remote LATAM $3.5k–$4.9k/mo …`                         | six countries invented from body prose |

  `US_LOCATION_RE` carries a bare `us` deliberately — vocab says so in as many words,
  because "Remote - US" is 227 rows — and against 2 KB of prose it matches the English
  pronoun. **Three of the twelve affected rows matched inside a URL**
  (`https://grnh.se/bhfswi9e5us`). Max location length is now **119**; 82 locations change
  and **22 boundaries are corrected**, every one toward the posting's own words.

  **Attribution, decomposed against the raw comments** so the two HN entries in this release
  do not have to be summed by guesswork. Re-fetched 196/196 originals from HN's Firebase
  endpoint, because `flat.ndjson`'s `text` is post-strip and structurally cannot show a
  pre-strip change:

  ```
  cap alone (this entry)          22 boundaries corrected
  block-tag split adds (below)     2
  the release                     24
  ```

  Cleanly additive, no overlap. The split's two are both the pronoun again — `Join Us in
  Building` and `to help us` — sitting past the block tag where the cap could not reach
  them.

  **Truncated, not dropped.** Filtering over-long segments out instead emptied the location
  entirely on 9 rows whose header and body share one segment (`REMOTE (US) Origamics is
building…`) — discarding a genuinely stated boundary to remove the noise attached to it.
  **The cap is 64** because a mid-token cut invents places: at 48, `Remote (USA, most
states) or Onsite (NYC, NC, MA)` truncates inside the list, `split_place` reads the
  fragment as a city with state `NC`, and a correct `["US"]` is suppressed. 64 is the
  shortest cap that keeps every measured multi-place header intact.

  **Stated because a green suite would otherwise imply otherwise:** the word-boundary rewind
  is **not exercised by any real row at this cap** — all 196 segments were truncated with
  and without it and zero changed. It is kept because the mid-token failure is real at a
  smaller cap, and the test pins it with a _constructed_ string. The first version of that
  assertion passed with the rewind deleted, which is the same green-but-blind shape this
  release keeps finding. This is a **mitigation, not a cure**: a body beginning inside the
  first 64 characters still contaminates, on 2 measured rows.

- **…and the cure it names: an HN header ends at a BLOCK TAG, so the boundary is read
  before the strip.** The cap above bounds the damage without removing it. The real
  separator between an HN comment's header and its body is a `<p>`, not a pipe and not a
  newline — `Portless | AI Engineer | Remote (North America) | $180k-$230k<p>AI usage today
is basic…` — and `util.clean` flattens that tag into the same text run. By the time
  `_hn_rows` splits on pipes the boundary is gone, so trimming afterwards can only make the
  value **shorter, never correct**: on that row the 64-char cap still leaves 83 characters,
  49 of them pitch copy. `_hn_rows` now splits the **decoded markup** at the first block tag
  and reads the location out of the header, which returns it outright:
  `['Portless', 'AI Engineer (Founding seat)', 'Remote (North America)', '$180k-$230k']`.

  This is the invariant 0.9.0 already ships for `sections` — _structure is read BEFORE the
  strip, or not at all_ — applied to the one adapter that was still reading a structured
  signal out of stripped prose.

  **The split and the cap COMPOSE, and neither is redundant.** Measured over 196 live
  comments, maximum `location` length:

  |                           | max     | mean     | over 64 |
  | ------------------------- | ------- | -------- | ------- |
  | cap only (previous entry) | 119     | 49.6     | 68      |
  | split only                | **548** | 40.2     | 19      |
  | split then cap            | **97**  | **33.3** | **15**  |

  Split-only reaches a _worse_ maximum than the cap alone, because some comments carry no
  block tag before a long header — the cap is the backstop for exactly that tail. Removing
  either regresses the other's weak side; do not simplify one away.

  **Only `location` reads the split.** `parts[0]` and `parts[1]` reproduce company and
  title on 196 of 196 measured comments and are untouched, as is the segment scan feeding
  `remote_type`/`employment_type`. A comment whose block tag precedes its pipes falls back
  to the old segments rather than emptying a populated location — 4 of 196 split short, 0
  lose a location today, and the fallback keeps that true for a thread that formats
  differently next month.

  **`_reports/flat.ndjson` cannot verify this and its test fixture is inline for that
  reason:** the stored `text` is post-strip and contains zero markup tags on all 196 rows,
  so a pre-strip boundary is structurally invisible in it. The measurement was taken against
  HN's Firebase item API. A corpus that cannot contain the defect cannot clear the fix.

- **Hacker News shipped a URL that could not reach the posting on 127 of 196 rows, and 48
  of those were a link the response was carrying correctly all along.** HN renders a long
  link as `<a href="FULL">https://boards.greenhouse.io/acme/j...</a>`. `util.clean` strips
  the tag, keeps the **display text**, and discards the href — so `_hn_rows` then regexed a
  URL with a literal ellipsis out of the cleaned prose. Those 404. They skew to
  `boards.greenhouse.io` and `jobs.lever.co`, so the bug destroyed the **highest-value**
  links in the source. `_hn_rows` now reads the href out of the decoded markup first — the
  same invariant 0.9.0 ships for `sections` and applied above to the location.

  Measured over 196 live comments, before → after:

  | url bucket                       | before | after   |
  | -------------------------------- | ------ | ------- |
  | deep link (reaches a posting)    | 69     | **107** |
  | truncated — dead by construction | **48** | **0**   |
  | bare company homepage            | 46     | 34      |
  | HN comment thread                | 33     | 55      |

  62 rows change. Link quality on the rows the fix touches goes from **21%** reaching a
  real posting to **60%** (200 plus a path depth ≥ 2, on the 47 URLs the fix newly
  introduces). It does not reach 100% and cannot: a month-old thread contains postings that
  have since been taken down, and 9 of those 47 are already 404/410.

  **Two gates, because a recovered link must EARN its place rather than inherit it.**
  - **Tier 1 only.** Selection reuses `_is_direct_apply` — a known ATS host or the
    employer's own domain — and deliberately **not** `_best_apply_link`'s full preference
    order, whose middle tier is "anything not on the known-aggregator list". That list is
    measurably under-populated (it is why google_jobs preferred `learn4good.com` over
    LinkedIn), and applying it here would promote 15 unclassified hosts and, on one
    measured row, trade an employer homepage for `linkedin.com/jobs/view/…`. Reuse the
    tier, not the order.
  - **Slug-versus-company.** A host check proves a board is real, never **whose** it is:
    the Phaselaw comment carries `jobs.ashbyhq.com/Pear-VC/…`, an investor's board posting
    for a portfolio company, and the ATS allowlist waves it through. Handing a user the
    wrong employer's posting is worse than handing them a broken link — the broken one
    fails visibly. Gated on **recovered** links only; a URL the poster typed is never
    second-guessed.

  **A truncated URL now falls back to the HN comment link.** It is a known 404, and the
  thread reaches the comment holding the posting's own details. This is not a promotion:
  `_is_direct_apply` reads `False` on `news.ycombinator.com`, so those rows stay honestly
  not-direct and stay out of a direct-only consumer's intake.

  **Tier 1 leaves 21 rows unrecovered and that is the accepted price**, not an oversight:
  their comments carry no ATS or employer-domain href at all. Taking 15 unvetted hosts to
  rescue 15 links that are mostly dead anyway is the worse trade.

  **`first_seen` resets on 29 hn rows.** `shortlist._upsert_locked` recovers a moved
  `dedup_key` by URL and a moved URL by key; this release moves both, so neither index
  fires. Splitting the two across a harvest would have cut it to 16 — **the sequencing was
  available and deliberately not taken**, because the only harvest that re-keys the store
  writes the owner's live shortlist and polls hundreds of third-party endpoints, which is
  not worth 13 rows. Measured against that store today: 1,650 rows, **every `status`
  empty**, so nothing recoverable is lost _today_. That conditional stops being true the
  day the owner applies to something.

- **`direct_apply` was decided per SOURCE and never looked at the URL, so the same host
  carried opposite verdicts.** `jobs.ashbyhq.com` is direct on 1,202 rows and not-direct on
  10 — the only difference being that hn found the second set. 92 rows corpus-wide sit on a
  known ATS or the employer's own domain while reported not-direct, and **every one is hn**,
  because it is the only source carrying a link a human typed. Every other breadth
  aggregator serves its own URL, so per-source and per-URL agree there. In production all
  172 hn rows are frozen out of the consumer's intake, so this is a **resurrection** of 85
  rows rather than a relabel.

  `engine._coerce` now reads `source_rule OR _is_direct_apply(url, company)`.

  **Monotone, never replacing — this is the design, not a style choice.** Swapping the
  source rule for the URL rule **demotes 2,638 rows in a 67,481-row production store**
  (eyecare-partners 286, esri 204, zipline 201, okta 80, buckner 34) to gain 85 — **31:1**,
  every one a genuine employer careers page. And they do not drop cleanly, they **rot**:
  intake rejects them on every harvest while the stale rows stay served.
  `_is_direct_apply` is positive-evidence-only and structurally blind to a company name
  under 5 characters, so it is a rescue, not an oracle. The downstream consumer reached the
  same conclusion independently and recorded it in its own source as _"WHY
  `_is_direct_apply` IS NOT THE ORACLE"_.

  **A link the adapter RECOVERED does not earn the flag from host shape.** Of the 5 rows
  the ATS-platform additions below newly reach, **4 are 404/410** — `applytojob.com`
  postings expire fast. Promoting on the host would assert "you can apply here" about a
  dead posting: the same lie this entry fixes, pointed the other way and manufactured by
  the fix for it. Only a link the poster typed can promote.

  **The test that guarded this could not see it.** Its docstring says the question is
  _"can you complete an application from this URL"_ and _"not is it a depth adapter"_ — and
  all six of its cases call `_coerce` **with no `url` key at all**, so it exercised only
  the branch that was wrong. A new test passes URLs; all six original assertions still pass
  untouched, because `_is_direct_apply("")` is `False` and the OR is a no-op without one.

- **The company name can be the ATS's SUBDOMAIN, and a substring test cannot tell.**
  `bitpay.applytojob.com` matched on `bitpay` and read as BitPay's own careers page. All 6
  measured rows are real ATS platforms, so they were right **by accident** — the identical
  rule promotes `nike.some-aggregator.com`. No such row exists in the corpus, so the hole
  was **latent, not demonstrated**, and is closed before something lands in it.
  `_is_direct_apply` now matches the **registrable domain**, not any substring of the host.

  **`applytojob.com` (JazzHR), `welcomekit.co`, `careers-page.com` and `personio.com` are
  added to `_ATS_HOSTS` as part of the same change, and the order is load-bearing.** Those
  five rows keep their verdict through the _intentional_ branch. Tightening first and
  adding second silently drops 6 measured rows with every gate green — they are one change,
  not two cheap ones. `personio.de` was already listed and does not match
  `friendlycaptcha.jobs.personio.com`: the same vendor under a second TLD, which is the gap
  that survives an audit precisely because a reader scanning the tuple sees "Personio" and
  ticks it off.

  **One accepted regression, named so nobody "fixes" it.** Shared hosting where the
  _subdomain_ is the owner — `github.io`, `netlify.app`, `vercel.app`, `pages.dev` — is
  structurally invisible to a registrable-domain match. `joulent.github.io` is **1 row in
  7,545** and stays unrecognised. Allowlisting `github.io` would certify every project page
  on GitHub as a direct apply, which is a far worse trade than one missed employer. The
  test asserts the failure on purpose.

- **google_jobs asserted a stated-worldwide eligibility boundary on 43 rows, off a token
  that describes the query rather than the posting.** Google's `location` is `Anywhere` on
  every work-from-home result under `&ltype=1` — 43 of 43 locally, and a real city
  (`Vancouver, BC`, `Surrey, BC`) on the 9 that are not. It varies with the **search mode**,
  not with the row, so it is not evidence about any posting. `derive_remote` parsed it into
  `remote_areas = []`, and through `scoring._region_allowed` an empty list is **the one
  unconditional bypass in the scoring layer** — so each of those rows satisfied every
  `allowed_scopes` policy a user can set. **11 of the 43 state a US-only bound in their own
  title, body or URL**, including one titled `Open-Source Machine Learning Engineer - US
Remote` recorded as open to the world, and another whose body reads "Candidates must live
  in the United States."

  The mechanism underneath: **`None` meant both "unstated" and "derive me"**, so an adapter
  had no way to say _there is no boundary here, do not invent one_. It now can —
  `remote_scope_raw is None` is the adapter declaring it recorded no boundary evidence, and
  that is the only state in which the location string is the engine's to read. **Whoever
  supplied the raw owns the parse.** google_jobs records Google's own word as evidence and
  leaves the boundary unstated; its `remote_type` still comes from `work_from_home`, a real
  vendor boolean, and is untouched. Measured: 43 rows lose the `[]`, **348 adapter-supplied
  boundaries are kept, and zero rows lose a boundary the location legitimately stated.**

- **A full week in the office was labelled `hybrid`, and a qualified telework was labelled
  `remote`.** Three defects in `remote_signal`'s body lane, 100 rows, every change from a
  wrong value to a right one:
  - `_HYBRID_RE`'s day-count alternatives never looked at the number, so `in office 5 days
a week` read as a split week. **38 rows** — and **35 are one employer** (Postman, "we
    are in office 5 days a week for all roles"), 2 Anthropic, 1 OpenAI. A new `_ONSITE_RE`
    runs **before** the hybrid branch, which is the whole fix: the same string also matches
    `_HYBRID_RE` via "days in the office", so behind it the branch is unreachable. **Blind
    spot, stated:** the numeral branch cannot see a spelled-out count above five. 31 corpus
    bodies write "four days a week in the office", all genuinely hybrid today, so the gap
    costs nothing measurable — but no metric built on this check can detect that; only
    reading bodies can.
  - This is also the **first body path to `onsite`**. The negation branch returns
    `(None, None)` rather than a verdict, so `onsite` was previously reachable only from a
    location or title — which is why greenhouse produced 0 of 4,852 and themuse 0 of 216.
    It recognises one narrow shape and does **not** make onsite generally reachable:
    flipping the 2,872 greenhouse rows that say nothing would default an unknown to a
    plausible value.
  - `_ROLE_REMOTE_RE`'s bare `\btelecommut\w*|\btelework\w*` had no assertion structure,
    unlike every sibling branch, and nothing claimed the **qualified** phrasings first. All
    35 telework occurrences across 26 distinct contexts were read: `50% Telecommuting
Permitted` (9), `Part-time telecommuting is an option` (2), `Telework Type: Part-Time
Telework` (2), `part-time telework per our global telework policy` (2) all came out
    `remote`. They are now claimed by `_HYBRID_RE`, which runs first — the documented
    precedence ("the specific schedule beats the general claim") already resolves them once
    they are recognised at all. `Telework Type: Full-Time Office/Project` (4 rows) goes to
    `onsite`, because it says office in its own words. **The bare stems stay:**
    "Telecommuting is available for this position" is a genuine assertion and a test pins
    it; the defect was the missing hybrid claim, not the stem.
  - **A location that IS an arrangement word is now read as one.** `vocab.remote_type` is
    the exact whole-string normalizer every adapter already uses for `workplaceType`, and
    `remote_signal` never asked it about the location. **+22 `In-Office` → onsite, +19
    `Distributed` → remote, 0 flips** — and **all 41 rows are one employer on one source**
    (Cloudflare). Placed **last**, so it can only fill a `None` and never overturn a
    verdict; first instead, it breaks the documented "City (Remote)" demotion on 2 rows.
    Exact-match is the safety property: adding `distributed` to `_REMOTE_RE`, which is
    applied to the **title**, would stamp remote on 22 unclassified distributed-systems
    titles to fix 19 location rows.

  `_HYBRID_LITERALS` gains `telecommut` and `telework` — load-bearing exactly as `anywhere`
  once was, since "Part-time telecommuting is an option" contains neither `hybrid` nor
  `office` and the cheap gate would short-circuit before the pattern ran. All three fixes
  ship corpus-verbatim regression tests, and four mutants (each fix disabled in turn, plus
  the shrunk literal set) were confirmed to fail them.

- **An office address was published as a remote-eligibility boundary on 622 rows.**
  `remote_scope`'s documented rule is stated-only — "a boundary inferred from an office
  city is not a boundary" — and the only thing enforcing it was `has_city`, a PROXY that
  reads `split_place(...)["city"]`. A string the address parser merely could not read came
  back `city=None`, which the gate could not tell from "parsed, and there is no city", so
  the office country was emitted as an eligibility claim: `US` (109 rows), `United States`
  (89), `London, UK` (50), `San Mateo, CA United States` (48, no comma before the country),
  `Singapore` (34), `San Francisco, CA • New York, NY • United States` (23),
  `Mexico City` (7). 622 rows across 120 distinct locations in a 7,545-row harvest;
  **4,038 of 10,723 boundary-carrying rows in a 67,481-row production store.**

  A location that names no arrangement now states no AREA — this docstring's own rule,
  applied one level earlier than the proxy, and a strictly stronger test since a string
  with no arrangement word cannot be stating a remote bound whatever the address parser
  makes of it. **Areas only:** a region name is never an office address, and of the
  no-arrangement locations that yielded a boundary, 622 were areas and **zero** were
  regions — so gating regions bought nothing and cost `Bengaluru, Karnataka, India, APAC`,
  which states APAC and says nothing about remoteness. Every change is to `(None, None)`;
  no value is partially rewritten.

  **This LOWERS the fill rate of `remote_areas` by roughly 30%, and that is the fix, not a
  regression** — filed under **Changed — BREAKING** above, where it is measured against
  jobfitr's real intake filter at production scale: 2,492 rows lose the field and **zero**
  change what a user sees.

  The narrow predicate that WAS safe on `split_place` itself — a parsed city that is a
  country name is not a city — shipped separately as `_country_is_not_a_city`. Nulling any
  comma-bearing city instead was measured at 2,037 rows newly asserting an office address
  as a boundary, including the `Costa Mesa, California, United States` this module's own
  test pins, and was not taken.

  **The test that should have caught this was green.**
  `test_remote_scope_takes_only_STATED_boundaries` exists to enforce exactly this
  invariant; four of its five strings passed because `has_city` is TRUE on them — the one
  shape where the proxy works — and the fifth passed through the state-name guard, a
  different branch. The failure path was never exercised. Its inputs were hand-written and
  the corpus's are not, and that difference was the bug. It now carries ten real corpus
  locations with their row counts, and the gate was mutated to confirm they fail without
  it.

- **Adzuna: the city was taken from the wrong tier of the vendor's hierarchy — or
  thrown away.** `_adzuna_place`'s docstring said "depth 5 shifts city one slot, so
  branch on length rather than indexing blindly"; the code read
  `city = area[-1] if len(area) >= 4 else None`, which does not branch. At depth 5 that
  is a neighbourhood (`Grand Central`, `Hayes Valley`, `SoMa`), and at depth 3 the `>= 4`
  test **discarded the city entirely** — `'San Francisco, California'` (15 rows) and
  `'New York City, New York'` (8) returned no city while the vendor had supplied a clean
  one. The city is now pinned to its slot: `area[3]`, falling back to `area[2]` at depth 3. **Not a `County`/`Parish`/`Borough` suffix rule** — that is a US-English word list
  and Adzuna's UK, Australian and German hierarchies put a district, an LGA and a Kreis
  in that tier. The test that covered this asserted the unshifted value while its own
  docstring described the shift, so it was green and the documented behaviour had never
  existed.

  **This reads the hierarchy correctly; it does not make Adzuna's geography correct.**
  `'Times Square, King County'` resolves to `state='WA'` on 3 rows, and Times Square is
  not in King County, Washington. _Inferred from source and harvest output — there are no
  Adzuna API keys in this environment, so unlike the Ashby and Greenhouse findings this
  one was never confirmed against a live response._

- **Ashby: a `state` that was just the country repeated, and the vendor's own trailing
  whitespace.** 36 of 1,730 live postings put the country in `addressRegion`
  (`("UK","UK")` 16, `("Australia","Australia")` 7, `("Singapore","Singapore")` 5), and
  11 send it with trailing whitespace (`"California "`) straight into a column that gets
  grouped on. `_ashby_place` now compares the two fields as **raw strings** and trims at
  the boundary. The obvious rule — drop a region that _resolves_ to the row's own country
  — was measured and rejected: `England` resolves to GB but is a genuine ISO 3166-2:GB
  subdivision, and **27 of the 34 values that rule deleted were correct (39% collateral)**.
  `vocab._COUNTRY_CODES` carries that alias for prose matching; borrowing it to validate a
  data column is what makes it wrong. String equality catches 36 of 43 with zero collateral.

- **Geography: the subdivision was being left inside `city`.** `vocab.split_place` splits
  on the LAST comma, so `"Costa Mesa, California, United States"` matched the
  country-name branch, wrote `"Costa Mesa, California"` into `city` and left `state`
  null. **11,447 of 67,481 rows `[live prod, 2026-08-20]` — one in six — carry a comma
  inside `city`**, and 53.7% have no `state` at all. The three-part branch already
  re-split the head; it was gated on a two-letter tail, so a spelled-out country never
  reached it. `split_place` now re-splits a comma-bearing head there too, and reads a
  spelled-out US state as a tail (`"Mountain View, California"`, 449 corpus parts, which
  previously returned nothing at all).

- **Geography was skipped entirely whenever an adapter set any ONE field.** The fallback
  in `engine._coerce` was gated on city AND state AND country all being `None`, which is
  not what the paragraph above it claims ("ONLY fills what the adapter left None … can
  add, never overwrite"). Lever sets `country` from a real vendor field on 135 of 135
  rows, so the gate never opened and its own `"New York, NY"` was never read: **0 of 135
  Lever rows had a city.** Now per-field — and **gated on country agreement**, which is
  required rather than defensive: SmartRecruiters sends `"bengaluru, in"` with country
  `IN`, and `split_place` reads that `in` tail as INDIANA. Ungated, the fix wrote a US
  state onto 60 Indian rows.

- **A work arrangement was being turned into a city.** `_REMOTE_STRIP`'s `(?![a-z])`
  boundary is satisfied by a hyphen, so `"Remote-Friendly"` became the city `"Friendly"`
  — a real town in West Virginia, so not even self-evidently wrong downstream. 14 rows
  `[live prod]`. A hyphen-compound is now one token **unless its fragment resolves as a
  place**, which is what keeps `"Remote-USA" -> US` working; a blanket hyphen guard was
  tried first and silently lost it.

- **A subdivision or a region sitting in `city` now goes to the field that fits it.**
  `"Remote - Illinois, USA"` yielded `city="Illinois"`; it now yields `state="IL"`, and
  `"LATAM, Brazil"` yields no city rather than the city of LATAM. This one is why the
  release is worth shipping as a whole: **the other geography fixes alone move the
  invention class from 51 distinct bad parts to 52**, because re-splitting
  `"Americas, Europe, Israel"` turns `city="Americas, Europe"` into `city="Americas"` —
  visible junk into plausible junk, which is strictly worse. With this rule the same
  count goes **51 -> 22**.

- **`locations[]` and the scalar fields no longer disagree.** The nested branch parsed
  raw while the scalar stripped arrangement words first, so one row carried
  `city="Illinois"` beside `locations[0].city="Remote - Illinois"`. Both read through one
  helper now: **125 nested entries carrying a separator or the word "remote" in `city` -> 0.** Multi-location strings also split on `|` and `•`, not just `;`.

- **Multi-location splitting no longer manufactures places.** `|` does not mean the same
  thing on every board: Greenhouse uses it for separate offices, **Ashby uses it as a
  hierarchy** (`"US | Illinois | Chicago"` is one office), and some Greenhouse boards
  append coordinates. A blanket split emits 4,789 `locations[]` entries `[live prod]` of
  which **2,562, across 1,332 rows, are not a place under any reading** — a latitude
  receiving a city, a state, a country and an apply url. Parts that cannot be established
  as places are dropped, and the whole string is kept when none survive (146 rows). The
  test is structural — a bare number, an unbalanced parenthesis, nothing left after the
  arrangement words — never a place list, which would be an artifact of a US-heavy tech
  corpus.

  **`dedup.normalize_location` deliberately keeps `;` only**, and now says so: adopting
  the wider set changes 1,488 keys — and so 1,488 user-facing job ids — to buy 2 merges.

- **`state` no longer reads a bare `"…, Georgia"` as the US state.** Exactly four US
  state/territory names are also ISO 3166-1 country names, and three of them
  (`American Samoa`, `Guam`, `Puerto Rico`) resolve to the same code under both
  registers. Only `Georgia` conflicts. Scoped to the two-part bare tail: the 283 prod
  rows reading `"…, Georgia, United States"` are decided by the country branch and keep
  their state; of the 118 ambiguous ones, **11 are the Republic of Georgia** (Tbilisi 10,
  Adjara 1). 107 rows forgo a gain — returning to the all-None they already have — so
  that 11 do not invent `country="US"` for a Georgian job.

- **A `city` that is a country name is now `None`.** `"Canada, United States (Remote)"`
  yielded `city="Canada"`. Nine rows also gain a correct stated remote boundary as a
  result, because the category error was suppressing it.

- **An inline tag no longer leaves a space where it stood — 4,580 → 62 spaces sitting
  before a punctuation mark.** `clean` replaced EVERY tag with a space, which is right
  for a block tag and wrong for a bold or a link: `At <a>Smartsheet</a>, your ideas`
  became `At Smartsheet , your ideas`, on 67.7% of rows with a body, and a tag boundary
  landing mid-word split "the" into "t he". Measured on 2,712 raw bodies `[live fetch, 9
boards, 2026-08-20]`; also 5 → on himalayas and 7 on a non-tech Muse sample.

  **The uppercase guard is the whole rule**, and it is why this is not simply "delete
  inline tags": a word split by a tag always CONTINUES in lower case and two distinct
  words do not, so an uppercase letter after the run means the space stays. Without it
  `<strong>Requirements</strong>Must have` collapses to `RequirementsMust`. Zero words
  were glued — a camel-case proxy held at exactly 3,099 before and after. The
  case-insensitive flag is **scoped to the tag half on purpose**: a module-level `re.I`
  case-folds the `(?![A-Z])` lookahead too and silently turns the guard off, with every
  test still green. Two people hit that independently while building this.

  **Composition, reported rather than explained:** this change and the header rule each
  produce **0** sections whose span cannot be located. Applied together they produce
  **10** of 22,202, which fail safely — the section keeps its `type` and `header` and
  carries no offsets, which is what `clean_with_sections` documents for a disagreement
  it cannot resolve. The cause is not understood and could not be reproduced on a
  separate 910-body corpus at any combination, so it is recorded as corpus-specific and
  nothing further is claimed about it. The two changes ship as separate commits for that
  reason.

- **A Braintrust row reports `sections: []`, not `null`, because it has a body.** `null`
  means "there was no body to read" and `[]` means "a body with no headers"; this adapter
  emitted `null` while shipping a built body on 29 of 29 rows, which is a false statement
  about the posting and collapses the exact two-state distinction the field exists to
  carry. Its synthesized text now goes through the same `clean_with_sections` path as
  every other body, which yields `[]` because a built sentence has no markup.

- **Lever reads the markup body and promotes its own list headings — `sections` on 488
  of 489 postings, up from 13 of 135.** This adapter read `descriptionPlain` and
  `additionalPlain`, so it could never produce a section: a header exists only in
  markup. It is the same defect Ashby had, one adapter over, missed when Ashby's was
  fixed in 0.9.0. Lever also labels every `lists[]` entry with its own heading —
  `{text: "What We Require", content: "<li>…"}` — and the adapter appended that label as
  bare prose, discarding structure the vendor states outright and then failing to find
  it again. Wrapping it in a heading tag transcribes the source rather than guessing at
  it. Measured `[live api.lever.co, 4 boards, 489 postings, 2026-08-20]`: rows with no
  sections 471 → 1, typed sections 1 → 1,336, 0 unresolved spans.

  **`text` changes on every Lever row**, which is the real cost: median 7,286 → 7,304
  chars on palantir, because the HTML body drops inline URLs and bullet markers the
  plain field spells out — the same trade Ashby took. **Fit scores are unchanged on 482
  of 489 postings**; the 7 that move span −2 to +3, mean −0.29. The `or` fallbacks are
  load-bearing: an employer who fills only the plain field would otherwise ship with an
  empty body, silently, because `""` is a legal value and nothing raises.

- **Only a HEADING starts a section — inline emphasis inside a sentence no longer does.**
  0.9.0 promoted every `<strong>`/`<b>` it found, anywhere. So
  `...leverages state-of-the-art <strong>computer vision, deep learning, and generative
AI</strong> to automatically analyze...` produced a section headed by that noun phrase
  whose span opened on the word "to": **5,753 sections across 2,056 rows** `[local
94-board harvest, 0.9.0]`. Measured on 2,712 raw bodies `[live fetch, 9 boards,
2026-08-20]`: headers that do not start a line **1,701 → 94**, spans opening
  mid-clause **1,334 → 158**, headers with no letter or digit **13 → 0**, `type: null`
  46.9% → ~34%, and **0 sections whose span could not be located**, unchanged. This also
  closes the mid-sentence half of the "span ends mid-sentence" and "span starts on
  punctuation" reports — they were one bug, not three.

  A heading is **block-initial**: nothing but whitespace and punctuation between it and
  the start of its block. What FOLLOWS it is deliberately not tested. Requiring the bold
  to be the whole line scores better on every span-quality metric and is wrong — it
  deletes the label-value paragraph (`<p><strong>Visa sponsorship:</strong> We do
sponsor visas!`, 487 of 487 postings at one employer) and took `eeo_legal` coverage
  from 100% to 0% on three boards. **The metric could not see its own cost, because a
  deleted section has no span left to judge.** Inside a list item the rule is stricter
  and the discriminator is morphology rather than the container — a heading terminates
  its label with a colon, inside the tag or just outside it — because excluding list
  items outright destroys 130 typed headers, 34 of them at a non-tech employer.

  **Known residual, named rather than left to be re-found:** two adjacent bold runs are
  two headings (160 occurrences across 52 of 2,752 bodies, 9 of 11 vendors), which is
  correct for `<h2><strong>Key responsibilities</strong><strong><br></strong></h2>` and
  wrong for a bolded label followed by a bolded value — `Equity grade:` → `2`,
  `Recruiter:` → a recruiter's personal name. That second case is now handled by the
  colon rule applied at the SECOND boundary: a bold is a heading only if the bold before
  it did not terminate a label. Not a third clause — one idea in two places. **68
  sections still read a value as a heading**, down from 94, with the typed-section count
  unchanged at 13,925 and every other defect metric flat on two corpora that share no
  board.

- **`employment_type` is read from vendor metadata by VALUE, not by key name — 1,558
  rows `[local 94-board harvest, 0.9.0]`, and 8,239 rows across 264 employers `[live
  prod, 2026-08-20]`.** Both numbers are real and the second is the blast radius: the
  local corpus is one 94-board slice, so quoting only 1,558 understates what ships by
  5.3×. **A title that contradicts the metadata vetoes the fill** — a hand-read of 150
  sampled rows found the metadata asserting a type the posting denies (`Store Lead -
  Part Time` carrying `Full Time`; `Clinical Lab Scientist (Contract)` carrying
  `Full-time`), 163 rows / 1.91% of the live fill, landing in `shortlist.csv` where a
  CLI user reads them. The title is a **veto, never a rival answer**: reading it as a
  competing answer was measured and abandoned after three attempts surfaced three
  false-positive classes, each a NON-TECH role invisible on the 94 tech boards this
  corpus is built from — `b2b` ("B2B Performance Marketing", a market), `trainee`
  ("Manager Trainee", a permanent trades role), `contract` ("Contract Management
  Lead", a domain noun). Under a veto each costs one unfilled row rather than a wrong
  assertion — and the veto carries **two measured guards**: it does not fire on domain
  usage (without that, 54 of 163 vetoes, **33.1%**, were correct fills discarded), and
  it compares by **overlap rather than difference**, so a title naming a second axis
  (`Customer Operations Intern - Part-time` against `Intern` metadata) corroborates
  instead of contradicting. Net: **8,526 → 8,432 filled `[live prod]`**, 94 withdrawn
  (1.10%). An independent hand-classification of all 163 pre-guard vetoes put the
  false-positive rate at **34.4%**; the guards release 54 of those, and every released
  row is domain usage — no genuine contradiction is given up. **One known residual
  remains and is deliberately unfixed:** `Production Director – Seasonal Content` is a
  domain noun the guard does not catch. It costs one unfilled row rather than a wrong
  assertion, and a single-row entry is exactly the exception-table growth this lens
  refused everywhere else. The rest of the 94 are `Per Diem` vs `Part-time` (31),
  a `(Contract)` term against `Full-time` metadata (23), and explicit `Part Time` /
  `Temp` titles (34). `Contract to Perm` (2) is genuinely ambiguous and vetoing it is
  the safe reading, not a clean win.

  **Two names for one arrangement do not contradict.** `{PER_DIEM, PART_TIME}` and
  `{CONTRACTOR, TEMPORARY}` are compatible: an employer's form offers
  Full-time/Part-time with no per-diem option, so a nursing manager picks Part-time
  and writes `Per Diem` in the title. That is a vocabulary GRANULARITY mismatch — the
  title is more specific than the form allowed — not a disagreement, and vetoing on it
  discarded 40 rows the metadata had right. Final: **94 → 54 vetoes, 0.63%**, and the
  repo's own rule is restored — a structured signal stands when nothing actually
  contradicts it.

  **The pattern this lens should be remembered for.** That class is 100% NON-TECH
  (nursing, creative contract work) and measures **zero** on the 94 tech boards the
  rule was built against — the third such class here, after the title-contradiction
  defect itself and the `b2b`/`trainee`/`contract` false positives. A defect can
  measure zero on the corpus you designed against and be real in production.

  **NOT covered by any of this:** the Braintrust numeric-tag filter and the
  parenthetical strip were never exercised by the hand read. That is untested, not
  cleared — three green passes on a different rule imply nothing about them.

  **How this was found is the more useful half.** The n=150 hand read found the defect
  KIND — 2 rows — and could never have found its RATE: at 0.46% prevalence the sample
  was underpowered by construction, with an expected count of ~1. A mechanical scan of
  all 8,555 rows found 62 in seconds. **A hand read finds defect kinds; only a full
  scan finds their rate.** Both were necessary and neither would have sufficed. It was `None` on 100% of Greenhouse rows while a third of them carried an
  unambiguous type string in `source_extra`. The fix ignores key
  names entirely and tests every metadata _value_ against `vocab._EMPLOYMENT_MAP`,
  accepting only a real map hit. Key names could never have worked: measured `[live
prod, 2026-08-20]`, **61 distinct keys** carry a resolvable value — including
  `Employment Classification (UKG)`, `TH: Employment Type` and
  `Full-Time/Part-Time Status` — while two of the four most obvious names (`Job Type`,
  `Worker Type`) resolve to nothing at all, because their values are `Standard` and
  `Employee`. Fill-only; `Regular` (151 rows) and `Standard` (124) are correctly left
  alone. `permanent` is skipped under a key naming a _term_ (`Contract Type`,
  `Duration`) — `vocab` already flags that entry as its shakiest, and such a key makes
  the flagged failure more likely, not less.

- **Two disagreeing metadata fields now yield `None`, not an arbitrary pick.** 11 rows
  state two different types at once (`Employment Type=Contractor` with
  `Time Type=Part Time`). A source that said two things has not said one; both raws are
  kept, joined and sorted, so the disagreement stays auditable.
- **A qualified contract string normalizes again — 29 of 29 Braintrust rows.** The
  adapter builds `f"contract ({contract_type})"`, which flattened to `contract long`,
  missed the map and became `OTHER`, while bare `contract` maps to `CONTRACTOR`. The
  adapter was defeating its own normalizer. A trailing parenthetical is now stripped
  **only after a direct lookup misses**, so no already-resolved value can change.
- **The Muse no longer reports an employment type it never had — 216 of 216 rows.** The
  adapter read `type`, which is that API's posting-_provenance_ flag: the literal
  string `"external"` on 20/20 rows probed live 2026-08-20. Every Muse row became
  `OTHER`, the single largest contributor to that bucket. `catalog/themuse.md` has
  recorded `employment_type: null` since it was written — the code was reading a field
  the profile says does not exist, the second time in this repo the catalog was right
  and an adapter was not. **The captured fixture said `"Full Time"`, a value the API
  has never sent, which is why the parser test agreed with the bug; it is now a real
  capture.** Together with the two fixes above, `employment_type='OTHER'` drops from
  247 rows to 2.
- **The Muse's canonical level token is no longer discarded.** It ships
  `{"name": "Mid Level", "short_name": "mid"}` in one object and the adapter kept only
  the display string. `short_name` now becomes `seniority` and `name` the raw — worth
  87 rows on a `seniority='senior'` filter.
- **A stated `seniority` is case-folded — 788 rows.** One column held five vendor
  dialects at once, so `seniority='senior'` returned 2,229 rows and silently missed 319
  more spelled `Senior`, `Senior Level` or `Mid-Senior Level`; 179 of those differed by
  letter case alone. **Rungs are deliberately NOT mapped.** Every other vocabulary here
  normalizes onto one someone else published, and none exists for seniority — so
  ordering would be this library's opinion, which `catalog/_SCHEMA.md` ("Fidelity, not
  opinion") leaves to the consumer. Probed live: SmartRecruiters ships LinkedIn's
  published enumeration verbatim, in which `Associate` ranks **above** `Entry level`,
  so the one ladder available to copy would have contradicted a vendor's own published
  ordering on the source where it fires on 60% of rows. `Mid-Senior Level` therefore
  stays `mid-senior level`. `"Not Applicable"` and `"Any"` become `None` with the raw
  preserved — the vendor answered and declined to classify, and that is not a level.
- **Braintrust's opaque numeric ids are out of `tags` _and_ the scored body** — 164 of
  its 227 tag tokens corpus-wide, and every purely-numeric tag in the corpus is this
  source. They reached two fields: `tags`, whose contract says "skills the source
  itself extracted", and `text`, where they were interpolated under the label
  `Skills:` and then read by `relevant()` and `score_and_signals()` on 42 live rows.
  Filtered once upstream of both — cleaning `tags` alone would have fixed half the
  defect while reporting it done.

### Documentation

- `category` and `team` now carry the `catalog/_SCHEMA.md` vocabulary in the contract
  itself: `category` is the catalog's **`function`** (job family), `team` and its
  deprecated alias `department` are **`org_unit`** (the employer's own group). A source
  that publishes only an org unit therefore leaves `category` `None` **correctly** —
  this has been filed as a bug more than once, and deriving one from the other was
  tried downstream and reverted after it filed 895 backend engineers under Science and
  Engineering.
- Corrected three claims that had gone stale: `CLAUDE.md` and
  `tests/test_sources.py` both stated that the downstream consumer reads `department`
  (it does not — the column is absent from its production schema and its code reads it
  zero times), and `catalog/_SCHEMA.md` described a Braintrust `level` mapping that was
  already removed. The `department` byte-identity gate is unchanged and still green.
- **The README's NDJSON key-count table was measured on a harvest that no longer
  exists, and it was four fields stale.** It published 50 / 31 / 30 keys against a
  7,568-row local harvest, and stayed at those figures when `industry`,
  `parent_company`, `salary_estimated_min` and `salary_estimated_max` were removed from
  the record — a docs-and-code-in-one-commit miss. Re-measured on a corpus that is
  named in the caption: **102,799 rows across 7,360 boards, 46 / 29 / 28 keys and
  7,694 / 7,295 / 1,691 bytes** per median record, counting leaves. `text` is 78% of a
  record's bytes rather than 72%. The caption now also states the corpus's coverage —
  **11 of 19 sources, 68% Greenhouse**, with usajobs, adzuna, google_jobs, workday,
  workable, teamtailor, rippling and themuse absent — and which way that biases it, so
  **19 null keys reads as an upper bound** rather than a measurement of the market. A
  number whose provenance is gone cannot be re-checked; naming a skewed corpus beats
  citing an unreproducible one.

## [0.9.0] - 2026-08-20

### Fixed

- **A pay range in `salary` now becomes numbers — 3,489 rows that had a fully formed
  range and five null columns.** Eleven of twelve adapters call `salary_from_text`,
  which returns a DISPLAY STRING and nothing else, and the only text→number parser in
  the codebase (`vocab.google_salary`) was wired to one adapter and could not read a
  period-less range: its pattern ends in a mandatory `an?|per <unit>`, which
  `$200,000 – $250,000` does not have. Measured `[local 94-board harvest, 0.9.0,
2026-08-20]`: `salary_basis` was `stated` on 981 rows, **`parsed` on 12**, null on
  6,567 — while **3,500 rows carried a range in `salary` with every structured column
  null**, 74.3% of every row with a salary at all. `README.md:41` promised `parsed`
  meant "read out of free text"; the promise was kept on twelve rows.

  New `vocab.salary_from_display` + `engine.derive_salary`. **One parser at the
  boundary, not twelve in the adapters** — per-adapter parsing is how three geography
  vocabularies reached one column. Recovers **3,489 of 3,720** target rows (93.8%);
  the 231 refusals are deliberate.
  - **The multiplier distributes leftward.** `$200-260K` means 200,000–260,000, but
    `_G_NUM` binds the multiplier per-number, so the obvious generalization reads
    lo=**200**. A `salary_min` of 200 on a $200K job — wrong by 1,300×, in the column
    this README calls what an employer COMMITTED to, carrying `basis="parsed"`. **26
    rows.** Latent in `google_salary` today and harmless only because its mandatory
    period phrase gates these strings out; making the period optional is exactly what
    arms it.
  - **Refusals, all measured:** a range wider than 5× (catches `$150,000 - 250,000k`
    → \$250 MILLION, and `$306 - $390,000`); a figure under 1,000 with no recoverable
    period (`$30-120` is an hourly rate or a thousands-shorthand and the string cannot
    say which); a lone figure, because `up to $200,000` has no floor to read.
  - **Currency and period are READ, not inferred** — from a sole code or unit within
    90 characters of where the display string sits in `text`. Coverage: currency
    **82.1%**, period **24.5%**; two candidates in the window is a refusal, not a coin
    flip. `country` was rejected as evidence on measurement: `country='CA'` rows whose
    body names one code say **CAD on 107 and USD on 18**, so a country rule is wrong in
    both directions, and 823 rows have no country at all.
  - **A stated period is vetoed on magnitude.** A real Greenhouse posting labels a
    \$140,000–\$220,000 band an "Estimated Hourly Pay Range". Refusing a stated period
    is disbelieving a witness; inventing one from magnitude stays forbidden
    (`vocab.salary()`: _"A period is never guessed"_).
  - **A model prediction never reaches the commitment columns.** Caught by measuring
    the fix, not by reviewing it: a predicted row has NULL `salary_min` by design, so
    a fill-only test waves it through — and `derive_salary` wrote `109106.0` into
    `salary_min` with `basis="parsed"` on a row whose `109106.69` was Adzuna's model
    output. Guarded explicitly on `salary_estimated_*`.

- **A point value is no longer rendered as a range.** `util.salary_range` printed
  `$188,569–$188,569` for every one of Adzuna's **220** point-estimate rows; a reader
  sees a range and reads a precise employer offer, and the only tell was the cents in
  the underlying value, which the formatting rounds away. `_adzuna_pay` kept the
  prediction out of the commitment columns and the display string handed back the
  appearance of one.

- **`engine.py`'s `salary_basis` comment named a token that never existed** (`text`);
  `vocab.SALARY_BASES` is `frozenset({"stated", "parsed"})`.

- **Ashby reads `descriptionHtml`, not `descriptionPlain` — `sections` was empty on every
  Ashby row.** Headers live in markup and nowhere else, so an adapter reading a plain-text
  body cannot produce one. Ashby serves BOTH fields; this one read the plain half, which
  made the feature above dead on the second-largest source: `[]` on **1,198 of 1,198 rows**,
  15.8% of a harvest, 12,661 rows in the downstream consumer's production store. Measured
  `[live probe api.ashbyhq.com, 5 boards, 457 postings, 2026-08-20]`: both fields present on
  457/457, `descriptionHtml` yields **3,981** sections and `descriptionPlain` yields **0**.

  Caught by reading harvest OUTPUT, not code — the adapter, `clean_with_sections`, and the
  `sections` coverage figure were each correct in isolation, and the defect existed only at
  the intersection of one source and one field.

  **`text` changes on Ashby rows as a result, and it is a small loss.** Ashby renders a link
  as `text https://url` in the plain field and as an `<a href>` in the HTML one; `clean`
  drops attributes, so the body loses its inline URLs and bullet markers — mean 4,822 → 4,663
  characters (−3.3%) over 75 postings. Every other HTML source already behaves this way
  (Greenhouse loses its hrefs identically), so this makes Ashby consistent rather than
  uniquely lossy, but a consumer reading URLs out of `text` will no longer find them there.

### Added

- **`sections` — the posting's own structure, read before the markup is stripped.**
  A job body's headers exist only in the vendor's markup, and the `clean()` fix below finally
  removes that markup correctly — which would have destroyed the only structural signal the
  corpus has. So it is captured first: `[{type, header, start, end}]`, where the span indexes
  the record's own `text`. Both halves ship together for that reason; the fix alone would have
  been a net loss of information.
  - **Ten types.** Measured across **478 distinct employers**: `requirements` on 93.5% of them,
    `responsibilities` 92.3%, `about_company` 61.1%, `benefits` 42.5%, `compensation` 37.0%,
    `location_travel` 31.2%, `eeo_legal` 23.6%, `metadata` 5.9%, `apply_cta` 5.4%,
    `fraud_warning` 1.5%. Report coverage **per employer, not per posting** — a posting-weighted
    percentage mostly measures how many roles one employer happens to have open.

    **Those figures are not out-of-sample, and three things qualify them.** The classifier's
    patterns were chosen by mining unclassified headers out of a 730-employer corpus, and **406
    of these 478 employers — 2,312 of the 3,000 postings — are in that same corpus**, so this is
    substantially a training-set score. On the **72 employers that are not**, `requirements` is
    97.2% and `responsibilities` 94.4%, above the headline; the marginal types move more and on
    n=72 (`about_company` 54.2%, `eeo_legal` 20.8%). Second, coverage rises with how deeply an
    employer was sampled — `requirements` is 85.6% for the 125 employers contributing a single
    posting against 98.1% for the 156 contributing five or more — so the denominator is partly
    measuring sampling depth. Third, the same classifier on the larger corpus reads up to 16
    points differently on the marginal types (`location_travel` 47.0% there against 31.2% here),
    so the decimal place implies a stability the data does not have. The two smallest figures
    rest on 7 and 26 employers.

  - **`type` PRECISION is not measured — only coverage.** Nothing here reports how often a
    classification is _wrong_, and two buckets are known to be loose: `eeo_legal` matches a bare
    `commitment to` / `privacy` / `sponsorship` (36% of its entries match only those), and
    `location_travel` matches a bare `remote` (23% of its entries), which files "Lead Remote
    Teams:" as a location section. Treat `type` as a strong hint rather than an assertion, and
    read the retained raw `header` when it matters.
  - **`type: null` is a real answer, not a failure.** Roughly half of all headers are employer
    prose ("Building something special") and forcing those into a bucket would assert something
    the source never said. The employer's raw `header` is kept beside our guess so a
    misclassification can be corrected in a later release without re-harvesting anything.
  - **Spans, not copies.** Carrying each section's text would grow a record by 104%; spans cost
    14.3% and lose nothing, since `text` is right there. A section that cannot be located emits
    **no span at all** rather than a plausible-looking one.
  - **`null` vs `[]`.** `null` means there was no body; `[]` means the body carried no headers.

  **This is effectively a Greenhouse field.** Greenhouse sends HTML on 100% of postings and the
  other eighteen sources send plain text, so they get `[]`. Anything built on `sections` is built
  on about two thirds of the corpus and none of the local lane — adzuna and google_jobs return
  nothing here. Two caveats worth stating rather than burying: `fraud_warning` appears for only
  7 employers in this corpus (10 in the larger one) and **one of them is ~80% of the rows**, so
  it is not a rate to plan on. And the 8,000-character cap bites harder than "undercounts" would
  suggest: **the median body in this corpus IS 8,000 characters** — 1,765 of 3,000 are truncated
  — and on an uncapped corpus the same cut costs `eeo_legal` 11 points (55.1% → 44.1%) and
  `benefits` 3. `eeo_legal` and `benefits` are the late sections, and they are the ones hit.
  That 11-point figure describes the corpora as they were captured, under the OLD `clean()`;
  once a consumer re-harvests on 0.9.0 the bodies are 21.6% shorter, so the same cap bites
  roughly 2.4 points instead.

  Cost, measured against this implementation rather than the prototype it was designed from:
  **412 µs added per HTML posting** (304 → 716), about **9.5 seconds** on a harvest with 23k
  Greenhouse rows; an independent re-measurement on other hardware got 380 µs, so treat these as
  the right order rather than the exact figure. A plain-text posting pays **~3 µs** (191 → 194) —
  the guard skips the split, not the decode, so it is nearly free rather than exactly free.
  Two costs that belong beside it: `sections` adds about **34% to the resident memory** of a held
  row (roughly 4 KB against 12 KB of `text`), which matters because a depth harvest holds many
  rows at once; and Python's `re` holds the GIL, so this CPU competes with the fetch threads
  rather than hiding entirely behind them. No claim is made here about downstream retrieval quality; that has not been
  measured on a second corpus or a second reader.

### Fixed

- **`clean()` stripped HTML tags before decoding HTML entities, which is backwards for any
  source that sends HTML-ESCAPED HTML.** The strip found no `<...>` to remove and the decode
  then turned `&lt;div&gt;` into `<div>` — the function whose job is to remove markup was the
  one creating it. Greenhouse escapes **every** body (2,697 of 2,697 measured across nine
  employers; 116,214 live tags left behind on one board alone) and is roughly 65% of a typical
  harvest, so `text` changes on about two thirds of harvested rows. The Muse, Arbeitnow, HN,
  Workday and Remotive each do it on a minority of postings, which is why this is fixed in the
  one shared helper rather than in the Greenhouse adapter.

  Two consequences worth planning around:
  - **Greenhouse salaries parse for the first time.** `salary_from_text` matched **0 of 809**
    postings on one board before and **424 of 809** after — the pay figures sat inside tags.
    Expect `salary` and `salary_basis: "parsed"` fill to jump on Greenhouse rows.
  - **Fit scores rise on Greenhouse specifically.** 655 of 809 postings moved, every delta
    positive (median +1, max +9), from a 21.6% shorter body and keywords that tags had split.
    That is a systematic uplift of one source relative to the other eighteen, so it shifts the
    cross-source mix of a shortlist and where `min_score` cuts, not just individual numbers.

  Three smaller decisions came with it. The tag pattern is now `</?[A-Za-z][^>]*>` rather than
  `<[^>]+>` — **insurance, not a live fix**: the two produce byte-identical output on all 2,697
  bodies today. What earns the change is that 12 of them carry an inner literal `<` once entities
  are decoded (`travel as needed (<25%) ... to hit the goals`) and none happens to carry a `>`
  after it; the day one does, the loose pattern deletes the clause between them. A block-level
  closer (`</li>`, `</p>`, `</h*>`, `<br>`) now becomes a line break rather than a space —
  verified neutral for score, salary and remote verdict either way. And whitespace collapse uses
  `[^\S\n]+`, so a non-breaking space still collapses as it did before this release; an earlier
  draft of the fix left a literal U+00A0 on two thirds of bodies.

  `clean()` had no test at all, which is how this shipped; it has six now.

  **Not verified:** Adzuna, Google-for-Jobs and USAJOBS need credentials and were not probed,
  so their bodies are unmeasured either way.

## [0.8.2] - 2026-08-15

### Changed

- **Location parsing is ~8x faster.** `remote_scope` rebuilt a regex for all 50 US state
  names on every posting — about three-quarters of its runtime. It now uses the same
  precompiled alternation the country and region maps already use. Output is unchanged,
  verified against the released 0.8.1 across 1,654 strings built from the cases that could
  break it.

### Fixed

- **A vendor's own ISO code now resolves.** `"United States"` worked while the literal
  `"US"` — the string this package itself stores — did not, because the lookup only knew
  country _names_. `"US-TX"`-style subdivisions resolve too, matching what the location
  parser already emits.

  **Seven codes deliberately still do not resolve:** `AR CA CO DE ID IL IN` are each both a
  country and a US state abbreviation, and the location parser already reads `"CA"` as
  California. Resolving them as countries would make the same two characters mean two
  different places, and a wrong country lets a posting through a filter meant to exclude
  it. They stay unresolved with the vendor's text preserved. Measured first: across 136
  live rows from all three sources that populate this field, none sent a bare code — this
  is a consistency fix, not a response to observed data.

## [0.8.1] - 2026-08-14

### Fixed — found by probing live vendor endpoints

- **`"Worldwide"` was recorded as "we don't know".** himalayas declares an unrestricted
  posting with an empty array, which 0.8.0 handles; remotive and jobicy declare it with the
  **word**, and only the location path knew that word. **6 of 18 live remotive rows** — a
  third of that entire feed — came back unstated when the vendor had said "no restriction".
  Same defect as the empty array, on the string vendors.

- **All five continents are kept.** `"Americas, Europe, Asia, Africa, Oceania"` is
  remotive's canonical value and appears on **11% of a live feed**; `ASIA`, `AFRICA` and
  `OCEANIA` were not in the region vocabulary, so a role open to five continents was
  invisible to a searcher on three of them. `NORTHERN AMERICA` added as a near-miss alias.

- **A bounded "anywhere" string no longer reads as unbounded.** Caught before release: the
  Worldwide fix above matched the word as a substring, so `"Anywhere in the US"` and
  `"Worldwide except China"` returned "open worldwide" — and since an empty list satisfies
  every scope policy, `"Anywhere in the US"` would have been admitted into a Germany-only
  filter. The whole name must now BE an anywhere-word. A list of blanks is likewise
  malformed input rather than a worldwide declaration.

**Why none of this was caught before 0.8.0 shipped:** `sources.stated_scope` had never seen
a live vendor response. Every fixture was hand-written and the development corpus was a
downstream store with no adapter-supplied structured fields, so the path was unreachable
even in principle. The live canary proved the adapters _parsed_ and asserted nothing about
what they parsed the boundary _into_.

### Fixed — found by a four-lens panel review of the fixes above

- **A region containing the United States was being used as a non-US marker.**
  `northern america` was added to the default location-exclusion list as well as the scope
  vocabulary, so a role titled "Engineer, Northern America" was dropped as foreign while the
  synonym "North America" passed — opposite verdicts on the same place, from the shipped
  default config. The two lists now splice one shared constant, because the copy that caused
  this was a hand-paste into both.

- **A number in the boundary field killed the whole harvest.** `stated_scope(123)` raised
  `TypeError: 'int' object is not iterable`. Errors are values in this package, but this
  function runs inside the adapter, upstream of the coercion that enforces it. Junk now
  resolves to "boundary unknown" and keeps the vendor's value as evidence.

- **The country and region lookups disagreed about whitespace.** `"North  America"` with a
  double space resolved to nothing while `" Germany "` resolved fine. Normalized once, and
  `remote_scope_raw` now keeps the vendor's string exactly as sent rather than a rejoin of
  the cleaned parts — it exists so a consumer can re-read the original.

- **A continent inside a country name became a phantom region.** Adding `africa` made
  `"Remote - South Africa"` the only one of 425 countries to also carry a continent tag,
  while `"Remote - France"` carried no `EUROPE`. Not false, but inferred — and this field is
  stated-only. Region matches whose span falls inside a matched country name are now masked,
  which closes the class rather than special-casing the one name.

### Changed

- **`asia`, `africa` and `oceania` are now non-US location markers**, alongside the `europe`,
  `apac` and `latam` that already were. If you rely on the default `exclude_locations`, a
  posting whose title or location names one of those three continents is now filtered out
  where it previously was not. Set `filters.exclude_locations` yourself to opt out.

### Added

- **The live canary now checks the eligibility boundary itself** (`pytest -m live`, not CI),
  which is the gap that let the three defects above ship. It asserts the field shapes
  (areas are ISO codes; a region is never a country code), that a stated boundary is not
  silently dropped, and — the one the others cannot catch — that **every word** of a
  boundary is understood, not just the first two. Verified by replaying each defect against
  the day's real vendor values: both go red (14.6% and 20.0% against a 10% gate, from a 0%
  baseline on all three sources). One defect is honestly **not** live-catchable: across 820
  live values, zero vendors send a bounded "anywhere" string, so `"Anywhere in the US"` is
  gated by a hermetic test instead.

## [0.8.0] - 2026-08-14

### Changed — BREAKING

- **`remote_region` is removed. Three fields replace it.** The old column held four
  different kinds of value at once — alpha-2 country codes, ISO 3166-2 subdivisions,
  multi-country region names, and sentinels — which is a documented data-modelling failure
  mode and which forced a US-inclusive special case in the scorer. That special case was the
  tell: it existed only because a **set** was being stored as a **string**.

  Measured on a 31,790-row harvest, **1,162 of the 1,168 adapter-written values (99.5%) fell
  outside the closed vocabulary the field's own docstring claimed** — every adapter wrote raw
  vendor text straight into it.

  | new field          | holds                                                                          |
  | ------------------ | ------------------------------------------------------------------------------ |
  | `remote_areas`     | `list[str] \| None` — ISO 3166-1 alpha-2, or ISO 3166-2 for a stated US state  |
  | `remote_regions`   | `list[str] \| None` — closed tokens (`EMEA`, `LATAM`, …), never a country code |
  | `remote_scope_raw` | `str \| None` — the vendor's own words, verbatim                               |

  **Three states, and the middle one is why it is a list:** `null` = the posting said
  nothing · `[]` = it said **anywhere** · non-empty = these places. Collapsing the first two
  either drops the most permissive rows in a feed or admits the ones nobody classified.

  **Boundaries are STATED only.** A country parsed out of an office address is not an
  eligibility claim: `Munich, Germany` and `Costa Mesa, California, United States` are where
  the desk is. 6,779 area-carrying rows are that shape, and on them the old field meant
  nothing more than `country`. That geography still lives in `city`/`state`/`country`.

  Per schema.org's `applicantLocationRequirements`, which models this concept: it records
  where applicants may **apply from**, and is explicitly **not** a citizenship or work-visa
  claim.

  **Upgrading:** anything reading `remote_region` must move to `remote_areas`. `"US" in
row["remote_areas"]` is the replacement for the US question, and it no longer needs the
  caller to know that `US-CA` and `NORTH AMERICA` are US-inclusive.

> **About every number below.** All counts come from **one** harvest of 31,790 rows,
> composed **78.5% greenhouse · 12.5% ashby · 4.3% lever · 3.5% himalayas**; the other
> 7 sources present contributed 375 rows between them, and **8 of the 19 wired sources
> contributed nothing at all**. It was read from a downstream
> consumer's store, not from `harvest()` output. Each claim names the population it was
> measured over, because several of these counts move by 2× depending on whether the
> population is "all rows" or "remote-by-location rows", and on which token list is in play.
> **Nothing here is hand-labelled**: classes like "employer boilerplate", "role-level
> assertion" and "split-week schedule" are pattern-matched, so read every precision claim as
> an upper bound. Counts were re-derived with each row's adapter-supplied `remote_type`
> preserved — a replay that forces it to `None` routes 6,049 already-typed rows down a
> fallback the engine never calls for them, and overstates the deltas badly.

### Added

- **The remote gate now RECORDS what it decided.** It called `remote_posting()`, took a
  bare `True`, and wrote nothing — so a row admitted because its title said "Remote" came
  out with `remote_type`, `remote_basis` and `remote_region` all `None`, byte-identical in
  the record to a row nobody classified. The 0.7.0 rule that every derived field carries a
  basis was broken by the gate itself, which is why a downstream consumer invented
  `remote_basis='derived'`, a value outside `REMOTE_BASES`.

  Across all 31,790 rows, `engine.derive_remote()` now labels **7,457 remote · 4,183 hybrid
  · 2,009 onsite**, leaving 18,141 honestly unknown. By provenance: `stated` 4,805 ·
  `location` 4,525 · `text` 2,568 · `board` 1,244 · `title` 507. Only the last three are
  new work; `stated` and `board` come from the adapters and are never overwritten.
  `remote_posting()` is unchanged — it is released public API — so this is a typed sibling,
  not a rewrite.

- **`title` joins `REMOTE_BASES`.** Over all 31,790 rows, 462 say remote in the title with
  the location silent, and **507** end up recorded with `basis="title"` once title-only
  hybrid and negation cases are counted too. `location` would have been a lie about where we
  read it and `stated` implies a vendor field, so neither could stand in.

  Correcting the reasoning first given for this: it is _not_ true that Greenhouse and Lever
  both send no remote field. **Lever does** — the adapter reads `workplaceType`
  (`sources.py:297`) — and in this harvest all 39 lever rows already carry `stated`, so none
  of them reaches the title fallback. Greenhouse is the source that genuinely has no remote
  field, and it is where this basis earns its keep.

- **The boundary is now recorded for every source, and `scope_filter` selects on it.**
  It was being parsed and thrown away: the field existed and only Adzuna ever set it.
  **5,499 of 7,384 remote rows now carry a boundary.** The filter reads both new fields —
  unset admits 6,281 rows, `[US, ANY]` 4,001, `[US, ANY, UNSTATED]` 6,257.

  Worth knowing before setting it strictly: a row whose source _stated_ it is remote usually
  states no boundary, so it is `UNSTATED` rather than `US`. That is why the strict list is
  smaller — it drops most vendor-stated remote rows, not just the foreign ones.

  **`None` means UNSTATED and is deliberately not "anywhere".** Only 30 of 7,712
  remote-by-location rows actually say anywhere; a bare `Remote` means "remote, boundary
  unstated", usually within whatever country the employer can legally pay from. Admitting
  those is an explicit opt-in rather than an assumption baked into the parser.

### Fixed — `city` no longer holds a list of countries

- **A location that enumerates eligible countries put that enumeration in `city`.**
  `rpartition` leaves everything before the last comma in the head slot, and the head became
  the city unconditionally — so `Australia, Canada, Germany, United Kingdom (Remote)` was
  filed as a city of that name. **99 locations** in a 31,790-row harvest. They are now `None`,
  which is what `split_place`'s own docstring has always promised: _"A None here costs a
  filter; a wrong city is a permanently wrong row."_

  The test is **two or more distinct country names**, and the precision matters. Refusing any
  head containing a comma — the obvious fix — would also null `Austin, Texas` and
  `New York, New York`, ordinary city+state heads — **7,785 locations have a comma inside the
  head**, against the 99 that are genuinely country lists. That would trade 99 wrong values
  for thousands of right ones, and only measuring the two populations separately showed it.

  Heads carrying a city+state pair are untouched and still imperfect: `Austin, Texas` remains
  in `city` rather than being split across `city` and `state`. That is pre-existing, larger
  than this release, and now written down rather than assumed.

### Fixed — two bugs live since 0.6

- **Himalayas' country list was joined into a string, which is unrecoverable.** ISO country
  names contain commas — `Congo, The Democratic Republic of the` and `Micronesia, Federated
States of` are both real and both appear in this feed — so re-splitting the joined form
  yields fragments like `Federated States of` as if they were countries. The array now
  passes through intact; the join survives only as `remote_scope_raw`, which is display and
  which nothing re-splits.

- **An empty restriction list was recorded as "unknown".** Himalayas documents that an empty
  `locationRestrictions` array means _open worldwide with no geographic restrictions_, and
  `catalog/himalayas.md` had carried that rule since the profile was written — the adapter's
  `or None` overrode it on **29 rows**, discarding the most permissive rows in the feed. The
  inverse of the error this package's contract exists to prevent.

  A non-empty list whose members do not resolve is **unstated**, never `[]`. Asserting
  "worldwide" because a lookup failed is the worst available direction to be wrong.

### Added

- **`job_radar.iso3166`** — a generated ISO 3166-1 name → alpha-2 table (425 names, from
  pycountry at build time, committed as literal data; **no new runtime dependency**). Kept
  deliberately separate from `vocab._COUNTRY_CODES`, which is 62 names and feeds _prose_
  matching — putting a full ISO table there would match `Georgia` (a US state), `Jordan`,
  `Chad` and `Turkey` against ordinary location strings, and a test pins that no country name
  in that map is also a US state name.

  Vendors send **common** names and ISO ships **official** ones, so a raw ISO list mapped only
  210 of 231 real tokens; pycountry's common-name index takes it to 222, and a documented
  alias layer covers the rest (`usa`, `uk`, `england`, plus `turkey` — ISO renamed it Türkiye
  in 2022 — `palestine`, and `kosovo`).

  **One contract caveat, stated because a strict validator will trip on it:** Kosovo has no
  ISO 3166-1 code, and `remote_areas` emits **`XK`** for it — the user-assigned code the EU
  and most payment systems use, not an ISO one. Recorded so the value is legible rather than
  dropped; reject it on sight if you validate strictly against the ISO register.

### Fixed (found by sampling the rows this change moves)

- **Company mission copy was being read as a remote policy.** `_ROLE_REMOTE_RE` matched a
  bare `from anywhere in`, which is marketing prose far more often than a work arrangement:
  _"reimagine the way people come together, from anywhere in the world"_ (105 rows, one
  employer, every one located San Mateo CA) and _"work together in real time from anywhere
  in the world"_ (47 rows, another). **219 rows were admitted on that phrase alone and at
  least 152 were copy of this shape.** They emitted `basis="text"` — identical to a genuine
  role-level assertion — so a consumer discounting `text` had to lose the real ones to shed
  these. That is the one case where this design's "every row carries a basis you can
  discount" defence actually failed. Only `work from anywhere` now matches.

- **`remote_regions` lost US jobs, which is the key's entire purpose.** `"Atlanta, GA -
Remote"` scopes to a state, and nothing knew a state is inside the country, so
  `[US, ANY]` dropped it — **72 state-scoped US remote rows** vanished from a US-only
  search.

- **`remote_region` could not tell California from Canada.** Seven codes are both a US
  state and an ISO country — `AR CA CO DE ID IL IN` — so `"Los Angeles, CA - Remote"` and
  `"Remote - Canada"` produced the same value. State scopes are now **ISO 3166-2**
  (`US-CA`), which resolves the ambiguity and is what makes the fix above possible.

- **`"South America"` was read as the United States.** `US_LOCATION_RE` carried a bare
  `america`, and since the non-US exclusion uses that same pattern as its US veto, a
  posting naming Argentina, Chile and South America survived a US-only search. 44 rows
  carry one of these; they are now scoped as the regions they are.

- **A US town named after a country was dropped.** The exclusion matches country names
  against raw text, and Turkey TX, Peru IN, Greece NY, China ME, Italy TX and Egypt TX are
  real US places. `_coerce` had already read `"Turkey, TX"` correctly as `country=US`, and
  the gate then discarded the row on the word "turkey". The row's own parsed country now
  wins — this package's standing rule applied to the last gate ignoring it. 6 rows here,
  but this corpus is 78% big-city tech; a small-town US harvest is where it bites.

- **A typo'd `remote_regions` value silently emptied the board.** `[USA, ANY]` is
  well-formed, so shape repair could not catch it, and `USA` matches no boundary this
  package emits — every remote row filtered away with no error. Unrecognised values now
  warn against `vocab.REMOTE_REGION_TOKENS` (the closed region set) and
  `vocab.REMOTE_AREA_RE` (the ISO format check) -- there is no single `KNOWN_REMOTE_SCOPES`
  symbol; an earlier draft of these notes named one that does not exist.

### Changed

- **A `remote_only` harvest returns a different set of rows. Read this before upgrading.**
  The gate now reads the arrangement the engine derived, and a posting that states a split
  week is `hybrid`, which is not remote. Measured over all 31,790 rows with the location
  filter held constant: **629 rows that used to pass no longer do, and 126 now pass that
  did not** — a net −503, or **−5.8%** of the 8,688 a remote-only run surfaced before.

  Reproduce it exactly: run `scoring.is_remote` on both sides with `exclude_locations=[]`,
  reconstructing each row's pre-derivation state as "the adapter supplied `remote_type` iff
  `remote_basis in ('stated','board')`". That reconstruction reproduces the corpus's own
  `remote` column on 31,790 of 31,790 rows, which is the control the figure rests on.
  **The gate falls back to `remote_posting` when the derived type is unknown** — a reading
  that omits that fallback reports roughly 1,430 lost instead of 629, and describes a gate
  this package does not have.

  The 629 are postings whose body states a split week ("2 days a week in the office") while
  nothing in the title or location said remote; the old gate saw the word "remote" in that
  same sentence and admitted them. The 126 are postings whose title or location says remote
  where nothing previously typed them at all. Both directions are the intended effect and
  neither is a silent reweighting — every row now carries a `remote_basis` saying how it
  was decided, so a consumer that disagrees can filter on the basis rather than re-derive.

  If you want the old recall back, `remote_only: false` plus your own filter on
  `remote_type` reproduces it exactly and leaves the labels intact.

- **A harvest costs more CPU per posting: about 2.1× on the per-row path.** Typing the
  arrangement means reading the job body, which the old bare-bool gate mostly did not. Two
  things keep it from being much worse: `derive_remote` runs _after_ the relevance gate, so
  the ~69% of postings discarded on a title test never have their body scanned at all, and
  the body patterns sit behind cheap literal gates. Measured over 31,790 real postings the
  per-row path goes from 1.69s to 3.50s; it was 6.25× before those two changes. This is
  CPU on a run whose wall-clock is dominated by network, so it is unlikely to be visible —
  but it is real, it is single-threaded, and you should not discover it from a profiler.

- **Removed a latent import cycle: `config → vocab → dedup → config`.** `vocab` imports
  `dedup` for its title vocabularies and `dedup` imported `config` at module level, which
  worked only while nothing read an attribute during import — so it was fine until
  `DEFAULT_NON_US` began deriving from `vocab` and broke outright whenever `vocab` was
  imported first. Fixed at the root rather than sequenced around: `dedup` now defers its
  `config` import into `fuzzy_title_match`, its single call site. Every module is now
  importable first. Caught by importing them in each order, not by reasoning about it.

### Fixed

- **A posting stating a split week was reported remote.** `_REMOTE_RE` matched the title or
  location and returned before anything else could be read, so hybrid could not win even
  when the posting said so plainly. Two measured cases: 41 rows read
  `"Palo Alto - Hybrid (Remote)"`, and 346 of 2,887 `"City (Remote)"` rows state a split
  week in the body — the location tag contradicted by the posting's own text on roughly 1
  row in 8. A body claim now overrides a `(Remote)` suffix **only** when the location names
  no real boundary, so `Remote - Brazil` is not demoted by the word hybrid appearing
  somewhere in a long description.

  `hybrid` is matched with different sensitivity per field, which is not a compromise but
  the point: bare in a title or location, where the word can only mean the work
  arrangement, and requiring a work-context noun in a body, where it usually does not. A
  bare `\bhybrid\b` matched **5,390** of 31,790 bodies and the third-largest group was
  **"hybrid vehicles"** — automotive job descriptions — with "hybrid environment" (as often
  hybrid _cloud_ as hybrid work) close behind. Tightening took it to **2,995**, of which
  **zero** contain "hybrid vehicle". Stated precisely, because the looser claim was wrong:
  **101 of the 2,995 still contain "hybrid cloud" or "hybrid environment"** somewhere in the
  body — the pattern matches on a _different_ span in those rows, so the verdict is
  defensible, but "zero vehicle/cloud matches" was not what was measured. The head case
  regressed when both patterns shared one tightened form, and a test caught it, not a
  reading.

- **The non-US location filter matched bare substrings, so it dropped US jobs.** `"india"`
  matched india**na** and `"apac"` matched c**apac**ity — over all 31,790 rows, matching
  title+location against the old 34-name list, **188 rows** were excluded by collision,
  including `"Anderson, Indiana, United States"` and a Capacity Planning role in Austin.
  (The population matters and is why an earlier draft said 202: matching location-only gives
  129, and the new 66-token list gives 165.) Matching is now word-bounded. The boundary is
  `(?<![a-z])…(?![a-z])` rather than `\b`, because real tokens are not all word-shaped:
  `"(eu)"` shipped in the example config and `\b(eu)\b` does not mean what it appears to.

- **A posting listing several eligible countries was dropped on the foreign one.**
  `"Remote (United States | Canada)"`, `"Americas (USA or Canada)"` and `"Remote - US &
Canada"` were all discarded because `canada` matched — losing jobs that are workable from
  the US. Over all 31,790 rows, **451** that the old list dropped are kept by the new
  filter — that figure combines this veto with the word-boundary fix above, which is why an
  earlier draft's 339 (the veto alone, on a narrower population) was not reproducible.
  A US marker in the location now vetoes the
  exclusion; the veto reads the location only, never the title, because titles carry "US"
  incidentally ("US client") and would rescue genuinely foreign rows. This long predates
  the change below — `canada` was in the old hand-written list — and only became visible
  when the filter was measured instead of read.

- **The default non-US filter was 34 hand-written names against a 58-name vocabulary.**
  The names it lacked leaked **260** remote rows bounded to countries the user cannot work
  in — led by the Philippines (68), the UK spellings (44), South Africa, Pakistan, Thailand,
  China, Chile and South Korea. Population: rows whose location matches the remote pattern,
  name a non-US country, and carry no US marker. **The leak measures 260 → 2.** It now
  derives from `vocab._COUNTRY_CODES`, the same fix and the same reasoning as
  `_KNOWN_COUNTRIES`: adding a country to the map is sufficient, and there is no second list
  to go stale. A test pins the coverage so it cannot drift again.

  One token was traded away rather than gained: `czech` was in the hand list and is not a
  key in the country map, which carries `czechia` and `czech republic` instead. Both real
  spellings are still caught and all 21 rows naming it are excluded by another token, so
  there is no live regression — but the change was not purely additive, and saying so
  matters more than the clean story.

- **`country_code("UK")` returned `"UK"`.** That is not a valid ISO alpha-2 code — `GB` is
  — and the name map has said `uk → GB` all along, but the unvalidated two-letter
  passthrough answered first. So one column the record contract declares alpha-2 held both
  spellings for the same country, ~197 rows in the measured harvest. The map is now
  consulted before the passthrough; a genuinely unlisted code still rides through, which is
  the behaviour that passthrough exists for. Same class of bug as the
  `state='California'` vs `'CA'` split `engine._coerce` already canonicalizes.

- **`bulgaria` and `dominican republic` were missing from the country vocabulary.**
  Bulgaria was in the old hand-written filter but not the map, so `country_code("Bulgaria")`
  returned `None`; deriving the filter from the map surfaced it by letting 23 Bulgarian rows
  through. Santo Domingo leaked 18 the same way. The map is deliberately incomplete and
  grows on evidence — note that `georgia` must never be added, since it is a US state as
  well as a country and `split_place`'s bare-name lookup is guarded on that collision.

- **The shipped example config downgraded the filter it was meant to demonstrate.** It set
  `exclude_locations` to six names, and a YAML list _replaces_ the default rather than
  extending it, so `job-radar init` handed every new user a weaker filter than the built-in
  one. The key is now commented out with an explanation, and a test fails if it is ever set
  to a strict subset again.

- **`split_place()` read a work arrangement in the city slot as a city.** All three
  resolving branches assigned `city` from the head of the string without ever testing
  that the head named a place, so `Remote, France` came back as the city of Remote —
  along with `Anywhere, Canada`, `Hybrid, Germany`, `WFH, India`, `Remote, TX` and
  `Remote, ON, CA`. That is the permanently-wrong row this function's own docstring
  refuses to create, and it is why a two-letter-tail rule was rejected rather than
  shipped: accepting `, US` would have written a city named Remote onto ~295 measured
  rows. Only the invented city is dropped — the country and state the string really
  carries survive, so `Remote, TX` is a Texas row with no city.

  The vocabulary derives from `dedup._QUAL_NOISE` rather than restating its words, so
  the shared set has one source instead of a fourth copy to drift. Matches are
  undecorated only: `Remote - US` still keeps its city, a bounded residual rather than
  a rule widened without measurement, because a pattern loose enough to catch it also
  nulls the real city in `Hybrid - Austin`.

- **`split_place()` discarded countries it already knew how to resolve.** A location
  string with no comma returned before the function ever asked whether the whole
  string was a country name, so `country_code("Singapore")` gave `SG` while
  `split_place("Singapore")` gave nothing. Measured by a downstream consumer over a
  31,790-row harvest: 1,591 of the 14,616 rows with a blank country are exactly this
  shape, and **539 of them are the literal string `United States`** — so the guard was
  discarding US identification as well as foreign. The lookup is gated on the country
  NAME map rather than `country_code()`, whose two-letter passthrough would turn a bare
  `CA` or `ON` into a country, and it fills only `country`: a single token names no
  city, so `London` and `Kuala Lumpur` still resolve to nothing rather than a guess.

  It also excludes US state names, guarding an invariant nothing else enforced — no key
  in `_COUNTRY_CODES` is also a US state name. That holds today, but the map is
  hand-curated and missing Georgia, Jordan and Chad, so adding `"georgia": "GE"` for an
  unrelated source would otherwise have made the bare US state a foreign country. A
  test now pins the two maps disjoint, so that collision fails CI instead of shipping.

- **`remote_posting()` documented a promise it does not keep.** Both its docstring and
  the comment above `_REMOTE_BODY_RE` claimed the body must hit a **role**-remoteness
  phrase. It does not: employer policy (`remote-first`, `remote-eligible`) and hybrid
  schedules (`3 days of remote work each week`) both pass, and of 6,070 rows derived
  remote from the body alone in that same harvest, 1,874 name a real place with no
  remote wording — 512 from company-level boilerplate, 614 from split-week schedules.
  Comments only; the regex is unchanged, because tightening it has a real
  false-negative cost that has to be measured over a full harvest first. The note
  records the blast radius meanwhile: `is_remote()` passes `text` into the predicate and
  `remote_only` defaults to true, so these reach shortlists, not just consumer tags.

## [0.7.0] - 2026-08-09

### Added

- **The catalog's gates now actually gate, and now run in CI.**
  `catalog/_scaffold.py --check` reported problems and returned 0 unconditionally — a
  planted TODO, a planted YAML break and a deleted `license.read_at` (which the schema
  marks REQUIRED) were all printed with a passing exit code. It now checks the
  required keys and exits nonzero. `_crosscheck.py` could only compare rows present in
  both files, so a profile whose INDEX row was deleted printed "no drift"; it now
  fails structurally. Deliberately one-directional — INDEX.md carries other tables of
  candidate sources whose first cell is prose, and a gate that cries wolf gets
  ignored. Neither script ran in CI at all; both do now.

- **Source attribution, which five wired sources require as a condition of access.**
  Nothing in the package provided any of it. Remote OK and Remotive both state plainly
  that they will **revoke** API access if their name is not shown as the source;
  Himalayas, Arbeitnow and Adzuna each attach their own stated condition. This was
  found by re-reading `catalog/`, where the terms are quoted and dated — the audit
  that started as "Arbeitnow needs a link" turned up four more.

  Three surfaces, because a library cannot discharge a _display_ obligation on behalf
  of whatever displays the jobs:
  - the CLI credits the sources a run actually used (crediting one that returned
    nothing is noise, and noise is how a credit line gets ignored);
  - `--format ndjson` carries the full terms in the run manifest under `attribution`,
    keyed by the same `source` value on every row, so a consumer can join without a
    lookup table of its own and render what it owes;
  - the README states the obligation for anyone building on this.

  `row_link_suffices` is the distinction that decides what a consumer must build.
  Every one of these terms wants a link to the job's own URL, which the record already
  carries — that half is satisfied structurally. Naming the source never is, and
  Adzuna needs more than a text credit at all (a branded label and a sized logo per
  advert), so it is flagged `false` rather than reported as satisfiable by a link.

  Two constraints that are not attribution and are easy to miss: Remote OK requires
  the link back be followable, explicitly **not** `rel=nofollow`, and Remotive forbids
  submitting its jobs to third-party job sites. Both now travel in the manifest.

  A test cross-checks `job_radar/attribution.py` against `catalog/`, so a newly wired
  source whose profile demands attribution cannot ship without it — the failure mode
  otherwise is invisible until a vendor cuts you off.

- **`sources.harvest_depth` — every depth ceiling in one named config block.** The
  nine ceilings were module-level constants in `sources.py`, each reading its own
  environment variable at IMPORT time, which had two consequences: a YAML config
  (parsed later) could not set any of them, and tuning a harvest meant knowing nine
  undocumented variable names. They now read from config at call time. The same env
  vars still supply the defaults, so nothing that worked before stopped working.

  A typo'd key warns to stderr and is ignored rather than raising — matching how this
  loader treats every other bad input, since a malformed config must not crash the
  CLI. What it must not do is stay silent: an ignored ceiling reads as "that setting
  had no effect", which is indistinguishable from a quiet job market.

- **A SerpApi quota guard, because `google_jobs` is the one metered source.** It
  spends `pages × title_queries` searches per run — six at the shipped defaults, or
  180 of a 250/month free tier at daily cadence (72%). One more title query or one
  more page overruns it mid-month, and SerpApi reports exhaustion as a JSON `error`
  rather than an HTTP failure, so the adapter degraded into a printed notice while the
  shortlist quietly shrank.

  The remaining quota is now checked before anything is spent, against SerpApi's
  `/account` endpoint — which is **free**, verified live: usage stayed put across
  calls, which is what makes checking every run affordable. `reserve` (default 25) is
  held back so an overrun cannot consume the end of the month, and
  `max_searches_per_run` (default 12) bounds a single run regardless of what the plan
  reports. A failed quota check falls back to that cap rather than to zero — a network
  blip says nothing about the quota, and treating it as empty would disable the
  adapter. Every reduction is announced; nothing is trimmed silently.

- **A structured record contract — ten new keys, every unknown `None`.** `category`
  (the job family), `team` (the company's own team), `parent_company` (the umbrella
  organisation), `city` / `state` / `country`, `remote` + `remote_basis`,
  `tags`, and `seniority`. Sources that already sent this data were discarding it:
  SmartRecruiters alone carries a real job function, org unit, seniority string,
  structured geography and a remote **boolean** on every posting, and the adapter
  used none of it.

  Two rules make this a contract rather than a rename. **`None` is not `False` and
  not `""`** — it means the source did not say, so a consumer can write
  `WHERE remote IS NOT NULL` instead of inheriting a guess. And **every derived value
  carries its basis**: `remote_basis` records whether remoteness came from a source
  field, a location rule, or the description, so a consumer that disagrees can
  override it rather than re-deriving everything.

- **`--format ndjson`** — the machine-facing output. One JSON object per line to
  stdout, the run manifest and progress to stderr, so `job-radar --format ndjson
--all > jobs.ndjson` produces a clean file. CSV cannot represent a list, a boolean,
  or the difference between "unknown" and "empty", which is exactly what the contract
  above turns on.

- **A run manifest**, one object per harvest: row counts per source, which adapters
  failed, companies discovered, and the filter config that produced the run. A store
  fed only rows cannot answer "why did Tuesday have four hundred fewer jobs".

- **Three new adapters, each rights-checked before a line was written** (`catalog/`):
  - **Rippling** (depth, keyless). List endpoint returns five fields and no body or
    date; `RIPPLING_FETCH_DETAILS=1` (the default) fetches one detail call per role to
    fill `text`, `posted`, `employment_type` and the full multi-location list — the same
    trade Workday makes, and for the same reason. `live_rippling` answers liveness from
    the list alone: **1 request instead of 749** on Rippling's own 748-role board.
  - **Teamtailor** (depth, keyless). One request; the JSON Feed carries body and date,
    and its `_jobposting` schema.org block supplies location and employment type.
    Deliberately has **no** `live_*` variant — the feed is a single document, so a
    liveness call and a full fetch are the same request, and `liveness_for()` falls back
    to counting a full fetch (as it already does for Ashby).
  - **The Muse** (breadth, keyless). The least tech-skewed source in the catalog — 11%
    tech titles measured — carried for the non-tech coverage nothing else provides. It
    has **no title search**, verified across nine parameter names, so `queries` is
    accepted for signature parity and never reaches the URL. Bounded by
    `THEMUSE_MAX_PAGES` (default 5) and hard-stopped at the vendor's page-99 cap.

### Changed

- **`employment_type` now carries the closed-vocabulary value, not the vendor's
  string.** `"Full-time"`, `"FULL_TIME"` and `"full time"` all arrive as `FULL_TIME`;
  the vendor's exact words move to **`employment_type_raw`**. This changes the value
  semantics of a field that shipped in 0.6.0, and jobfitr reads these rows — a
  consumer matching on the old strings must read `employment_type_raw` instead, or
  match the new closed set in `vocab.EMPLOYMENT_TYPES`. Flagged here because a pin of
  `>=0.6,<0.7` makes it load-bearing.

- **`remote_basis` gained a fourth value, `"board"`.** Six adapters were emitting
  `"stated"` for a fact no row ever asserted: remotive, jobicy, remoteok, himalayas
  and braintrust are remote-only sites, so every posting is remote because of what the
  BOARD is, and usajobs was conditioned on our own query parameter. The values were
  right; the provenance label was not, and collapsing "the vendor's field said so"
  into "the board is remote-only" destroys the distinction the field exists to keep. A
  consumer tightening a remote filter can now discount a board-scope inference without
  discarding a vendor's explicit flag. The full closed set is
  `stated | board | location | text | None`, pinned by a test.

- **Workday and Rippling no longer buy job descriptions they are about to discard.**
  Both fetch one detail request per role for the body, and the relevance gate ran
  afterwards — so a harvest paid for the full description of every role it rejected on
  the title alone. The gate now runs first, inside the adapter, against titles the list
  endpoint already returned. Measured across the ten shipped Workday employers:

  |                                  | requests |          roles |
  | -------------------------------- | -------: | -------------: |
  | before (cap 200, bodies for all) |    1,663 | 1,583 of 6,922 |
  | after (bodies after the gate)    |  **903** |      **6,922** |

  Every role, for roughly half the requests the truncated version cost. `keep=None`
  preserves the old behaviour for a direct caller. Because of it, `WORKDAY_MAX_PAGES`
  rises from 10 to 25 (200 → 500 roles/employer): the cap was standing in for a request
  budget, and the gate is now what bounds the cost.

### Deprecated

- **`department`.** It carried four different things depending on the source — an org
  unit on Greenhouse and Ashby, a job function on Adzuna, a seniority level on
  Braintrust, and the **employer** on USAJOBS — so a consumer pouring it into one
  column got a category dimension it could not filter on. Still emitted
  byte-identically, and a test pins that. Use `category` / `team` /
  `parent_company` / `seniority`. Removed at 1.0.

### Removed

- **The TechTree adapter (`search_techtree`)**, and its entry in `BREADTH_ALL`, the shipped
  example config, the live canary's whole-board list, and its parser test. Removed for two
  measured reasons rather than a judgement call: the feed carries **personal data** — every
  row's `delivery_owner` names an individual — and **60 of 76 postings are anonymised**, with
  `company_name` reading "TechTree's client", which collapses unrelated employers in any
  dedup keyed on company. It was also the stalest breadth source measured (45-day median,
  74% inside 100 days) and not a remote board (24 of 76 remote). Its terms additionally read
  as prohibiting this use; that reading is contested and is recorded in full, both sides, in
  `catalog/techtree.md`. Breadth stays at **8** keyless adapters — TechTree out, The Muse
  in, in the same release; the record shape,
  scoring, dedup and every other adapter are untouched.

- **The repo-root copies of `job-radar.example.yaml` and `watchlist.example.json`.**
  Each example file existed twice — once at the root, once under `job_radar/data/` —
  and only the packaged copy is what the wheel ships and `job-radar init` writes. The
  two had already drifted once (a 2026-07-18 fix corrected the packaged watchlist and
  left the root one pointing at five boards that now 404), and the guard against that
  was a test pinning them byte-equal. Deleting the second copy removes the failure
  mode instead of policing it. **Nothing a user receives changed**: `init` read the
  packaged copy before and reads it now.
- **`prompts/build-config-with-ai.md`**, the paste-into-an-AI config interview, and
  the README paragraph advertising it. It carried a third copy of the adapter lists,
  which drifted 19 days behind and handed people a config with `workday`,
  `google_jobs` and `usajobs` silently switched off — the same bug 0.5.0 fixed in the
  shipped example. A doc that generates config is config, and this one had no reason
  to be a separate copy of it.
- `_resolve_config` no longer probes `./job-radar.example.yaml` as a fallback
  candidate. It existed for running from a clone with the root copy present; with
  that copy gone it could only match a file the user placed there. The generic
  defaults it falls through to are the same configuration the example encodes.

### Fixed

- **A three-part location now reads its last field as a country, not a state.**
  `split_place` took the token after the final comma and called it a US state if it
  looked like one, which is right for `"Waco, TX"` and wrong for `"Toronto, ON, CA"` —
  there the region slot is already occupied by `ON`, so `CA` is Canada. Seven codes are
  both a US state and a country (`AR CA CO DE ID IL IN`), so the rule needs structural
  evidence rather than a guess about two letters.

  Measured by re-running the old and new function over 21,495 captured location strings:
  **294 rows stop being reported as American** (`Toronto, ON, CA`), and **469 gain a
  country they previously had none of** (`Chicago, IL, US`, `Curitiba, PR, br` — the old
  path returned nothing whenever the trailing country code was not also a US state).
  Zero rows changed in any other way.

  Gated on the country names this module already knows, never on `country_code()`, which
  passes any two letters through — otherwise `"…, Seattle, WA"` invents the country
  Washington. And restricted to single-place strings: the first cut of this rule guarded
  only the comma case and would have turned 519 multi-location rows
  (`"New York, NY; San Francisco, CA"`) Canadian to fix 25. That regression was caught by
  the differential re-run, not by review.

- **One column, three vocabularies — `country` is now alpha-2 or `None`, enforced at
  the boundary.** A live probe across all nineteen sources (2,747 rows) found UPPER
  alpha-2 from most adapters, **lowercase** from SmartRecruiters (`de`, `us` — 79 of
  100 rows) and **display names** from Workable (`United States`, 28/28 — while that
  same record's own `locations[0].country` said `US`). Grouping by country split every
  country into pieces. Normalized in `engine._coerce` rather than per adapter, so no
  future adapter can reintroduce a second vocabulary; an unrecognised name becomes
  `None` rather than guessing, and the vendor's text is never lost (`location` and
  `locations[].raw` keep it).

- **A US state was sometimes a name and sometimes a code, in the same column.**
  `state` is deliberately two vocabularies — a two-letter code inside the US, the
  source's own subdivision name outside it, because `Greater London` and `Attica` have
  no code to map to. The US half of that rule was false on **580 measured rows**: Ashby
  sent `California` 518 times, Workable `New York`, Adzuna `Michigan`, so
  `state='California'` and `state='CA'` named the same place and a US-state filter
  missed 566 of Ashby's 737 rows. Every one of the 580 mapped cleanly through
  `vocab.us_state_code`, which usajobs already used for exactly this. Canonicalized at
  the boundary; it may only canonicalize, never discard, so an unrecognised US
  subdivision (a county, a metro) survives untouched, as does every non-US row.

- **`employment_type` was `""` on 24% of rows.** 680 of 2,747 — 100% of Greenhouse,
  RemoteOK and Teamtailor — and `""` is not a member of `vocab.EMPLOYMENT_TYPES`. It
  is the exact None-vs-empty lie the contract exists to remove, and it was invisible
  from NDJSON because `emit` masks it with `or None`: only the flat dict a **library**
  consumer receives carried it, which is precisely who the contract is for.

- **`locations[]` had two different element shapes.** 644 of 3,153 elements were
  `{raw, url}` with no `city`/`state`/`country` key at all, so a consumer doing
  `l["city"]` raised on a fifth of the list. Every element now carries the same five
  keys, each place parsed from its own string rather than copied from the first.

- **Two depth sources discarded geography they already had.** Greenhouse filled
  `city`/`state`/`country` on **0 of 396** live rows while the location strings it
  emits parse on 358 of them; Rippling 0 of 193 where all 193 parse. One fallback in
  `_coerce` fixes every adapter at once. It may only ADD — a source that sends real
  structured geography always wins — and an unreadable location stays `None`.

- **`salary_basis: "parsed"` was outside its own closed set.** `vocab.google_salary`
  and `SALARY_BASES` had been renamed apart. Caught only by checking the values the
  FUNCTIONS produce; a test that greps source literals missed it, because the value
  arrives as a keyword-argument default.

- **A caller-supplied `cfg` did not govern the run.** `engine.harvest(cfg=...)` never
  installed it, while `sources._depth()` (every `harvest_depth` ceiling), the SerpApi
  quota guard and the adzuna/usajobs adapters all read the process-global
  `config.active()`. A library consumer that built a Config and passed it in got the
  GLOBAL settings silently — the config looked applied and was inert, which is worse
  than no config at all. jobfitr had reverse-engineered the workaround (calling
  `set_active` itself) with nothing in the docs saying it was required. `harvest` now
  installs the cfg for its duration and restores the previous one in a `finally`.
  Stated plainly because it is a real constraint: two concurrent `harvest()` calls
  with DIFFERENT configs in one process will interfere, and a caller doing that needs
  its own lock.

- **USAJOBS ignored the row's own remote field and read our query parameter instead.**
  `remote_basis` was `"stated"` because WE had appended `&RemoteIndicator=True` to the
  request, so every row claimed a vendor statement about remoteness. The real per-row
  fields were there the whole time (probed n=25: `UserArea.Details.RemoteIndicator` on
  25/25, `TeleworkEligible` true on 13), and are now read — including hybrid, which no
  amount of reading our own query could ever have produced.

- **The remote-vs-place predicate drifted apart again.** `search_usajobs` compared
  `cfg.location.lower() != "remote"` — the exact literal-string bug adzuna's own
  comment names as one it already fixed, and which CLAUDE.md lists as an invariant
  learned expensively. With `location` set to `anywhere`, `any`, `""` or `" remote "`
  it built `&LocationName=%20remote%20` with no `RemoteIndicator`, so the remote
  filter silently never reached the API. Now uses the shared `_is_remote_query`.

- **`employment_type_raw` could hold a value the vendor never sent.** It is documented
  as "what the vendor actually said", and the back-fill wrote the NORMALIZED value
  into it whenever the raw was absent. USAJOBS is the real case: it maps
  `PositionSchedule[].Code == "1"` to `FULL_TIME` itself and `.Name` is empty on 47 of
  50 measured rows, so rows claimed a quotation that never existed.

- **Two hot paths, 2× on a realistic corpus, byte-identical output.** `util.has` was a
  lookaround regex scanning a ~4.8 KB blob once per keyword — measured at 46% of
  whole-corpus consume time — and is now a `str.find` loop with identical semantics
  (verified against the old regex on 60,000 randomized cases, zero mismatches; scoring
  measured 1.98× with identical scores and signals). And `dedup.different_openings`
  re-parsed both URLs with `job_ref` on every pairwise comparison, when the candidate's
  ref was computed one line earlier and each hit's on insert; stashing it measured
  **11.4× at n=1600** with identical hit-key sets at every size. Recorded so nobody
  re-derives it: an `lru_cache` on `job_ref` is the _wrong_ fix and measured slower —
  it hashes tens of thousands of distinct URL keys to serve blocks averaging ~10.

- **The self-expanding watchlist was blind to three of its eight ATSs.** `funnel`
  identified a candidate board with `ats_from_url`, which returns `(ats, slug)` and
  therefore returns `None` for Workday (a board needs tenant + host shard + site),
  Rippling and Teamtailor. Every candidate on those three was skipped — including the
  direct Workday apply links `google_jobs` already returns — so the three ATSs with
  the deepest enterprise coverage could never grow the watchlist.

  `dedup.board_entry(url)` now returns the full entry an adapter needs, and
  `funnel._probe` passes the extra fields through. That last part is not cosmetic:
  `live_workday` **defaults** `host="wd1", site=""`, so probing with a bare slug does
  not raise — it builds a wrong URL, 404s, and the board is discarded as dead. A
  silently wrong probe is exactly the failure a probe exists to prevent.

  `dedup.entry_key(entry)` replaces the four hand-built `(ats, slug)` tuples in
  `engine`, `funnel` and `seed`. One Workday tenant can run several sites, and two
  sites are two boards; the 2-tuple treated them as one and kept only the first.

  `job_ref` also learned Rippling and Teamtailor, so the "two different ids on one
  board are two openings" veto now covers all eight depth adapters instead of five.
  Verified on Rippling's live board: the id resolves on 150/150 rows and **zero**
  distinct openings merge — the 70 rows that do collapse are the same uuid returned
  once per location, which is a genuine duplicate.

  This also removes the last duplicate URL parser: `discover` carried its own Workday
  regex and its own not-a-slug list. All three copies (dedup, discover, seed) had
  drifted before, and seed's still had the `&`-vs-`?` bug that produced slugs like
  `gemini&token=774`. There is now exactly one.

- **Workday silently merged 22% of every board into other rows.** `fetch_workday`
  read `locationsText`, which **is not in the list response at all** — `location` was
  empty on 120 of 120 rows on `accenture`, the same absent-key failure Workable had.

  The consequence was not a blank column. `dedup_key` is
  `company|title|location|job_id`, and Workday was missing _both_ of the last two —
  the location because of the absent key, the requisition id because `job_ref` did
  not recognise a Workday URL. So the key collapsed to `company|title`, and every
  same-titled role a company posts worldwide became one row. Measured on `accenture`
  (n=400): **89 rows discarded, each with its own apply URL and its own city** —
  "Contract Manager" in three cities kept one. The veto that exists to prevent
  exactly this could fire on **0 of 19,900 pairs**.

  Both values were already in the payload, at no extra request. `externalPath` is
  `/job/<Location>/<Title>_<ReqId>` on every tenant probed (accenture/wd103,
  academy/wd1, 3m/wd1); `bulletFields` carries them on some. `bulletFields` is read
  **by shape, never by position** — it holds a requisition id, sometimes a location,
  and sometimes `Posting Date: MM/DD/YYYY`, in no guaranteed order. After the fix:
  location 400/400, and rows discarded 89 → 6, the remaining six being degenerate
  rows the API returns with no title and no path, which the relevance gate drops.

  The requisition id is parsed by its own pattern rather than by teaching
  `ats_from_url` about Workday, and that restraint is load-bearing: `ats_from_url`
  returns `(ats, slug)`, but a Workday board needs slug + host + site. Routing it
  there would make `funnel._probe` call `live_workday(slug)`, whose defaults do not
  raise — it would build a wrong URL, 404, and silently discard every real Workday
  employer discovery finds.

- **Six adapters were discarding data their API already sends.** Each was measured
  against the live endpoint, not inferred:
  - **Ashby** shipped compensation as a display string only. 594 of 734 postings on
    `openai` carry a structured range; those are now `salary_min`/`max`/`currency`/
    `period`. Only the `Salary` component is read — the same list carries
    `EquityCashValue` on 576 rows, and taking the first would have written an equity
    grant into salary. The interval arrives as `"1 YEAR"`, so a period map keyed on
    `year` matched nothing and every Ashby salary would have been dropped.
  - **Lever** looked for `country` under `categories`, where it does not exist (0 of
    295 on `binance`) rather than at the top level, where it is present on all 295.
    Its body was also only the intro: `descriptionPlain` averages 1,118 characters
    while the requirements sit in `lists[]` (2,279) and the closing in
    `additionalPlain` (712). Bodies went 1,118 → 3,657 characters.
  - **USAJOBS** read `JobSummary` (305 characters) and ignored `MajorDuties` (1,692),
    `Evaluations` (1,487) and `Requirements` (322), so federal roles scored near zero
    regardless of match. Bodies went 305 → 3,810. Its geography keys also lie:
    `CityName` is `"New Orleans, Louisiana"` and `CountrySubDivisionCode` is a NAME,
    not the code it claims.
  - **The Muse** sends no structured geography at all, so city/state are now parsed
    from the `"Waco, TX"` display string: 80/80 city, 68/80 state (the other 12 are
    non-US and correctly have none).
  - **Greenhouse** `metadata[]` now reaches `source_extra`. Not a core column: the
    field names are per-board — `databricks` sends "Company Assignment", `anthropic`
    sends "Location Type", `stripe` sends none — so mapping one to `parent_company`
    would work on exactly one board and mean something else on the next. That is the
    mistake `department` already made.
  - **Rippling** `payRangeDetails` (3 of 30 sampled) is now read as a real range.

- **`country` and `state` held several vocabularies at once.** Lever sends `SG`,
  Ashby sends `Singapore`, USAJOBS sends `United States` and `Louisiana`. Grouping by
  country split every country into pieces. One normalizer now maps all of them to
  ISO alpha-2, and it returns `None` for a name it does not recognise rather than
  guessing — a wrong country enters a database once and never leaves. The location
  parser refuses `"Taiwan, Taipei"` and `"Toronto, ON"` for the same reason: several
  sources emit country-first, and a province is indistinguishable from a country code
  at two characters.

- **Workday's detail pass upgraded a date without upgrading its label.** It replaces
  a date derived from "Posted 26 Days Ago" with a real `startDate` when the detail
  call returns one, but set `posted` alone, leaving the row claiming `relative` while
  holding a date the tenant actually published. Same drift `_rippling_detail` had.

- **The machine feed advertised the 0.7.0 contract and shipped six fields of it.**
  `--format ndjson` emitted three key names — `function`, `org_unit`, `employer_org` —
  that nothing in the package has set since they were renamed to `category`, `team`
  and `parent_company`. The rename reached the contract and all nineteen adapters and
  stopped at `emit._nested`, so those keys were `null` on every row while the ones the
  adapters actually fill were never emitted at all.

  Auditing that turned up the larger half: **23 of the 29 contract fields never
  reached the wire.** Every salary field, every `*_basis`, `remote_type`, the title
  decomposition and `locations` existed on the record and stopped at the emitter. So
  did `text` — the full description, which is the entire input to the fit score — for
  a third reason: it is neither a contract field nor a store column, so the join in
  `cli._emit_ndjson` grafted neither half of it.

  Nothing failed while this was true. The feed had the right shape and was null where
  it mattered, which is the failure mode this format exists to prevent.

  `title`, `location`, `remote` and `salary` are now nested objects keeping the raw
  vendor value beside the parsed parts. A new `tests/test_emit.py` asserts in both
  directions — every key the emitter reads must be something the package produces, and
  every contract field must reach the wire — so the next rename cannot half-land.
  `emit.py` had no test coverage at all before this.

- **A date and the label saying where it came from can no longer drift apart.**
  `posted_basis` distinguishes a date the vendor published (`stated`) from one this
  tool computed by subtracting a phrase like "Posted 26 Days Ago" (`relative`) — but
  the label was hand-written at each of eighteen call sites, so a new adapter, or a
  moved line, produced a date with no basis and nothing caught it. Rippling had
  already drifted: it sets `posted` in its detail pass, which no one updated, so every
  Rippling row carried a date and no basis.

  Both values now come out of one call — `util.posted_from()` for a vendor timestamp,
  `sources.posted_from_relative()` for a recency phrase — because the basis is a
  property of _how the date was derived_, which is knowledge only the deriving code
  has. It cannot be defaulted at the record boundary either: by then a computed date
  and a real timestamp are both just strings, so defaulting to `stated` would be right
  for sixteen adapters and an invisible lie for the two that compute. A test now
  asserts the pairing across all nineteen adapters.

  The label reports provenance, not accuracy: `stated` means the vendor sent a date,
  not that it is the right one. Greenhouse's `updated_at` was a genuine ISO timestamp
  and still the wrong field.

- **SmartRecruiters returned 100 rows of every board, however large.** The API clamps
  `limit` at 100 and says nothing — `?limit=200` returns 100 rows and echoes
  `limit: 100` — and the adapter made a single call. Measured on a real board
  (`boschgroup`): **100 of 4,716 postings, 97.9% dropped**, silently, on every run.
  The module already knew the true number: `live_smartrecruiters` reads `totalFound`
  and feeds discovery's role-count sort while the fetch returned 100 — two functions
  in one file disagreeing by 46x. Now pages with `&offset=`, bounded by
  `SMARTRECRUITERS_MAX_PAGES` (default 10 = 1,000 roles/company).

- **The Muse fetched 100 rows out of ~36,060.** The unfiltered feed hard-caps at page
  99 = 2,000 rows, and the cap applies **per category slice** — so the 20-category
  fan-out is the only way past it, and it was never implemented. All 20 category
  values were probed individually before shipping — necessary because this API
  silently ignores an unrecognised parameter _value_ and serves the unfiltered feed,
  so an unverified slice would look healthy while being a copy of the others. What blocked it was a
  wrong entry in our own catalog claiming category filtering was unreliable;
  re-measured, `category=Healthcare` returns 20/20 Healthcare rows with zero overlap
  against the unfiltered page. The trap is corrected in `catalog/themuse.md`. The Muse
  also now emits `seniority` from its `levels` field.

- **Himalayas was paged on the wrong endpoint.** This source has two, with different
  pagination: `/jobs/api/search` takes `page` and walls at ~8,020 rows;
  `/jobs/api` takes `offset` and walks the whole corpus (**96,934** measured). Sending
  `offset` to the search endpoint is silently ignored and returns page 1 forever. A
  browse lane is added alongside the search lane (`q` does nothing on browse, so it
  cannot replace it). Bounded by `HIMALAYAS_BROWSE_PAGES` (default 50 = 1,000 rows).
  Browse is **date-ordered** — measured offset 0 → median age 0 days, 20,000 → 8 days,
  60,000 → 28 days — which is what makes a bounded lane worth having: those 1,000 rows
  are the newest 1,000, not an arbitrary slice. An age stop exists as a secondary
  guard, but at the default 60-day window it cannot fire inside the page cap; the cap
  is the budget. Walking the full corpus would be ~4,850 requests.

- **USAJOBS never sent `&Page=`.** One request per keyword, so anything over one page
  was truncated — `SearchResultCountAll` reports the true total and nothing read it.
  Measured in `catalog/usajobs.md`: "medical assistant" 736 and "registered nurse" 620
  against a 500-row page, i.e. 236 and 120 postings lost invisibly. Now pages, bounded
  by `sources.usajobs.max_pages` (default 3), with the politeness pause applied between
  pages as well as between queries.

- **Hacker News read one thread.** On the 1st of a month that thread is nearly empty
  and the entire prior month vanished. The Algolia search already returns four, so
  reading the two newest costs one request: measured 2026-08-04, 138 rows became 383.

- **Adzuna returned ZERO rows whenever the configured location was "remote".**
  `where` resolves against a place hierarchy, so the word was being sent as a town
  name — and zero rows is indistinguishable from "no such jobs" behind the adapter's
  error handling. Measured on `what="AI Engineer"`, US: `where=remote` → 0;
  `where=""` → 55,052 at **2%** actually remote; `what_and=remote` → 15,500 at
  **84%**. Blanking `where` was the tempting wrong fix; the remote keyword filter is
  the right one. Real places are unaffected and compose with it.

- **The Adzuna radius guard tested the wrong thing.** It checked
  `location != "remote"` on its own, so "anywhere" slipped through and a `distance`
  was sent with no place to anchor it. Both branches now share one predicate.

- **Adzuna nationwide postings are recognised as remote.** `location.area == ["US"]`
  exactly means nationwide, and the adapter kept only `display_name` — which for
  those rows is the bare string `"US"`, invisible to any text rule. `area[1]` is also
  a real US state in 246 of 246 rows sampled, and was being thrown away.

- **Google for Jobs named a remote filter and never applied it.** The adapter's own
  comment said Google treats "remote" as a filter rather than a place, then dropped
  the word and set nothing — so a remote search silently ran unfiltered and
  nationwide. It now sets SerpApi's documented `ltype=1` work-from-home filter.

- **The remote gate ignored every structured remote signal.** `is_remote` re-derived
  remoteness from prose even when the source stated it outright. A structured flag now
  wins, and `None` still falls through to the text rule — unknown is not `False`.
  Without this the mapped `remote` field would have been decorative.

- **Remotive was called four times per run, identically.** Every parameter on that
  endpoint is ignored, so `?search={query}` filtered nothing and the four calls were
  four copies of one request. Remotive's own notice advises a maximum of **four
  requests per day**; four per run is 96 on an hourly schedule. Now one unfiltered
  request, which returns the whole 31-row corpus anyway.

- **Himalayas was under-fetched by roughly 60x.** The adapter sent `limit=20` and no
  page parameter, taking 20 rows per query out of a measured 8,020 reachable. The
  trap: this source has two endpoints with different pagination models — `/jobs/api`
  takes `offset`, `/jobs/api/search` takes `page`, and sending `offset` to the search
  endpoint is silently ignored and returns page 1 forever. Now pages properly,
  bounded by `HIMALAYAS_MAX_PAGES` (default 10 = 200 rows/query).

## [0.6.0] - 2026-07-31

The release that stops the engine quietly deleting jobs you wanted, plus a
faster, lighter scan. **Scores are unchanged** — a role's number is exactly what
it was in 0.5.3.

### Fixed

- **Different openings stopped being merged into one.** A company's `AI Engineer`
  and `AI Engineer, Ads` were collapsing into a single row, as were `II` vs `III`
  and `(East)` vs `(West)` — and a merge **discards the loser's apply URL**, so
  the second role was deleted before you ever saw it. Which copy survived depended
  on the order the feeds happened to answer in, which is why this was invisible.

  The matcher decided on string similarity alone, and string similarity is
  positive evidence only — nothing in it could argue _against_ a match. The
  outcome therefore tracked suffix **length** rather than meaning: `, Payments`
  was just long enough to fall below the threshold, `, Ads` was not. Titles are
  now checked for **disqualifying marks** — a seniority/level mark, and a trailing
  qualifier like `(EU)` or `, Ads` — and a disagreement vetoes the merge before
  any similarity is consulted. Two different job ids on the same board are also an
  absolute veto: that is two openings by definition.

  **You will see more duplicate rows**, and that is the intended trade. A wrong
  merge deletes a job you wanted and hides it; a wrong split shows a redundant row
  you can ignore. The provenance tiebreak from 0.5.0 still puts the employer's own
  ATS link first, so the extra row is the redirect, not the real one.

  Honest about the limits: this is a rule-based approximation of proper record
  linkage, not the real thing — it still contains a hand-tuned threshold. And two
  postings with **byte-identical** titles on one board still merge, because
  splitting those would mean putting the job id into the store's primary key,
  which would orphan every existing row and could reattach an "applied" status to
  the wrong opening. That is a worse bug than the one it would fix.

- **A shortlist saved in the wrong encoding no longer bricks every command.**
  Excel's "Unicode Text" save writes UTF-16, and that raised a raw decoder
  traceback out of _every_ command — including `apply` and `dismiss`, so you
  couldn't reach your own file to fix it. UTF-16 now loads; anything genuinely
  unreadable gets one sentence naming the file and the fix.

### Performance

None of these change a single score, ranking, or row — only how long a scan takes
and how much memory it uses.

- **HTTP connections are reused per host.** A scan is ~500 companies concentrated
  onto a handful of ATS hosts, and every request was opening a fresh TCP + TLS
  handshake: 149ms cold against 84ms on a reused connection. The pool is
  per-thread, so connections are never shared between workers, and a socket the
  server closed while idle is transparently retried once on a fresh one.
- **The discovery funnel probes in parallel.** It was serial: ~150 dead candidates
  measured at roughly 60 seconds of requests to add zero companies. The probe
  budget added in 0.5.x already caps how many requests go out, so this is purely
  wall-clock — the load on third-party boards is unchanged.
- **Peak memory during a scan is bounded.** All ~500 companies were submitted at
  once, so every fetched job description stayed in memory whether or not it had
  been processed yet (~1.25 GB at 500 companies). Fetching now runs on a sliding
  window and results are released as they are consumed.

### Changed

- **The README's scoring claim was wrong and has been corrected.** It advertised
  "term-frequency saturation (`score_k1`)". There is none: each keyword counts at
  most once, so there is no term frequency to saturate — repeating a keyword can
  never raise a score (measured 26 / 26 / 25 / 22 at 1x / 5x / 50x / 500x, falling
  only because repetition lengthens the document). `score_k1` is real but it is a
  _gain_ on length normalization. Counting each keyword once is a deliberate
  anti-keyword-stuffing choice and is unchanged; only the description of it was
  false. This also corrects the 0.5.2 entry below, which claimed the BM25 label was
  "true for the first time" — the length-normalization half was, the
  term-frequency-saturation half never was.
- Three `title_penalty` keys were reported as unreachable dead config. They are
  not: a bare "Research Scientist" is filtered by the relevance gate before
  scoring, but "AI Research Scientist" reaches it and the penalty fires correctly.
  Kept, with a test proving it rather than a deletion.

## [0.5.3] - 2026-07-31

### Fixed

- **A scan with the LLM re-rank enabled stored nothing.** Introduced in 0.5.2 and
  fixed here. `cli.cmd_scan` passed `write=not llm_on` to `upsert` — skipping the
  write to avoid one rewrite — and the `annotate()` call added in the same release
  then re-read the file, which therefore never contained the harvested rows, and
  wrote that back. The scan evaporated while the CLI printed that it had tracked
  the roles: `apply <id>` could never find an id, `first_seen` never accumulated,
  and every role stayed "new" forever.

  It only affected runs with `llm.enabled: true` (off by default, needs an API
  key), and only 0.5.2. The harvest is now always persisted, then annotated.

  Why it shipped: `write=False` had one caller and zero tests, and there was no
  end-to-end test of `cmd_scan` with the LLM on — so the one production path
  without coverage was the one that broke. There is now a test that drives the real
  `cmd_scan`, because the defect was in how the CLI wired two correct functions
  together rather than in either of them.

- The AI-config prompt (`prompts/build-config-with-ai.md`) emitted a `sources.ats`
  list that omitted **workday** — reintroducing, through the "let AI write your
  config" path, the exact bug 0.5.0 fixed in the shipped example. It also predated
  `google_jobs` and `usajobs`. It now tells the assistant to OMIT those keys unless
  narrowing (absent means "every adapter this build ships"), and a test asserts the
  prompt mentions every registered adapter. A doc that generates config is config.

## [0.5.2] - 2026-07-31

Three ways the store could lose your work or fail without saying so. All three were
found by an independent review of 0.5.0 and classified minor; all three are
reproducible, and two of them break the promise this tool leads with — that it
remembers what you have applied to.

### Fixed

- **`apply` during a scan was silently discarded.** `upsert` and `mark_status` both
  read the whole store, change it in memory and write it back. The write is atomic,
  but atomic is not serialized: a scan that read BEFORE your `apply` and wrote after
  it put the pre-apply rows back, so the role lost its status and resurfaced. A scan
  takes about a minute, so a cron run overlapping a manual `apply` — or simply two
  terminals — is enough. Both paths now hold an exclusive `flock` across the whole
  read-modify-write.

  Locked on **both** platforms: `fcntl.flock` on POSIX, `msvcrt.locking` on
  Windows, polled to a deadline because its blocking mode gives up after ten
  seconds and a scan can run longer. The first attempt fell through to "unlocked"
  on Windows — so a Windows user would have silently kept the exact bug this
  fixes. The Windows CI cells caught it, because the test asserts the guarantee
  rather than the implementation.

  Deliberately an OS lock on a descriptor rather than a lock FILE. The existing note in
  `funnel.append_watchlist` rejects locking because "a lock file only risked getting
  stuck after a crash" — true of lock files, and not of `flock`, which the kernel
  releases when the process dies. Best-effort: a platform without `fcntl` proceeds
  unlocked rather than refusing to run.

- **The LLM path could undo concurrent changes.** It read the store, made a network
  request that can take many seconds, then wrote back the rows it had read —
  discarding anything that changed meanwhile. It now re-reads under the lock and
  grafts the scores on by `dedup_key`.

- **A CSV saved by Excel broke `apply` and `dismiss` permanently.** Excel writes a
  UTF-8 BOM; read as plain UTF-8 those bytes become part of the first header name,
  so the column is `\ufeffid` rather than `id`, every id lookup misses, and the CLI
  reports "no role with id ..." forever — on a file that looks perfect in a
  spreadsheet. The store is documented as user-editable, so this was reachable by
  doing the obvious thing. Now read as `utf-8-sig`.

- **`--config <path-that-does-not-exist>` silently loaded a different config.** It
  fell through to `./job-radar.yaml`, so a typo in the path ran happily against
  someone else's settings and reported nothing. Now exits 2 with the path named.
  Same trust rule as the auto-discovery guard in 0.5.0, applied to the path where
  the user was explicit.

## [0.5.1] - 2026-07-31

### Fixed

- **One malformed posting could kill an entire harvest.** A JSON `null` from any of
  the ~500 sources arrived as `None`, and `.get(k, "")` does not guard against that —
  its default fires only when the key is ABSENT, so a present-but-null `title` yielded
  `None` and the first `.lower()` raised `AttributeError`. The damage was
  disproportionate: `engine._consume` runs OUTSIDE both of `harvest`'s try blocks, so
  the crash escaped per-source error handling entirely, discarded a network harvest
  that had already completed, and skipped the "keep your existing shortlist" guard on
  the way out — a raw traceback and a lost run, caused by one bad row from one vendor.

  Every text field is now coerced once at `_consume`'s boundary, which is the single
  point every posting from every adapter must cross. A bad ROW is dropped; it is not
  treated as a bad SOURCE. Wrong-typed values (a list, a dict, an int) are handled by
  the same guard, since `null` and `123` fail identically downstream.

  This was found during the 0.5.0 review and deferred as non-blocking. That was the
  wrong call — it is a crash on the main path, reachable from any source, and it
  should not have shipped.

## [0.5.0] - 2026-07-31

Two things, and the smaller headline is the more important one.

**job-radar did not work on Windows.** Not "had rough edges" — `import job_radar`
raised, so every command failed on every released version, 0.2.0 through 0.4.1.
CI had only ever run on Linux, so nothing contradicted the 0.2.0 entry claiming
Windows was supported.

**And it gains the meta-aggregator.** Every existing breadth source is one
publisher's own index; Google for Jobs is Google's index OF those publishers,
plus the company career pages and enterprise ATSs (Workday, iCIMS) that no single
feed exposes — reachable by title and location, with no per-tenant polling.

### Caller-visible contract change (jobfitr)

**A deduped role's `url` can now differ from what an earlier version returned.**
When the same role arrives from several sources at an equal fit score, the merged
row keeps the higher-preference source's copy, and `google_jobs` outranks
everything else — because its `apply_options` resolve to direct-to-employer links
rather than an aggregator redirect. The fit **score** is unchanged and remains
source-agnostic: `score_and_signals` reads only a posting's content. Source
preference breaks ties, it never contributes points.

### Added

- **`sources.search_google_jobs`** — Google for Jobs through SerpApi, queried by
  title + location exactly like `search_adzuna`/`search_usajobs`. Off unless
  `SERPAPI_KEY` is set, and skipped with a one-line note when it isn't, matching
  how Adzuna already behaves. Metered on purpose: SerpApi's free tier is 250
  searches a month and each PAGE is one search, so `google_jobs_pages` defaults
  to 1 (~10 roles per query).
- **`_best_apply_link`** prefers the first non-aggregator host from Google's
  ordered `apply_options`, so a listing routes to the employer's own careers page
  or ATS instead of LinkedIn/Indeed/ZipRecruiter. Direct-to-company links are the
  product promise; an aggregator redirect is a worse version of the same role.
- **`_google_posted`** resolves Google's relative recency strings ("16 hours ago",
  "30+ days ago", "today") to an absolute Eastern date at fetch time — the same
  rot-in-the-cache trap Workday's `postedOn` has, where a stored relative string
  silently ages into a lie.
- **`sources.google_jobs.{key_env,pages}`** in the config file, with a loader, so
  the knob the example config documents is one the code actually reads.
- **`job-radar --version`.** It had none — the first thing anyone types at an
  unfamiliar CLI, and what a packaging smoke test calls to prove the entry point
  works. Top level only, so `job-radar list --version` is still an error rather than
  quietly printing a version instead of running the subcommand.

### Caller-visible: the fit score changed

**Every score moves, and merged roles may now carry a different URL.** Two fixes to
the scorer, found by an independent review after the rest of this release landed:

- **The score rewarded brevity, and it inverted the product.** `raw / norm` is the
  k1→∞ limit of BM25 — term-frequency saturation switched off — and because `norm`
  bottoms out at `1 − length_norm_b` = 0.25, a very short posting had its score
  multiplied by up to **4×**. That floor is independent of `avg_jd_tokens`, so
  raising it from 400 to 1600 (below) could not fix this and did not. The
  consequence: an 80-word aggregator stub outscored the same role's full employer
  description, and since the merge tiebreak led with score, **14 of 20 merged roles
  on a live board handed the user a RemoteOK redirect instead of the company's own
  ATS link** — the opposite of "routes you to the source." Scoring here is
  presence-based (a keyword counts once), so BM25 collapses to
  `raw·(k1+1)/(1+k1·norm)`; that is now what runs, with `score_k1` (1.2) exposed in
  the config. The README's "BM25 length-normalized" claim is true for the first time.
- **The merge tiebreak now leads with provenance, not score.** Two copies of one job
  are the same job, so "which fits better" was never the right question between
  them — only "which is the better record." `_SRC_PREF` also ranked a company's own
  Greenhouse board **equal to** a RemoteOK redirect; depth sources (the employer's
  ATS) now outrank Google-for-Jobs, which outranks aggregators. Measured across
  three live boards in both arrival orders: aggregator retention **70–88% → 0%**.

Expect an existing `shortlist.csv` to re-rank on the next run, some short aggregator
stubs to fall below `min_score` (they were clearing on inflation), and merged roles
to carry employer URLs where they carried redirects. If you tuned `min_score` against
the old inflated scale, lower it. No CSV schema change.

### Security

- **The LLM API key could be printed to stdout.** `llm.rerank` reported failures as
  `({type}: {e})`, and the exception carries the request header: a key with a
  trailing newline — a `.env` file, `$(cat key)`, CRLF on Windows — made
  `http.client` raise `ValueError: Invalid header value b'sk-ant-…'`. A scheduled
  run redirects stdout to a log file, so the key landed on disk. It now reports the
  exception **type only**, matching the eight other error paths that already did.
  `Config.env()` also strips whitespace now, removing the trigger for every
  credential at the one chokepoint they all cross.
- **Formula injection via the `posted` column.** `_csv_safe` was applied only to a
  curated `TEXT_COLS` set that omitted `posted`, which is fed from vendor data by
  `to_date` — and `to_date` returned `str(val)[:10]` for any string, so any of ~500
  boards could put a live formula in your spreadsheet. `to_date` now returns `""`
  for anything not date-shaped, and every column is sanitized. `TEXT_COLS` is
  deleted rather than corrected: curating "the untrusted columns" is a judgement
  that must be re-made on every new column, and it was already wrong once.

### Fixed

- **The depth lane was buried.** On a clean install, company ATS feeds — what this
  tool leads with — harvested 436 roles and surfaced **1**, while aggregators
  surfaced 14 from 168. Three compounding causes, all fixed:
  1. `"on behalf of"` scored −10 as an agency signal. It is generic English, and it
     appears verbatim in employers' own anti-recruitment-fraud boilerplate ("we may
     partner with vetted recruiting agencies who will identify themselves as
     working on behalf of X") — present on **20 of 20** relevant roles on one live
     board. Removed.
  2. `agency_penalty` was the only uncapped score component and the only one that
     goes negative, so a long thorough JD accrued penalty without limit while its
     body score was normalized down. Now capped by `agency_penalty_cap` (15), like
     `blob_score_cap` and `title_score_cap`.
  3. `avg_jd_tokens` was 400 — roughly a job-board _summary_. Real full ATS
     descriptions measure a median of ~1590 tokens, so every thorough posting was
     treated as abnormally long and had its score divided by ~3.2. That inverts what
     BM25 length normalization is for. Now 1600.

  Measured on one live board: 0 of 20 relevant remote roles surfaced before, 7 of 20
  after. **Read that as a smoke test, not as validation.** n=20, one board, no
  labelled ground truth, and the success metric is output volume — which is exactly
  what the tuned parameter controls. It does reproduce across five boards, and 400
  was measurably wrong, so the direction is right; but a defensible calibration needs
  a few dozen hand-labelled roles and a precision@k number, and that has not been
  done. (A reviewer's claim that simply lowering `min_score` would produce the same
  result was tested and is **false**: matching the surfaced count needs
  `min_score=9`, and the resulting sets differ at Jaccard 57%, with ~10% of ordered
  pairs re-ranking. A length-dependent transform genuinely re-orders; a threshold
  cannot.)

- **The discovery funnel spent up to a minute per scan to add nothing.**
  `funnel_max_new_per_run` caps companies ADDED, and a dead slug is never an "add" —
  so on a run where candidates were dead the cap never fired and every one of them
  got probed, serially. Measured: 150 dead candidates, ~60 s, 0 added, on every scan,
  with `auto_grow` on by default. New `funnel_max_probes_per_run` (50) bounds
  attempts. Not parallelized on purpose: probing concurrently would make the healthy
  case worse by hitting every candidate every run, which is more load on other
  people's ATS endpoints from a tool strangers install.
- **The agency penalty scored keywords found in a role's title or location.** It is
  meant to read company + description only. Introduced earlier in this same release
  when that penalty was routed through the keyword prefilter with the wrong token
  set, so a role titled "Staffing Engineer" was penalised as a staffing agency.
  Caught by the equivalence gate below, on its first honest run.
- **`search_usajobs` had no politeness delay** while five sibling sources pause
  between queries, and it requests the largest page in the codebase.
- The repo-root and packaged copies of `watchlist.example.json` had **already
  drifted**: a 2026-07-18 fix corrected the packaged copy and left the root one —
  the file a GitHub visitor reads — pointing at five boards that now 404. Synced,
  and a byte-equality test now pins both copies of both example files.
- **`tzdata` is now a dependency on Windows** (`sys_platform == 'win32'`). Windows
  ships no system time-zone database, so `zoneinfo` has nothing to read and
  `ZoneInfo("America/New_York")` raises `ZoneInfoNotFoundError`. Both `util.py` and
  `sources.py` construct that object at **module level**, so the failure landed on
  import rather than on a date calculation — the package was unusable, not merely
  wrong about times. Linux and macOS have a system tzdb and are unaffected, hence
  the environment marker instead of an unconditional dependency.
- **`job-radar init` silently disabled Workday.** The shipped starter config
  listed five of the six ATS adapters under `sources.ats`, and an explicit list is
  a SUBSET filter — so every user who ran `init` since 0.3.0 had the enterprise
  tier switched off while the README described it as the reason to use this tool
  for non-tech work. Both copies of the example config now list every adapter, and
  a test asserts the config enables everything in `DEPTH_ALL`/`BREADTH_ALL` so the
  two cannot drift apart again.
- **The README told people to install from Git.** It had said a PyPI release was
  "coming" since before 0.2.0 went up on 2026-07-19, so the headline install
  command was wrong for twelve days and five releases. It now says
  `pipx install job-radar`, and states the supported platforms.
- `CONTRIBUTING.md` counted two runtime dependencies; there are now three.
- **Jobicy roles carried a Python list in the `department` column.** Jobicy returns
  `jobIndustry` as an array, and the adapter normalized `jobType` but not this one,
  so `department` reached `shortlist.csv` as the literal text `['Engineering']`
  instead of a value you could filter on. Found by the hand-written Jobicy parser
  test built from that vendor's real response shape — which is the argument for
  building fixtures from real payloads rather than from the code.

### Changed

- **CI now runs on Linux, macOS, and Windows** across Python 3.10–3.13 (12 cells,
  was 4 on Linux only), and adds `mypy`. The Windows cells found the bug above on
  their first execution. A wheel end-to-end step also builds the artifact, installs
  it into a clean virtualenv, and runs `job-radar init` from outside the repo —
  the editable install the tests use cannot prove `package-data` shipped.
- **Releases are cut by pushing a `v*` tag**, which re-runs the full matrix on the
  released commit, refuses to publish if the tag and the packaged version disagree,
  and creates the GitHub Release from this file. The previous workflow published on
  a manually-created Release without running any tests, and had never once run.
- Dependabot now tracks GitHub Actions and pip, with the workflow actions pinned by
  commit SHA rather than a mutable tag.
- **A weekly audit** (`audit.yml`) runs four checks that catch problems appearing
  without this repo changing: a CVE published against a dependency, a rotted README
  link, an action tag repointed upstream, and the repo's OpenSSF Scorecard. A
  commit-triggered check cannot catch any of them — there is no commit to trigger
  on. `uv.lock` is committed to feed the CVE scan an exact dependency set; without
  one the scanner finds no package sources and passes having examined nothing. It
  does **not** change what `pip install job-radar` resolves, and it is not shipped
  in the wheel.
- Type annotations on the `DEPTH_ALL`/`LIVENESS` registries and `discover.known_keys`,
  whose deliberately non-uniform key shape (a 3-tuple for Workday, a 2-tuple
  otherwise) is now documented rather than implied. No behaviour change.
- The version sections in this file were out of chronological order — 0.4.1 sat
  between 0.3.2 and 0.3.1. Reordered, content untouched.
- **Adapter test coverage.** Six of the eleven breadth sources — Remotive, Jobicy,
  Arbeitnow, Himalayas, USAJOBS and TechTree — had no test at all, despite
  `CONTRIBUTING.md` requiring one for every source. All six now have parser tests
  built from their real response shapes, plus a **registry contract test** that
  iterates `DEPTH_ALL`/`BREADTH_ALL` and asserts the posting contract.
  134 tests, up from 107.
- **A weekly live canary** (`canary.yml`) asks the real APIs whether they still
  return the shape the parsers read. Fixture tests freeze a vendor's shape as of
  the day they were written and cannot detect drift; this can. It is scheduled, not
  part of CI, so a third party's outage never blocks a pull request, and it
  separates "unreachable" (skip) from "reachable but unparseable" (fail).
- **Scoring is 2.5x faster** — 961 to 388 µs per posting. The agency penalty ran 13
  whole-word regexes over the full description for every posting (68% of scoring
  CPU) while the `_present` prefilter twenty lines above solved exactly that for
  `fit_weights` and had never been extended to it. Output verified identical over
  4,000 adversarial postings.
- **The test guards added earlier in this release did not guard.** An independent
  review broke three of them, and they are rebuilt here:
  - The registry contract test stubbed the transport with `{}`; every adapter
    returns `[]` from that, so its per-key assertions iterated an empty list and a
    deliberately non-conforming adapter passed. Each adapter now has a sample
    payload of its own real response shape, and the test asserts a row was actually
    produced before judging it.
  - The example-config parity test read the repo-root copy while `init` ships the
    packaged one, so deleting `workday` from the file users receive left the suite
    green — the very bug this release fixed, reachable again.
  - The canary asserted non-blank only on `url` and `title`, so a vendor blanking
    every description kept it green while all scores went to zero; and because
    pytest exits 0 on an all-skip run, an aggregator blocking CI's IP range
    produced a green run that checked nothing. Both now fail. `search_remotive` and
    `search_himalayas` swallowed network errors internally, making outage and drift
    indistinguishable to the canary; they take `strict=True` from it now.
  - `search_google_jobs`, this release's headline feature, had **no executed
    coverage** of its parsing path. Now covered end to end.
  - **The scoring equivalence gate guarded nothing** — and this one is the reason
    the agency-penalty bug above shipped. `test_scoring_matches_bruteforce_reference`
    declares itself the gate that must fail if the scoring optimization changes
    results. Its reference still subtracted the agency penalty _uncapped_ after
    production started capping it, and it passed regardless, because its generated
    vocabulary contained not one agency keyword — so that branch was never compared
    in any of its 2,000 cases. Forced to one: production 49, reference −44. The
    reference now matches production and the corpus reaches every branch, verified by
    reverting each fix in turn and confirming the gate goes red.
  - A test now pins an **absolute** score for a fixed posting. Every other scoring
    test asserted a relative property (A > B), which survives any global rescale —
    which is how `avg_jd_tokens` stayed wrong by 4× for three releases with a green
    suite.

### Housekeeping

- The stale `[Unreleased]` section is folded into **[0.2.0]**, where it belongs.
  An earlier draft of this entry guessed 0.3.0; `git log -S` puts the introducing
  commit (`a6927cb`) at the v0.2.0 tag itself, so those notes were 0.2.0's all
  along and simply never got promoted. Content moved verbatim, nothing rewritten.

## [0.4.1] - 2026-07-23

### Fixed

- `discover.name_variants` leaked a bare generic word when normalization collapsed a
  multi-word name to one token. `_norm_name` strips trade words (`group`, `company`,
  `holdings`, `the`), so `Capital Group` reduced to `capital` and the "conservative"
  variants WERE that bare word — no `aggressive` opt-in, no ownership check — producing
  a real false binding (`Capital Group` -> `lever/capital`, Capital.com's board). The
  gate now fires only when a TRADE word caused the collapse; a legal-suffix-only
  collapse (`ACME LLC` -> `acme`) is still a valid slug and is preserved.

## [0.4.0] - 2026-07-22

Discovery stops using a full job harvest to answer a yes/no question. Confirming
that one Workday board exists cost **210 HTTP requests** (10 list pages plus one
detail call per role, measured against a live tenant); it now costs **1**. That
over-fetch was itself what tripped Workday's rate limiter, so the 429 handling
added in 0.3.0 was defending against a storm this code was causing.

### Caller-visible contract changes (jobfitr)

1. **`discover.probe`'s `roles` value changes meaning.** It is now the ATS's own
   reported total from a cheap liveness call, not the length of a full fetch. For
   Workday that means the true open-role count instead of the page-capped 200 — a
   more accurate number, but a **different** one. Anything sorting or displaying
   `roles` will see larger values.
2. **`seed.ATS_PATTERNS` and `seed._JUNK` are gone.** Mining lives in
   `discover._PATTERNS` only. `seed.SeedError` still exists and is still catchable
   by that name — it is now an alias of the new `discover.DiscoveryError`.
3. **`seed.enumerate_tokens` is superseded by `seed.enumerate_entries`**, which
   returns full entry dicts rather than bare slugs (Workday needs its host and site
   to be fetchable at all). `enumerate_tokens` remains for slug-only callers.

### Added

- **`sources.LIVENESS` + `sources.liveness_for(ats)`** — cheap per-ATS "is this
  board real" probes returning an exact role count. Measured against live boards on
  2026-07-22: Greenhouse without job bodies (244 KB vs 4.4 MB), Lever `?limit=1`
  (8 KB vs 379 KB), Workday one POST with no detail pass (1 request vs 210),
  SmartRecruiters `totalFound` (verified to agree with a full fetch). Workable uses
  the documented `details=false` variant but its saving is **unverified** — no
  reachable Workable account had open roles to measure against. Ashby is excluded
  on purpose: measured, it returns its whole board either way, so there is nothing
  cheaper to call. Ashby, and any future ATS without a variant, fall back to the
  full adapter transparently — callers never need to know which are cheap.
- **`job-radar seed workday`** — the miner already understood Workday's
  tenant/host/site triple; the CLI just never offered it. The choice list is now
  derived from the miner's own pattern table so the two cannot drift again.

### Fixed

- **A corrupt `watchlist.json` no longer discards a good scan.** `cli.cmd_scan`
  caught `OSError`, but `json.JSONDecodeError` is a `ValueError` — so it escaped
  _after_ the full network harvest and _before_ the shortlist write. Growing the
  watchlist is a nice-to-have; the harvest is the point.
- **A mid-pagination failure no longer loses the whole employer.** `fetch_workday`
  now keeps the pages it already fetched and stops early, matching the best-effort
  discipline the detail pass already used. A failure on page 1 still raises, since
  there is nothing to salvage and a silent empty would look like a live board with
  no jobs.
- **Nested thread pools.** The Workday detail pass opened its own 8-worker pool per
  employer inside the engine's 12-worker pool: a measured peak of **96** concurrent
  requests against a nominal cap of 12. One shared, lazily-created pool now makes
  the real ceiling ~20.
- **Mining missed `job-boards.greenhouse.io`** (Greenhouse's current host) and
  **dropped whole Workday tenants on a lowercase `en-us`** locale segment. Both
  came from this module carrying its own narrower copies of regexes
  `dedup.ats_from_url` already had right; the five single-key ATSs now route
  through that one parser. The third copy, in `seed.py`, still cut a slug at `?`
  rather than `&` — the bug 0.3.1 fixed elsewhere — and is deleted.
- **`discover.match_known` was O(names × variants × universe)**, building a lookup
  dict and then discarding it. 428.8 ms → 6.22 ms at 5k × 5k, with output pinned
  byte-identical against the previous algorithm by a differential test.

### Changed

- `seed --verify` probes concurrently through `discover.probe` instead of one
  board at a time with a full harvest fetch each, keeping the same early stop at
  `--max`.
- Removed every unsourced percentage from the discovery docstrings (mining yield
  rates, "120 real store names", per-job timings). No artifact ever existed to
  check them against, and `mine` caps CDX _rows_ rather than companies over a
  SURT-sorted index — so any rate measured that way describes an alphabetically
  truncated slice, not the population. The mechanisms they were attached to are
  unchanged and still documented.

## [0.3.2] - 2026-07-22

Documentation correctness. An independent panel review reproduced five claims in the
docs and comments that contradicted the shipped code; a docstring that confidently
states the opposite of what a function does is worse than no docstring, because it is
the one place a caller looks. No behaviour changed except the one item noted below.

### Changed

- `fetch_workday`'s docstring claimed descriptions are "deliberately NOT fetched."
  They are fetched by default. It now states the real cost — one request per role,
  not per page — and the cap comment gives the combined list+detail total rather than
  the list-only figure, which understated a realistic run by an order of magnitude.
- `verify_identity` credited `capital`/Capital One and `foundation`/Foundation for
  the NIH as catches. Neither is a live Greenhouse board, so the liveness probe drops
  them before the identity gate runs. The docstring now cites only confirmed catches
  and states two boundaries that were easy to misread: a dead slug never reaches the
  gate, and on Lever it returns `True` unconditionally — so the motivating example
  (`jobs.lever.co/capital`, a real board that is not Capital One's) is protected by
  `from_names` withholding the first-word variant, not by this function.
- `probe`'s documented outcome enum omitted `throttled` and `unsupported`. It is now
  complete and split into terminal versus retryable, since deciding whether to
  blacklist is the reason a caller reads it. Also documents that `wrong-owner`
  currently fires when the identity endpoint is merely unreachable.
- The 200-role Workday cap (`WORKDAY_MAX_PAGES` x 20) was undisclosed. It is
  documented as silent and lossy — NVIDIA reports `total=2000` and returns 200 — and
  ordering is Workday's own, so the roles kept are not necessarily the newest.
- `engine`'s module docstring still said the engine grows the watchlist and that
  `store` writes postings. Neither has been true since 0.3.0.
- The 0.3.0 entry credited a fix for a Workday pagination bug that never shipped:
  `git log -S` shows the `total` latch was present in the same commit that
  introduced the adapter. Recorded as a design note under Added instead.
- README scopes its "every source is an official, public API used as documented"
  claim to exclude Workday, whose CxS endpoint is public and no-auth but is not
  documented for third-party use the way Greenhouse's and Lever's are.

### Added

- `WORKDAY_MAX_PAGES` reads the environment, like `WORKDAY_FETCH_DETAILS` and
  `WORKDAY_DETAIL_WORKERS` already did. Default unchanged at 10. It was the only
  Workday knob that could not be tuned, and documenting an unadjustable cap is half
  a fix.

## [0.3.1] - 2026-07-22

### Fixed

- `dedup.ats_from_url` stopped at `/ ? #` but not `&`. Greenhouse's embed form puts
  the slug inside the query string (`embed/job_app?for=SLUG&token=...`), so the
  pattern consumed the `?` itself and the capture ran on through, yielding slugs like
  `gemini&token=7743177&gh_jid=7743177`. Harmless on its own — a malformed slug just
  probes as a 404 — but it corrupts any consumer that compares parsed slugs against
  known boards, which is exactly what apply-URL ownership auditing does.

## [0.3.0] - 2026-07-22

### Added

- **Workday adapter** (`fetch_workday`) over the public CxS endpoint — the first
  enterprise ATS in the set, reaching the manufacturers, insurers, municipalities and
  national labs that never appear on the startup boards. Needs a three-part key
  (tenant, `wdN` host, site slug) rather than a slug, so `DEPTH_EXTRA_FIELDS` lets an
  adapter declare the extra watchlist fields it requires. Job descriptions are fetched
  from the per-job detail endpoint behind `WORKDAY_FETCH_DETAILS` (default on) —
  without a body a job cannot be ranked or read, so this is a precondition rather than
  an enhancement. Budget one request per role for it, on top of one per 20 for the
  listing. Two design notes worth knowing before relying on it: Workday reports
  `total` only on the first page (it is latched once — re-reading it per page ends the
  loop after two pages), and each employer is silently truncated at
  `WORKDAY_MAX_PAGES` × 20 = 200 roles.
- **`job_radar.discover`** — bulk company discovery. Mines the Common Crawl CDX index
  by ATS URL pattern to recover slugs (and Workday's full triple) in bulk instead of
  one company at a time, and resolves a company NAME to a slug for employers the index
  never saw. Every candidate is verified by a live probe before it is trusted.
- **Board-ownership verification** (`verify_identity`). A probe proves a board is
  LIVE; it cannot prove the board is the RIGHT one. `jobs.lever.co/capital` is a real
  board with real jobs owned by someone other than Capital One. Greenhouse reports who
  owns a board, so we now ask, and a mismatch is rejected.
- `util.post_json` for POST-only read APIs.

### Changed

- **`engine.harvest` accepts a company array** (`companies=[...]`) as well as a
  watchlist path, so a caller that keeps its universe somewhere other than a JSON file
  can drive the engine.
- **The engine no longer writes files.** Discovered companies are RETURNED instead of
  being appended to the caller's watchlist; persistence belongs to whoever owns the
  universe. `cli.py` does it for the standalone CLI, so its behaviour is unchanged.
- Source defaults are now expressed as absence rather than a copied list of adapter
  names. `config.ALL_DEPTH`/`ALL_BREADTH` are gone: they duplicated the registries in
  `sources.py` and had already drifted, silently disabling a newly added adapter.
- Rate-limiting (429) is distinguished from a hard refusal (401/403) and a miss (404).
  Conflating them let a transient throttle be recorded as permanent.

### BREAKING

- `job_radar/store.py` is renamed to `job_radar/shortlist.py`. It is the CLI's
  shortlist.csv store and was imported only by `cli.py`, but the name collided with
  the store module of the app built on this library. Anyone importing
  `job_radar.store` must update the import.

## [0.2.0] - 2026-07-14

### Added

- `job-radar init` — writes a starter `job-radar.yaml` + `watchlist.json` into the
  current folder (refuses to overwrite existing files). The example config and
  starter watchlist now ship inside the package.
- CI (GitHub Actions): `ruff` + `pytest` on Python 3.10–3.13, plus CodeQL.
- `SECURITY.md`, `CONTRIBUTING.md`, this changelog.
- Tests for the source parsers, `engine.harvest` end-to-end, the watchlist funnel,
  and the date/salary/word-match helpers.
- `--verbose` (print which sources failed and why) and `--strict` (exit nonzero if
  any source errored, for scheduled runs / CI) flags on `scan`.
- Quality-tier tags (`★ strong` / `◆ worth a look`) on each surfaced role, driven by
  the `scoring.tiers` config (previously loaded but unused).
- `seed` gained its own `--max` flag (default 500) instead of reusing the print
  `--limit` (which capped it at 25).

### Changed

- **De-duplication costs a factor of the company count less** — a company-block
  index plus block/title precomputed on insert, so the fuzzy pass compares only
  same-company candidates. Output is byte-identical to before; a run over ~8k
  postings drops from ~31s to ~3s of CPU. (This entry originally called the result
  "linear instead of O(n²)". Measured later, it is not: blocking divides the
  constant, not the asymptotic, so cost is still quadratic in postings for a fixed
  company universe — per-posting time rises ~1.5x per doubling. The speed-up is
  real; the label was wrong, and nothing measured the growth curve to catch it.)
- Breadth sources are fetched **in parallel** (like the depth sources); removed the
  pointless cross-host delay between independent providers.
- Keyword scoring scans the fit-weights **once** per posting (was twice).
- Seniority is **kept** in the de-dup key: `Staff` / `Senior` / `Lead` are treated
  as distinct roles instead of collapsing into one.
- Dates are now Eastern Time throughout (fixes off-by-one role ages near midnight).
- Install: use `pipx install git+https://github.com/hawkesj12/job-radar` until a
  PyPI release is published.
- Keyword scoring is faster (tokenize-once + set membership for single-word keywords,
  a first-token prefilter for multi-word ones); output is byte-identical, verified by
  a differential-equivalence test over 20,000 randomized postings.
- Starter watchlist repaired: fixed five dead Greenhouse slugs (→ Ashby / corrected),
  added Harvey / Sierra / LangChain / ElevenLabs — a clean first run with 0 feed errors.
- README now describes what a fresh clone actually does (a starter watchlist + ten
  aggregator feeds, growable via `seed`) instead of overstating out-of-box coverage.
- Store writes use a unique temp file (`mkstemp`) so overlapping runs can't collide.

### Fixed

- A non-integer `ADZUNA_PAGES` / `USAJOBS_RESULTS_PER_PAGE` no longer crashes every
  command at import; a malformed `job-radar.yaml` now warns and falls back to
  defaults instead of dumping a traceback.
- Auto-discovery no longer writes into the shipped `watchlist.example.json`
  template; it seeds and grows a real `watchlist.json`.
- A recruiter re-titling a role you already applied to no longer resurfaces it as a
  new row (sticky status now re-matches on the stable job URL).
- Salary parsing no longer mistakes funding figures ("$20-40 million") for pay.
- Broken job sources surface in the run's error count instead of silently looking
  like "no jobs."
- The LLM re-rank path writes the shortlist once per run instead of twice.
- **Windows:** every file open and stdout/stderr are UTF-8, so non-ASCII job titles
  and the `✓ ⚠ ↳ ★` glyphs no longer crash a run (`UnicodeEncodeError`) on a cp1252
  console or a redirected/scheduled-task stdout.
- A total source outage no longer wipes the shortlist / resets `first_seen`; the prior
  file is kept and the run exits nonzero.
- A corrupt `watchlist.json` now surfaces a loud error instead of silently dropping the
  entire depth harvest.
- `seed` degrades gracefully (a clean message, exit 1) on any Common Crawl failure,
  including a mid-stream connection reset — no raw traceback.
- De-duplication no longer over-merges distinct roles that share a title prefix
  (e.g. "AI Engineer" vs "AI Engineer, Payments").
- Keyword-stuffed titles can't run away the score (the title double-count is capped).
- Remote/on-site negation is read from the title and location, not only the body.
- `first_seen` is Eastern Time (was naive local), matching the age math.
- `apply` / `dismiss` on a non-existent id exits nonzero.

### Removed

- The unimplemented `[scrapers]` extra and its config key (it never did anything).
- Dead code (`util.env`, an unused constant) and the misleading optional-rapidfuzz
  fallback (rapidfuzz is a required dependency).

### Security

- SmartRecruiters no longer hard-codes `?q=AI`; it harvests generically like the other
  ATS sources and lets the relevance gate filter.
- Braintrust pagination only follows a `next` URL that stays on its own host (SSRF guard).
- Watchlist slugs are validated (`[A-Za-z0-9._-]`) before being spliced into ATS URLs.

## [0.1.0]

- Initial public release.
