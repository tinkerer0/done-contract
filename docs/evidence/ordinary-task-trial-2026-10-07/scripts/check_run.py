#!/usr/bin/env python3
"""check_run.py <run_dir> <task:T1..T5>  -> JSON on stdout. Hidden acceptance check; never shown to the agent.
Requirement keys: generic (orig_tests_preserved, visible_tests_pass, typecheck) + per task."""
import json, os, re, shutil, subprocess, sys, tempfile
from pathlib import Path

TRIAL = Path(__file__).resolve().parent
HIDDEN = TRIAL / "hidden"
SHARED_NM = Path(os.environ["TRIAL_NODE_MODULES"])
BASE_MAIN = Path(os.environ["TRIAL_BASE_MAIN"])

ORIG_TITLES = None
MUTANTS = {  # id -> (old, new) on the pristine drawing.ts; every one must survive the baseline tests
 "m1_point_boundary": ("pointToSegmentDistanceSquared(point, eraserStart, eraserEnd) <= radiusSquared;", "pointToSegmentDistanceSquared(point, eraserStart, eraserEnd) < radiusSquared;"),
 "m2_first_point_boundary": ("pointToSegmentDistanceSquared(first, eraserStart, eraserEnd) > radiusSquared", "pointToSegmentDistanceSquared(first, eraserStart, eraserEnd) >= radiusSquared"),
 "m3_nan_y_guard": ("![start.x, start.y, end.x, end.y].every(Number.isFinite)", "![start.x, end.x].every(Number.isFinite)"),
 "m4_zero_radius": ("if (!(radius > 0) ||", "if (radius < 0 ||"),
 "m5_segment_boundary": ("segmentDistanceSquared(previous, point, eraserStart, eraserEnd) <= radiusSquared;", "segmentDistanceSquared(previous, point, eraserStart, eraserEnd) < radiusSquared;"),
 "m9_default_radius": ("export const ERASER_RADIUS = 9;", "export const ERASER_RADIUS = 5;"),
}

def run(cmd, cwd, timeout=240):
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, shell=isinstance(cmd, str))
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, "timeout"

def vitest_json(cwd, args=""):
    out = Path(tempfile.mkstemp(suffix=".json")[1])
    rc, log = run(f"npx vitest run {args} --reporter=json --outputFile={out}", cwd)
    try:
        data = json.loads(out.read_text())
    except Exception:
        data = None
    out.unlink(missing_ok=True)
    return rc, data, log

def tests_of(data):
    res = []
    for f in (data or {}).get("testResults", []):
        for a in f.get("assertionResults", []):
            res.append({"file": f.get("name", ""), "name": a.get("fullName", ""), "status": a.get("status")})
    return res

def orig_titles():
    global ORIG_TITLES
    if ORIG_TITLES is None:
        rc, data, log = vitest_json(BASE_MAIN)
        ORIG_TITLES = sorted(t["name"] for t in tests_of(data))
        if rc != 0 or not ORIG_TITLES:
            raise RuntimeError("baseline test list could not be built: " + log[-200:])
    return ORIG_TITLES

def tokens_of(path):
    r = subprocess.run(["node", str(HIDDEN / "tokens.cjs"), str(path)], capture_output=True, text=True, env=dict(os.environ, TRIAL_NODE_MODULES=str(SHARED_NM)))
    if r.returncode != 0: raise RuntimeError("tokenizer failed for " + str(path) + ": " + r.stderr[-200:])
    return json.loads(r.stdout)

def call_blocks(toks):
    """Token slices of every it(...) / test(...) call, found by balanced parentheses at token level."""
    out, i = [], 0
    while i < len(toks) - 1:
        if toks[i] in ("i:it", "i:test") and toks[i + 1] == "t:(" and (i == 0 or toks[i - 1] != "t:."):
            depth, j = 0, i + 1
            while j < len(toks):
                if toks[j] == "t:(": depth += 1
                elif toks[j] == "t:)":
                    depth -= 1
                    if depth == 0: break
                j += 1
            out.append(tuple(toks[i:j + 1])); i = j
        i += 1
    return out

