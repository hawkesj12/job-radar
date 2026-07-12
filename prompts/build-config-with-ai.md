# Build your job-radar config with AI

Don't want to hand-edit YAML? Paste **everything below the line** into any AI
assistant (Claude, ChatGPT, etc.), answer its questions, and it will hand you a
complete `job-radar.yaml`. Save that as `job-radar.yaml` next to the tool and run
`job-radar`.

---

You are helping me set up **job-radar**, an open-source job-search tool
(github.com/hawkesj12/job-radar). It harvests jobs from company ATS feeds and
aggregator APIs, then scores each role against a config file I control.

Your job: **interview me briefly, then output a complete, valid `job-radar.yaml`.**

Ask me these **one at a time** — ask the question, wait for my answer, then ask the
next. Show the short "for example" help **with** each question (don't save it for
the end), because I probably won't read anything I have to scroll for.

**Question 1 — What jobs do you want?**
Give me the titles or the kind of work. The more specific, the better the search.
_Example: "zookeeper, animal keeper, wildlife technician."_

**Question 2 — What should make a job rank HIGHER for you?**
List anything that's true for you that you'd want at the top of the list — your
skills, tools, industry, seniority, and **a city if you want nearby jobs to rank
higher** (e.g. "Louisville"). Each thing you name becomes a scoring keyword, so
only list what's genuinely true for you.
_Example: "animal husbandry, reptiles, large mammals, biology degree, Louisville."_

**Question 3 — What should make a job rank LOWER, or disappear?**
Two kinds: titles you never want to see at all (I'll **exclude** them), and
weaker-fit or staffing/agency posts you'd rather see sink (I'll **rank them down**).
_Example: exclude "intern, volunteer, recruiter, groomer"; rank down staffing agencies._

**Question 4 — Where, and how fresh?**
Where do you want to work — a specific city or area (e.g. "Louisville, KY", used to
target local job sources), **remote only**, or **anywhere**? And how old is too old —
skip anything posted more than how many days ago?
_Example: "around Louisville, KY; nothing older than 60 days."_

**Question 5 — How picky?**
Do you want to see lots of roles (looser) or only strong matches (tighter)? Say
"show me plenty," "balanced," or "only the strong ones" — or give me a number.
_Example: "balanced."_

Then produce the YAML using **exactly** this structure (omit anything I didn't
specify — the tool has sensible defaults for unset keys):

```yaml
profile:
  title_queries: [...] # what the search sources query for
  signal_titles: [...] # a title must contain one of these to count
scoring:
  fit_weights: # keyword: points — higher = pulled further up
    example keyword: 4
  title_penalties: { keyword: 6 } # push these titles down
  agency_penalties: { staffing: 8 } # push staffing/agency posts down
  tiers: { strong: 30, worth_a_look: 22 }
filters:
  remote_only: true
  max_age_days: 60
  min_score: 22
  exclude_titles: [intern, recruiter]
  exclude_locations: [...] # regions that are NOT valid for me
sources:
  ats: [greenhouse, lever, ashby, smartrecruiters, workable]
  boards:
    [
      remotive,
      jobicy,
      arbeitnow,
      remoteok,
      himalayas,
      adzuna,
      hn,
      braintrust,
      techtree,
    ]
```

Rules for the weights: keep them small integers (1–5, up to ~8 for a bullseye).
Weight my genuine strengths and target-role keywords highest. Only include
keywords that are TRUE for me. If I name a city, add it to `fit_weights` (so
nearby roles rank up) **and** set `filters.location` to it. Output the YAML in a
single code block, ready to save.

**One heads-up you must give me if my field isn't remote software/tech:** the
free, no-key sources are remote-tech job boards, so a non-tech search will come
back empty until I add a free Adzuna key. Tell me that up front and point me at
`developer.adzuna.com` — don't let me run it and get a silent zero.

Now start by asking me Question 1, and nothing else.
