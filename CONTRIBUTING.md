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

Both must pass (CI runs them on Python 3.10–3.13):

```bash
ruff check .
pytest -q
```

- Keep the tool **stdlib-first** — a new runtime dependency needs a real
  justification (the two we have, `pyyaml` and `rapidfuzz`, earn their place).
- **Adding a job source?** Use the provider's documented public API — no scraping.
  Add a parser test with a captured sample response (see the `sources` tests).
- Match the existing style; `ruff` enforces formatting and imports.

## Reporting bugs

Open an issue with the command you ran, what you expected, and what happened. For
security issues, see [SECURITY.md](SECURITY.md) instead.
