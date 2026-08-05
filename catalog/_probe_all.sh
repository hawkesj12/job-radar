#!/bin/sh
# Run _probe.py over every source in _targets.json, one at a time, saving each
# result to _out/<name>.txt. Sequential ON PURPOSE: these sources are being probed
# partly FOR their throttling behaviour, and concurrent requests make a real rate
# limit indistinguishable from self-contention (_SCHEMA.md, closing note).
#
#   sh catalog/_probe_all.sh            # every target
#   sh catalog/_probe_all.sh jobicy     # just one
set -u
cd "$(dirname "$0")/.." || exit 1
mkdir -p catalog/_out
only="${1:-}"

python3 - "$only" <<'PY'
import json, pathlib, subprocess, sys, time

only = sys.argv[1] if len(sys.argv) > 1 else ""
spec = json.loads(pathlib.Path("catalog/_targets.json").read_text())
out = pathlib.Path("catalog/_out")

jobs = []
for lane in ("breadth", "depth"):
    for name, t in spec[lane].items():
        if name.startswith("_"):
            continue
        if only and name != only:
            continue
        jobs.append((name, t))

for i, (name, t) in enumerate(jobs, 1):
    cmd = [
        sys.executable, "catalog/_probe.py",
        "--name", name, "--base", t["base"],
        "--items", t.get("items", ""),
        "--date-field", t.get("date", ""),
        "--title-field", t.get("title", "title"),
        "--format", t.get("format", "json"),
        "--page-param", t.get("page_param", "page"),
        "--page-mode", t.get("page_mode", "page"),
        "--page-size", str(t.get("page_size", 0)),
    ]
    if t.get("title_params"):
        cmd += ["--title-params", t["title_params"]]
    if t.get("skip"):
        cmd += ["--skip", t["skip"]]
    print(f"[{i}/{len(jobs)}] {name}", flush=True)
    t0 = time.monotonic()
    r = subprocess.run(cmd, capture_output=True, text=True)
    (out / f"{name}.txt").write_text(r.stdout + "\n# ── stderr\n" + r.stderr)
    status = "ok" if r.returncode == 0 else f"EXIT {r.returncode}"
    print(f"      {status} in {time.monotonic() - t0:.0f}s -> catalog/_out/{name}.txt", flush=True)
PY
