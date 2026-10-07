#!/usr/bin/env python3
"""indep_sanity.py: records how each independent contract judges (a) the untouched start state and (b) the reference solution. Does not edit anything."""
import json, os, shutil, subprocess, tempfile
from pathlib import Path
SCR = Path(os.environ["TRIAL_SCR"]); HERE = Path(__file__).resolve().parent
TOOL_BIN = os.environ["TRIAL_TOOL_BIN"]
def run_contract(contract, state):
    d = Path(tempfile.mkdtemp(prefix="sanity_")) / "p"
    shutil.copytree(state, d, symlinks=True, ignore=shutil.ignore_patterns("node_modules", ".git"))
    os.symlink(SCR / "shared/node_modules", d / "node_modules")
    rows = []
    try:
        for it in contract["items"] + [{"id": f"repo:{c[:40]}", "check": c, "timeout": 300} for c in (contract.get("repo_checks") or [])]:
            try:
                p = subprocess.run(it["check"], shell=True, cwd=d, capture_output=True, text=True, timeout=int(it.get("timeout") or 180), stdin=subprocess.DEVNULL,
                                   env=dict(os.environ, PATH=f"{TOOL_BIN}:{os.environ['PATH']}"))
                rows.append((it["id"], p.returncode == 0, (p.stdout + p.stderr)[-140:].strip().replace("\n", " ")))
            except subprocess.TimeoutExpired:
                rows.append((it["id"], None, "timeout"))
    finally:
        shutil.rmtree(d.parent, ignore_errors=True)
    return rows
out = {}
for t in ["T1", "T2", "T3", "T4", "T5"]:
    c = json.load(open(HERE / "author/contracts" / f"{t}.json"))
    base = SCR / ("base-t2" if t == "T2" else "base-main"); ref = HERE / "refruns" / f"ref-{t}"
    b = run_contract(c, base); r = run_contract(c, ref)
    out[t] = {"base": b, "ref": r}
    print(f"{t}: 시작 상태에서 통과한 항목 {sum(1 for x in b if x[1])}/{len(b)} (0이어야 정상 아님: 작업 전이라 실패해야 함) | 정답 해법에서 실패한 항목 {sum(1 for x in r if not x[1])}/{len(r)}")
    for (i, ok, tail), (i2, ok2, tail2) in zip(b, r):
        flag = ("  <- 시작 상태에서 통과" if ok else "") + ("  <- 정답 해법에서 실패" if not ok2 else "")
        if flag: print(f"    {i}: base={ok} ref={ok2}{flag} | ref 출력: {tail2[:100]}")
(HERE / "data").mkdir(exist_ok=True); (HERE / "data/indep_sanity.json").write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str))