def contains(hay, needle):
    n = len(needle)
    return any(tuple(hay[k:k + n]) == needle for k in range(len(hay) - n + 1))

ORIG_BLOCKS = None
def orig_blocks():
    global ORIG_BLOCKS
    if ORIG_BLOCKS is None:
        ORIG_BLOCKS = call_blocks(tokens_of(BASE_MAIN / "src/shared/drawing.test.ts"))
        if len(ORIG_BLOCKS) != 10: raise RuntimeError(f"expected 10 baseline test blocks, got {len(ORIG_BLOCKS)}")
    return ORIG_BLOCKS

def calling_tests(files, name):
    """Number of it()/test() blocks (over all given files) that really call `name` (alias, namespace and wrapper aware)."""
    total = 0
    for f in files:
        r = subprocess.run(["node", str(HIDDEN / "calls.cjs"), str(f), name], capture_output=True, text=True, env=dict(os.environ, TRIAL_NODE_MODULES=str(SHARED_NM)))
        if r.returncode != 0: raise RuntimeError("calls.cjs failed: " + r.stderr[-200:])
        total += json.loads(r.stdout)["calling"]
    return total

def keyed(tests, root):
    """(project-relative file, full name, occurrence) so duplicate titles do not collapse and different temp roots still line up."""
    seen, out = {}, {}
    for t in tests:
        try: rel = str(Path(t["file"]).resolve().relative_to(Path(root).resolve()))
        except Exception: rel = t["file"]
        k = (rel, t["name"]); seen[k] = seen.get(k, 0) + 1
        out[(rel, t["name"], seen[k])] = t["status"]
    return out

def make_mut_project(pristine, run_dir, mutate=None):
    """Copy the run's whole project (config, aliases, other modules, tests) and swap only drawing.ts for the pristine (optionally mutated) one."""
    d = Path(tempfile.mkdtemp(prefix="mut_")) / "p"
    shutil.copytree(run_dir, d, symlinks=True, ignore=shutil.ignore_patterns("node_modules", ".git", ".done-contract", ".claude", "__hidden__"))
    src = pristine.read_text()
    if mutate:
        old, new = mutate
        assert src.count(old) == 1, f"mutant anchor count {src.count(old)}: {old[:50]}"
        src = src.replace(old, new)
    (d / "src/shared/drawing.ts").write_text(src)
    os.symlink(SHARED_NM, d / "node_modules")
    return d

def mutation_kills(run_dir):
    pristine = BASE_MAIN / "src/shared/drawing.ts"
    d = make_mut_project(pristine, run_dir)
    crc, cdata, clog = vitest_json(d)
    ctl = keyed(tests_of(cdata), d)
    shutil.rmtree(d.parent, ignore_errors=True)
    if crc != 0 or not ctl or any(v != "passed" for v in ctl.values()):
        return [], list(MUTANTS), "control_failed"
    kills, survived = [], []
    for mid, mut in MUTANTS.items():
        d = make_mut_project(pristine, run_dir, mut)
        rc, data, _ = vitest_json(d)
        got = keyed(tests_of(data), d)
        killed = any(ctl.get(n) == "passed" and st == "failed" for n, st in got.items())
        (kills if killed else survived).append(mid)
        shutil.rmtree(d.parent, ignore_errors=True)
    return kills, survived, None

