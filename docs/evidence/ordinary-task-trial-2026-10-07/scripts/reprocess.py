#!/usr/bin/env python3
"""reprocess.py: recompute audit, leak, block-reason and final-message fields for every Claude run from its raw streams (data/logs/<name>*.jsonl)."""
import glob, json, os, re, sys
from pathlib import Path
TRIAL = Path(__file__).resolve().parent
DATA = TRIAL / "data"
TOOL = os.environ.get("TRIAL_TOOL_BIN", "")
from common import FINGERPRINTS, audit2, events, tool_uses, inside
def final_text(ev):
    res = next((e for e in reversed(ev) if e.get("type") == "result"), {})
    if res.get("result"): return res["result"]
    for e in reversed(ev):
        if e.get("type") == "assistant":
            parts = [b.get("text", "") for b in (e.get("message", {}).get("content") or []) if isinstance(b, dict) and b.get("type") == "text"]
            if any(parts): return "\n".join(parts)
    return ""

def block_reasons(ev):
    out = []
    for e in ev:
        if e.get("subtype") == "hook_response" and e.get("hook_event") == "Stop":
            so = e.get("stdout") or ""
            if '"block"' in so:
                try: out.append(str(json.loads(so).get("reason")))
                except Exception: out.append(so)
    return out

n = 0
ONLY = os.environ.get("ONLY", "claude-")
for f in sorted((DATA / "results").glob(ONLY + "*.json")):
    m = json.load(open(f))
    if "run_dir" not in m: continue
    rd = m["run_dir"]; name = m["name"]
    streams = sorted(glob.glob(str(DATA / "logs" / f"{name}.p*.jsonl")))
    if not streams: continue
    audit, leaks, reasons, last_ev, raw_all = [], [], [], None, ""
    for s in streams:                                   # p0 / p1 / p1fix1 / p2 in file-name order == call order
        ev = events(s); raw = open(s).read(); raw_all += raw
        audit += audit2(ev, rd)
        for r in block_reasons(ev): reasons.append({"phase": Path(s).name.split(".")[-2], "reason": r})
        last_ev = ev
    # call order: p0 | p1, p1fix*, p2  -> the last phase is p2 if present else the last file
    order = sorted(streams, key=lambda s: ({"p0": 0, "p1": 1}.get(Path(s).name.split(".")[-2], 2 if "fix" in Path(s).name else 3), s))
    last_ev = events(order[-1])
    leaks = sorted({fp for fp in FINGERPRINTS if fp in raw_all})
    m["audit_v2"] = audit[:20]
    m["leak_fingerprints"] = leaks
    m["block_reasons_full"] = reasons
    m["final_message_full"] = final_text(last_ev)
    f.write_text(json.dumps(m, ensure_ascii=False, indent=1)); n += 1
print(f"재처리한 실행: {n}")
