#!/usr/bin/env python3
"""regrade.py [checker.py]: re-score every stored run with the given checker (default check_run.py). The earlier verdict is kept as check_at_run."""
import concurrent.futures as cf, json, os, subprocess, sys
from pathlib import Path
TRIAL = Path(__file__).resolve().parent
DATA = TRIAL / "data"
SCR = Path(os.environ["TRIAL_SCR"])
checker = sys.argv[1] if len(sys.argv) > 1 else str(TRIAL / "check_run.py")
prefix = sys.argv[2] if len(sys.argv) > 2 else ""
env = dict(os.environ, TRIAL_NODE_MODULES=str(SCR / "shared/node_modules"), TRIAL_BASE_MAIN=str(SCR / "base-main"))
changed = 0
def work(f):
    m = json.load(open(f))
    if "run_dir" not in m or not Path(m["run_dir"]).exists() or "check" not in m: return None
    r = subprocess.run([sys.executable, checker, m["run_dir"], m["task"]], capture_output=True, text=True, env=env)
    try: new = json.loads(r.stdout.strip().splitlines()[-1])
    except Exception: new = {"error": (r.stdout + r.stderr)[-300:]}
    if "check_at_run" not in m: m["check_at_run"] = m["check"]
    flip = m["check"].get("complete") != new.get("complete")
    m["check"] = new
    f.write_text(json.dumps(m, ensure_ascii=False, indent=1))
    return (m["name"], new.get("complete"), flip, "error" in new)

files = [f for f in sorted((DATA / "results").glob(prefix + "*.json"))]
with cf.ThreadPoolExecutor(4) as ex:
    res = [x for x in ex.map(work, files) if x]
for name, comp, flip, err in res:
    print(f"{name}: complete={comp}" + ("  (판정 바뀜)" if flip else "") + ("  (채점 오류)" if err else ""))
print("재채점:", len(res), "판정이 바뀐 실행:", sum(1 for x in res if x[2]), "채점 오류:", sum(1 for x in res if x[3]))
