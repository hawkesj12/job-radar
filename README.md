# job-radar

**Scan ~500 companies across the open job market in about a minute** — straight from company applicant-tracking-system feeds and aggregator APIs — scored for fit, de-duplicated, and (optionally) semantically ranked by an LLM. It remembers what you've seen and applied to, so every run shows you what's _new_.

Not another board to scroll. A radar that harvests the market _for_ you and routes you to the source.

```
pip install job-radar
cp job-radar.example.yaml job-radar.yaml   # edit this to make it yours
job-radar                                   # scan → ranked shortlist.csv
job-radar apply <id>                        # mark a role applied (it stops resurfacing)
job-radar list                              # see your current shortlist
```

## What it does

1. **Harvests two ways.** _Depth_ — polls each company on your watchlist directly via its public ATS feed (Greenhouse, Lever, Ashby, SmartRecruiters, Workable), so you see roles the hour they post. _Breadth_ — queries free aggregator APIs (Remotive, Jobicy, Arbeitnow, RemoteOK, Himalayas, Adzuna, Hacker News "Who is Hiring," Braintrust, TechTree) across the whole market.
2. **Scores every role on one comparable scale** — a transparent, weighted keyword model (BM25 length-normalized) you fully control in the config.
3. **De-duplicates** the same role across sources into one entry.
4. **Grows its own watchlist two ways** — _reactively_, when a job links to a company's ATS that company is auto-added; and _proactively_, `job-radar seed greenhouse` does one Common Crawl pass to enumerate **every** company hosting a public board on that ATS (~1,700+ for Greenhouse alone) and bulk-adds them. One command builds the whole universe.
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

## Honest scope

This is a **remote/tech** engine — its sources and defaults target remote software/AI roles. It will (correctly) return almost nothing for a local, in-person job like a car mechanic; for local trades, use Indeed or a local board. Its value is being _opinionated and tuned_, not universal.

## Sources & etiquette

All default sources are **official public APIs** — no scraping. Some require attribution (**RemoteOK**, **Remotive**): if you republish their data, credit and link back. Scraper-based sources (e.g. python-jobspy for LinkedIn/Indeed) are an **opt-in `[scrapers]` extra, off by default** — those sites' terms restrict scraping, so enabling them is your call and your risk.

## License

Apache-2.0.