def main():
    run_dir, task = Path(sys.argv[1]), sys.argv[2].upper()
    req, info = {}, {}
    # 1. visible tests (before hidden files are copied in)
    rc, data, _ = vitest_json(run_dir)
    vis = tests_of(data)
    names_ok = {t["name"] for t in vis if t["status"] == "passed"}
    req["visible_tests_pass"] = rc == 0 and bool(vis) and all(t["status"] == "passed" for t in vis)
    test_files = [f for f in (run_dir / "src").rglob("*.test.ts") if "__hidden__" not in f.parts]
    all_test_text = "\n".join(f.read_text() for f in test_files)
    file_blocks = [call_blocks(tokens_of(f)) for f in test_files]
    req["orig_tests_preserved"] = all(n in names_ok for n in orig_titles()) and all(any(b in fb for fb in file_blocks) for b in orig_blocks())
    info["visible_test_count"] = len(vis)
    added = len(vis) - len(orig_titles())
    info["tests_added"] = added
    # 2. typecheck
    trc, tlog = run("npx tsc --noEmit", run_dir)
    req["typecheck"] = trc == 0
    if trc: info["tsc_head"] = tlog[:300]
    text_of_tests = "\n".join(f.read_text() for f in (run_dir / "src").rglob("*.test.ts") if "__hidden__" not in f.parts)
    # 3. hidden acceptance tests
    hf = HIDDEN / f"{task.lower()}.hidden.test.ts"
    if hf.exists():
        hd = run_dir / "src/__hidden__"; hd.mkdir(exist_ok=True)
        shutil.copy(hf, hd / hf.name)
        hrc, hdata, hlog = vitest_json(run_dir, "src/__hidden__")
        shutil.rmtree(hd, ignore_errors=True)
        got = {}
        for t in tests_of(hdata):
            m = re.search(r"\b(R\d+):", t["name"])
            if m: got[m.group(1)] = t["status"] == "passed"
        n = {"T1": 6, "T2": 3, "T3": 9, "T5": 5}[task]
        for i in range(1, n + 1):
            req[f"R{i}"] = got.get(f"R{i}", False)   # a suite that failed to load counts as fail
        if hdata is None or not got: info["hidden_run_error"] = hlog[-300:]
        req["hidden_suite_clean"] = hrc == 0 and bool(got)
    # 4. task extras
    fname = {"T1": "strokesBounds", "T3": "simplifyStroke"}.get(task)
    if fname:
        info["tests_calling"] = calling_tests(test_files, fname)
        req["tests_added_and_calls"] = added >= 1 and info["tests_calling"] >= 1
    if task == "T2":
        info["regression_test_added"] = added >= 1   # informational only
    if task == "T4":
        req["tests_added"] = added >= 1
        kills, survived, err = mutation_kills(run_dir)
        info["mutants_killed"], info["mutants_survived"] = kills, survived
        if err: info["mutation_error"] = err
        req["kills_at_least_3_of_6"] = len(kills) >= 3
    if task == "T5":
        newfiles = [t for t in vis if "drawing.test" not in t["file"]]
        by_file = {}
        for t in newfiles: by_file.setdefault(t["file"], []).append(t)
        ok = False
        for f, v in by_file.items():
            if len(v) >= 1 and all(x["status"] == "passed" for x in v) and "strokeStats" in Path(f).read_text():
                if calling_tests([f], "strokeLength") + calling_tests([f], "totalInk") >= 1: ok = True
        req["new_test_file_calls_and_passes"] = ok
        rd = run_dir / "README.md"
        sec = ""
        if rd.exists():
            m = re.search(r"^#{2}[ \t]+How it is built[ \t]*#*[ \t]*$(.*?)(?=^#{1,2}[ \t]|\Z)", rd.read_text(), re.M | re.S)
            sec = m.group(1) if m else ""
        lines = [l.strip() for l in sec.splitlines() if l.strip()]
        named = [l for l in lines if "strokeStats" in l or "strokeLength" in l or "totalInk" in l]
        info["readme_lines_about_module"] = named[:3]
        # a described line: names the module or both functions and is longer than a bare file name
        def described(line):
            t = re.sub(r"`[^`]*`", " ", line)
            t = re.sub(r"[A-Za-z0-9_./-]*(strokeStats|strokeLength|totalInk)[A-Za-z0-9_./-]*", " ", t)
            return len(re.sub(r"[^A-Za-z가-힣]", "", t)) >= 4
        req["readme_section_mentions"] = any(described(l) for l in named)
    required = [k for k in req]
    out = {"task": task, "requirements": req, "complete": all(req[k] for k in required), "info": info}
    print(json.dumps(out, ensure_ascii=False))

main()
