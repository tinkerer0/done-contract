#!/usr/bin/env python3
"""contract_cross.py: run each agent-written contract (gate and contract-only arms) against (a) the untouched base and (b) every other run's final state of the same task.
accepts = every item exits 0 (what lets a Stop through). rejects = some item fails. inconclusive = a timeout/error or a check that points at paths we cannot move.
The owner's own run folder path inside a check is rewritten to the state being tested; nothing runs in the original folders."""
import concurrent.futures as cf, json, os, random, re, shutil, subprocess, sys, tempfile
from pathlib import Path
from common import load_results
SCR = Path(os.environ["TRIAL_SCR"]); TOOL_BIN = os.environ["TRIAL_TOOL_BIN"]
STRICT_ONLY = {k for k, v in json.load(open(Path(__file__).resolve().parent / "data/classification.json")).items() if v == "strict_only"}
rows = [r for r in load_results() if "check" in r and r.get("gate_status") in ("ok", "n/a") and Path(r.get("run_dir", "")).exists()]
owners = [r for r in rows if r["arm"] in ("gate", "contract") and (r.get("contract") or {}).get("items")]

def run_items(contract, owner_rd, state_dir):
    d = Path(tempfile.mkdtemp(prefix="cross_")) / "w"
    shutil.copytree(state_dir, d, symlinks=True, ignore=shutil.ignore_patterns("node_modules", ".done-contract", ".claude"))
    os.symlink(SCR / "shared/node_modules", d / "node_modules")
    out = []
    try:
        for it in contract["items"]:
            cmd = it["check"].replace(owner_rd, str(d))
            leftover = re.sub(r"/(usr|bin|dev|opt/homebrew)/\S*", "", cmd.replace(str(d), ""))
            nonport = bool(re.search(r"/(Users|private|var|tmp)/", leftover))
            try:
                p = subprocess.run(cmd, shell=True, cwd=d, capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL,
                                   env=dict(os.environ, PATH=f"{TOOL_BIN}:{os.environ['PATH']}"))
                out.append({"id": it.get("id"), "status": "pass" if p.returncode == 0 else "fail", "nonportable": nonport})
            except subprocess.TimeoutExpired:
                out.append({"id": it.get("id"), "status": "error", "nonportable": nonport})
    finally:
        shutil.rmtree(d.parent, ignore_errors=True)
    return out

def verdict(res):
    if any(r["status"] == "fail" for r in res): return "rejects"
    if any(r["status"] == "error" or r["nonportable"] for r in res): return "inconclusive"
    return "accepts"

def pick_states(task):
    """Every incomplete state of the task (they are what a good contract must reject) plus a seeded sample of complete ones."""
    same = [x for x in rows if x["task"] == task and x["name"] not in STRICT_ONLY]   # strict-only runs are substantively fine, so they are neither good nor bad references
    bad = [x for x in same if not x["final_complete"]]
    good = sorted([x for x in same if x["final_complete"]], key=lambda x: x["name"])
    random.Random(5).shuffle(good)
    return bad, good[:4]

def one_owner(g):
    task = g["task"]; base = SCR / ("base-t2" if task == "T2" else "base-main")
    c = g["contract"]; rd = g["run_dir"]
    base_res = run_items(c, rd, base); bv = verdict(base_res)
    rec = {"owner": g["name"], "arm": g["arm"], "items": len(c["items"]), "base": bv, "base_items": [(r["id"], r["status"]) for r in base_res], "vs": []}
    bad, good = pick_states(task)
    for x in bad + good:
        if x["name"] == g["name"]: continue
        v = verdict(run_items(c, rd, x["run_dir"]))
        rec["vs"].append({"state": x["name"], "state_is_complete": bool(x["final_complete"]), "contract": v})
    return rec

tot = {"contracts": 0, "base_accepted": 0, "base_inconclusive": 0, "bad_pairs": 0, "bad_accepted": 0, "bad_inconclusive": 0,
       "good_pairs": 0, "good_rejected": 0, "good_inconclusive": 0}
with cf.ThreadPoolExecutor(4) as ex:
    detail = list(ex.map(one_owner, owners))
for rec in detail:
    tot["contracts"] += 1; tot["base_accepted"] += rec["base"] == "accepts"; tot["base_inconclusive"] += rec["base"] == "inconclusive"
    for v in rec["vs"]:
        if v["state_is_complete"]:
            tot["good_pairs"] += 1; tot["good_rejected"] += v["contract"] == "rejects"; tot["good_inconclusive"] += v["contract"] == "inconclusive"
        else:
            tot["bad_pairs"] += 1; tot["bad_accepted"] += v["contract"] == "accepts"; tot["bad_inconclusive"] += v["contract"] == "inconclusive"
(Path(__file__).resolve().parent / "data" / "contract_cross.json").write_text(json.dumps({"summary": tot, "detail": detail}, ensure_ascii=False, indent=1))
print(json.dumps(tot, ensure_ascii=False, indent=1))
