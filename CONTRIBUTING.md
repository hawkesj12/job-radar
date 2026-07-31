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

## Reporting bugs

Open an issue with the command you ran, what you expected, and what happened. For
security issues, see [SECURITY.md](SECURITY.md) instead.
