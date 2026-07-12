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

Ask me, one short round at a time:

1. What roles am I targeting? (titles / kind of work)
2. What am I strongest at / what should pull a role UP? (skills, tools, domain, a city)
3. Dealbreakers — titles or things to push DOWN or exclude?
4. Remote-only? Any locations to exclude? How fresh must postings be (max age in days)?
5. Roughly how selective — show me lots, or only strong matches (a min score)?

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
keywords that are TRUE for me. Output the YAML in a single code block, ready to
save. Start by asking me question 1.
