#!/usr/bin/env python3
"""Snapshot wrapper: on `hook stop` it copies the project as the hook sees it, runs the real tool, and records the decision.
Every other invocation is passed straight through."""
import json, os, shutil, subprocess, sys, time
REAL = os.environ.get("TRIAL_REAL_BIN")
if not REAL:
    sys.stderr.write("TRIAL_REAL_BIN not set\n"); sys.exit(97)
args = sys.argv[1:]
if args[:2] != ["hook", "stop"]:
    os.execv(REAL, [REAL] + args)
data = sys.stdin.buffer.read()
run, snap = os.environ.get("TRIAL_RUN_DIR"), os.environ.get("TRIAL_SNAP_DIR")
dest = None
if run and snap and os.path.isdir(run):
    os.makedirs(snap, exist_ok=True)
    n = len([x for x in os.listdir(snap) if x[:2].isdigit()])
    dest = os.path.join(snap, f"{n:02d}")
    try:
        shutil.copytree(run, dest, symlinks=True, ignore=shutil.ignore_patterns("node_modules", ".git", ".claude"))
    except Exception as e:
        sys.stderr.write(f"snapshot failed: {e}\n"); dest = None
p = subprocess.run([REAL] + args, input=data, capture_output=True)
if dest:
    out = p.stdout.decode("utf-8", "replace")
    dec = "block" if '"block"' in out else ("allow" if p.returncode == 0 else f"rc{p.returncode}")
    try: payload = json.loads(data.decode("utf-8", "replace") or "{}")
    except Exception: payload = {}
    json.dump({"decision": dec, "rc": p.returncode, "ts": time.time(), "stop_hook_active": payload.get("stop_hook_active"),
               "reason": payload.get("reason") or payload.get("hook_event_name")}, open(os.path.join(dest, "_stop.json"), "w"))
sys.stdout.buffer.write(p.stdout); sys.stderr.buffer.write(p.stderr)
sys.exit(p.returncode)
