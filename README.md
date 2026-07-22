# job-radar

**Scan the open job market in about a minute** — your watchlist of companies plus ten aggregator APIs, straight from company applicant-tracking-system feeds — scored for fit, de-duplicated, and (optionally) semantically ranked by an LLM. It remembers what you've seen and applied to, so every run shows you what's _new_. Start with the ~20-company starter list and grow your watchlist to hundreds with one `seed` command.

Not another board to scroll. A radar that harvests the market _for_ you and routes you to the source.

```
pipx install git+https://github.com/hawkesj12/job-radar   # or: pip install git+…
job-radar init                              # write a starter job-radar.yaml + watchlist.json here
# edit job-radar.yaml to make it yours
job-radar                                   # scan → ranked shortlist.csv
job-radar apply <id>                        # mark a role applied (it stops resurfacing)
job-radar list                              # see your current shortlist
```

> A PyPI release (`pip install job-radar`) is coming; until then install from Git as above.

## What it does

1. **Harvests two ways.** _Depth_ — polls each company on your watchlist directly via its public ATS feed (Greenhouse, Lever, Ashby, SmartRecruiters, Workable, Workday), so you see roles the hour they post. Workday is the enterprise one: it reaches the manufacturers, insurers, hospitals, municipalities and national labs that never appear on the startup boards. _Breadth_ — queries free aggregator APIs (Remotive, Jobicy, Arbeitnow, RemoteOK, Himalayas, Adzuna, USAJOBS, Hacker News "Who is Hiring," Braintrust, TechTree) across the whole market.
2. **Scores every role on one comparable scale** — a transparent, weighted keyword model (BM25 length-normalized) you fully control in the config. It's tuned for _recall_ by design (catch everything that might fit); the optional LLM re-rank below is the _precision_ layer.
3. **De-duplicates** the same role across sources into one entry.
4. **Grows its own watchlist two ways** — _reactively_, when a job links to a company's ATS that company is auto-added; and _proactively_, `job-radar seed greenhouse` (also `lever`, `ashby`, `workable`, `smartrecruiters`, `workday`) does one Common Crawl pass to enumerate the companies hosting a public board on that ATS and bulk-adds them — up to `--max` per run (default 500). Add `--verify` to probe each board and keep only the live ones. A couple of `seed` runs build out a several-hundred-company watchlist.
5. **Remembers.** One upserted `shortlist.csv` tracks `first_seen`, `status`, and every role's score. `apply`/`dismiss` are sticky — applied roles persist and stop resurfacing.

## Optional: LLM semantic fit-ranking

Keyword scoring is fast and free but blind to _meaning_ — a perfect role that phrases things differently scores low. Add an API key and job-radar re-ranks the **top of your list** for semantic fit (0–100) with a one-line _why it fits / what's missing_ note:

```yaml
llm:
  enabled: true
  provider: anthropic # or an OpenAI-compatible endpoint
  api_key_env: ANTHROPIC_API_KEY
  rerank_top_n: 25
```

Off by default (the tool runs free with no key), cost-bounded to the top-N, one request per run.

## Make it yours

Everything is in one file — `job-radar.yaml`. Set your **target titles**, tune the **fit-weight keywords** (add your industry, your city), and adjust **filters** (`max_age_days`, `min_score`, remote-only, excluded locations). See `job-radar.example.yaml` for every knob.

**Or let AI write it for you (easiest).** Paste [`prompts/build-config-with-ai.md`](prompts/build-config-with-ai.md) into any AI assistant (Claude, ChatGPT). It interviews you about the job you want in plain English, then hands you a ready-to-save `job-radar.yaml` — no YAML editing required.

## Scope — tuned for remote/tech, generalizes to anyone

Out of the box it's tuned for **remote software/AI** roles, because the shipped example config and the free/keyless sources are remote-tech boards. But nothing about the engine is tech-specific — you generalize it in three steps:

- **Any field:** change `signal_titles` + `fit_weights` in the config to your field's language (nursing, finance, trades…). No code.
- **On-site / any location:** set `remote_only: false` and `location: "Your City, ST"`.
- **Any field _and_ location, for real:** turn on the **general sources** — **Adzuna** and **USAJOBS** (free keys; every field, any location, where the whole market lives).

Honest limits: the tool's _superpower_ — harvesting a role the hour it posts, direct from a company's ATS — is strongest in tech, because Greenhouse/Lever/Ashby are tech-company systems; for other fields you lean on the general aggregators. And the truly local, unposted, word-of-mouth job isn't in any structured feed, so no tool reaches it. Everything that _is_ posted online, this can find.

## Use it as a library

The CLI is one caller; the engine underneath is importable, and it doesn't touch your disk. `harvest` takes your companies as **data** — either a `watchlist.json` path or a plain list — and _returns_ everything it produced, including the companies it discovered along the way. Persisting them is the caller's job (the CLI appends them to `watchlist.json`; an app might write them to its own database).

```python
from job_radar import config, engine

config.set_active(config.load_config("job-radar.yaml"))   # optional; defaults apply otherwise

rows, discovered, errors = engine.harvest(companies=[
    {"name": "Anthropic", "ats": "greenhouse", "slug": "anthropic"},
    # Workday needs a three-part key instead of a bare slug:
    {"name": "Example Corp", "ats": "workday", "slug": "examplecorp",
     "host": "wd1", "site": "External"},
])
```

`job_radar.discover` builds the company universe in bulk: mine the Common Crawl index for every board on an ATS, or resolve a company **name** to its slug for employers the index never saw. Every candidate is proven by a live probe, and — where the ATS will tell us who owns a board — checked for **identity**, not just liveness. A probe proves `jobs.lever.co/capital` is a real board with real jobs; it does not prove it belongs to Capital One.

> Upgrading from 0.2.x: `job_radar.store` is now `job_radar.shortlist`.

## Legal & etiquette

This is a **personal job-search tool**, not a data-resale product, and it's built to be a good citizen:

- **Default sources are official, public, no-auth APIs**, used exactly as their vendors document them — Greenhouse, Lever, and Ashby publish these job-board endpoints _for_ programmatic use. Consuming a public API is distinct from scraping behind a login, and job-radar does none of the latter by default.
- **It rate-limits itself** to each provider's documented limits (e.g. Remotive is capped at 4 calls per run in code) and sends a self-identifying `User-Agent` so providers can see and contact the caller.
- **It asks for the smallest thing that answers the question.** Checking whether a company's board exists costs **one** request, not a full download of every job on it — for a Workday employer that is 1 request instead of 210, and for Greenhouse 244 KB instead of 4.4 MB. Discovery is where a tool like this can be rude at scale, so that's where the restraint matters most.
- **Attribution:** **RemoteOK** and **Remotive** require that, if you _republish_ their listings, you credit them and link back to the original job URL (job-radar keeps the direct source URL for exactly this). Honor their terms if you share `shortlist.csv` publicly.
- **API keys** (Adzuna, USAJOBS, the LLM) are read from environment variables only and never logged or committed. Note that Adzuna's key travels in the request URL per their API design.

One honest exception to "used as documented": **Workday**. Its CxS endpoint is public and no-auth — it's the same one Workday's own hosted careers-site widget calls — but Workday doesn't publish third-party API documentation for it the way Greenhouse and Lever do. It's a public endpoint used as its own front end uses it, which is a weaker claim than the others on this list, and worth knowing before you point it at hundreds of employers.

## License

Apache-2.0.
