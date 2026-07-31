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
