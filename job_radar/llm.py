"""Optional LLM fit-verdict / re-rank layer.

The deterministic engine harvests + first-pass-scores the whole market for free.
This layer -- only if you configure an API key -- reads the *top-N* shortlist and
scores each role for SEMANTIC fit (0-100) plus a one-line why/gaps note, catching
the great-fit roles keyword counting undervalues.

Off by default. A true no-op when disabled or keyless, so the tool always runs
free. Cost-bounded to `rerank_top_n` roles, one request per run. Stdlib only --
no provider SDK required.
"""

from __future__ import annotations

import json
import re
import urllib.request

from . import config


def _profile(cfg) -> str:
    top_kw = sorted(cfg.fit_weights.items(), key=lambda kv: kv[1], reverse=True)[:18]
    return (
        "Target roles: " + "; ".join(cfg.title_queries) + ". "
        "Values (highest-weight signals): " + ", ".join(k for k, _ in top_kw) + ". "
        "Remote required."
        if cfg.remote_only
        else ""
    )


_SYSTEM = (
    "You are a job-fit judge. Given a candidate profile and a list of job "
    "postings, score each posting 0-100 for how well it fits the candidate "
    "(semantic fit, not keyword overlap), and give a <=14-word note on why it "
    "fits or what's missing. Respond ONLY with a JSON array of objects "
    '{"id": <int>, "fit": <0-100>, "note": "<string>"} -- no prose.'
)


def _call(cfg, user_text: str) -> str:
    llm = cfg.llm
    key = cfg.env(llm.api_key_env)
    if llm.provider == "anthropic":
        url = (llm.base_url or "https://api.anthropic.com") + "/v1/messages"
        body = {
            "model": llm.model,
            "max_tokens": 1500,
            "system": _SYSTEM,
            "messages": [{"role": "user", "content": user_text}],
        }
        headers = {
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
    else:  # openai-compatible
        url = (llm.base_url or "https://api.openai.com/v1") + "/chat/completions"
        body = {
            "model": llm.model,
            "messages": [
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": user_text},
            ],
        }
        headers = {"Authorization": f"Bearer {key}", "content-type": "application/json"}
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(), headers=headers, method="POST"
    )
    with urllib.request.urlopen(req, timeout=cfg.timeout * 3) as r:
        data = json.loads(r.read().decode("utf-8", "replace"))
    if llm.provider == "anthropic":
        return "".join(b.get("text", "") for b in data.get("content", []))
    return data["choices"][0]["message"]["content"]


def _parse(raw: str) -> list:
    m = re.search(r"\[.*\]", raw, re.S)
    if not m:
        return []
    try:
        return json.loads(m.group(0))
    except Exception:
        return []


def rerank(items: list[dict], cfg=None) -> dict:
    """items: [{key, title, company, text}]. Returns {key: {llm_score, llm_note}}.
    No-op (empty dict) when the LLM is disabled or no API key is set."""
    cfg = cfg or config.active()
    if not cfg.llm.enabled or not cfg.env(cfg.llm.api_key_env):
        return {}
    items = items[: cfg.llm.rerank_top_n]
    if not items:
        return {}
    lines = []
    for i, it in enumerate(items):
        jd = (it.get("text") or "")[:800]
        lines.append(
            f'{{"id": {i}, "title": {json.dumps(it.get("title", ""))}, '
            f'"company": {json.dumps(it.get("company", ""))}, '
            f'"jd": {json.dumps(jd)}}}'
        )
    user = (
        f"CANDIDATE PROFILE:\n{_profile(cfg)}\n\nPOSTINGS:\n[\n"
        + ",\n".join(lines)
        + "\n]"
    )
    try:
        parsed = _parse(_call(cfg, user))
    except Exception as e:  # keep the tool working if the API hiccups
        print(f"  llm: skipped ({type(e).__name__}: {e})")
        return {}
    out = {}
    for obj in parsed:
        try:
            i = int(obj["id"])
        except (KeyError, ValueError, TypeError):
            continue
        if 0 <= i < len(items):
            out[items[i]["key"]] = {
                "llm_score": int(obj.get("fit", 0)),
                "llm_note": str(obj.get("note", ""))[:160],
            }
    return out
