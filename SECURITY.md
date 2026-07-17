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
local files (`shortlist.csv`, `watchlist.json`). API keys (Adzuna, USAJOBS, the
optional LLM) are read from environment variables only — never logged or written
to disk. The most relevant surface is untrusted job-posting text flowing into the
CSV store; that text is neutralized against spreadsheet formula injection on write.

## Supported versions

The latest released version receives security fixes.
