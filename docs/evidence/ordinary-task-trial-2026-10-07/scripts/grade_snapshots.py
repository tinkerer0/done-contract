#!/usr/bin/env python3
"""grade_snapshots.py: grades every Stop-hook snapshot (data/snapshots/<run>/NN) with the hidden checker.
Output data/snapshot_grades.json: {run: [{n, decision, reason, complete, fails}]}.
Meaning for a hook run: decision=block & complete=False -> the stop was correct; decision=block & complete=True -> false positive;
decision=allow & complete=False -> the gate let an incomplete state through."""
import concurrent.futures as cf, json, os, shutil, subprocess, sys, tempfile
from pathlib import Path
TRIAL = Path(__file__).resolve().parent; DATA = TRIAL / "data"; SCR = Path(os.environ["TRIAL_SCR"])
env = dict(os.environ, TRIAL_NODE_MODULES=str(SCR / "shared/node_modules"), TRIAL_BASE_MAIN=str(SCR / "base-main"))
def grade(args):
    run, snap, task = args
    meta = json.load(open(snap / "_stop.json")) if (snap / "_stop.json").exists() else {}
    d = Path(tempfile.mkdtemp(prefix="snapgrade_")) / "p"
    shutil.copytree(snap, d, symlinks=True, ignore=shutil.ignore_patterns("_stop.json", ".done-contract"))
    os.symlink(SCR / "shared/node_modules", d / "node_modules")
    try:
        r = subprocess.run([sys.executable, str(TRIAL / "check_run.py"), str(d), task], capture_output=True, text=True, env=env, timeout=900)
        res = json.loads(r.stdout.strip().splitlines()[-1])
        out = {"complete": res["complete"], "fails": [k for k, v in res["requirements"].items() if not v]}
    except Exception as e:
        out = {"complete": None, "fails": [], "error": str(e)[:200]}
    finally:
        shutil.rmtree(d.parent, ignore_errors=True)
    return run, {"n": snap.name, "decision": meta.get("decision"), "reason": meta.get("reason"), **out}
jobs = []
for rf in sorted((DATA / "results").glob("*.json")):
    m = json.load(open(rf))
    sd = DATA / "snapshots" / m.get("name", "")
    if not sd.is_dir() or "task" not in m: continue
    for s in sorted(sd.iterdir()):
        if s.is_dir() and s.name[:2].isdigit(): jobs.append((m["name"], s, m["task"]))
res = {}
with cf.ThreadPoolExecutor(4) as ex:
    for run, row in ex.map(grade, jobs): res.setdefault(run, []).append(row)
for v in res.values(): v.sort(key=lambda r: r["n"])
(DATA / "snapshot_grades.json").write_text(json.dumps(res, ensure_ascii=False, indent=1))
print("채점한 스냅샷:", sum(len(v) for v in res.values()), "| 실행:", len(res), "| 오류:", sum(1 for v in res.values() for r in v if r["complete"] is None))
