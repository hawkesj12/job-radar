# Security Policy

## Reporting a vulnerability

Please report security issues privately via GitHub's
[**Report a vulnerability**](https://github.com/hawkesj12/job-radar/security/advisories/new)
(Security → Advisories), or by opening a minimal issue that says only "security —
please advise" so a private channel can be opened. Do not post exploit details in
a public issue.

Expect an initial response within a few days. Fixes ship in a patch release and
are credited in the [CHANGELOG](CHANGELOG.md) unless you ask otherwise.

## Scope

job-radar is a local, single-user CLI. It reads public job-board APIs and writes
local files (`shortlist.csv`, `watchlist.json`).

**API keys** (Adzuna, USAJOBS, SerpApi, the optional LLM) are read from environment
variables only, and are never written to disk by this tool. Error paths report the
exception **type**, never its message — which matters because Adzuna's and SerpApi's
keys ride in the request URL and the LLM key rides in a header, so an exception
object can contain a key even when nothing intends to print one. Both claims are
covered by tests.

**Untrusted job-posting text** flows from ~500 third parties into `shortlist.csv`,
so **every** column is neutralized against spreadsheet formula injection on write —
not a curated subset, because a curated subset is a judgement that has to be
re-made whenever a column is added, and ours was wrong once (`posted` bypassed it
while being fed from vendor data).

**Config files cannot redirect a credential.** A `job-radar.yaml` in the current
directory is loaded automatically, which means a config file you did not write is
honored simply because you ran from its directory. Most settings are harmless that
way, but two are not: `base_url` chooses the host a request goes to, and the
`*_key_env` names choose which environment variable travels with it — together
enough to send your API key to someone else's server. A **discovered** config has
those keys reset to the defaults and prints a notice saying so; the rest of the
file still applies. Pass `--config <path>` to opt in deliberately.

## Supported versions

The latest released version receives security fixes.
