"""common.py: load graded results with manual overrides applied (data/manual.json) and the cap rule from the protocol."""
import json, os, re
from pathlib import Path
DATA = Path(__file__).resolve().parent / "data"

def load_results():
    manual = json.load(open(DATA / "manual.json")) if (DATA / "manual.json").exists() else {}
    rows = []
    for p in sorted((DATA / "results").glob("*.json")):
        r = json.load(open(p))
        if "check" in r and r["check"].get("requirements"):
            req = dict(r["check"]["requirements"]); ov = manual.get(r["name"], {})
            req.update(ov)
            r["check"] = dict(r["check"], requirements=req, complete=all(req.values()), manual_override=sorted(ov))
            capped = any(f in (r.get("flags") or []) for f in ("timeout", "max_turns"))
            r["capped"] = capped
            r["final_complete"] = r["check"]["complete"] and not capped      # protocol: runs that hit a turn or time cap count as incomplete
        rows.append(r)
    return rows

TOOL = os.environ.get("TRIAL_TOOL_BIN", "")
# distinctive strings from the hidden tests and the experiment's own files: seeing them means the agent saw grading material
FINGERPRINTS = ["margin is half the explicit width", "ignored entirely, whichever coordinate", "always returns a distinct copy", "totalInk is length times width",
                "stroke width survives the conversion", "m1_point_boundary", "kills_at_least_3_of_6", "hidden.test", "check_run", "refruns", "work/trial-2026",
                "claude-sonnet-T", "claude-haiku-T", "validate_reference", "data-invalid"]
ROOTS = ("/Users/", "/private/", "/var/", "/tmp/", "/home/", "/Volumes/", "/etc/")
ALLOWED = ("~/.claude",)

def events(path):
    out = []
    for l in open(path):
        try: out.append(json.loads(l))
        except Exception: pass
    return out

def tool_uses(ev):
    for e in ev:
        if e.get("type") == "assistant":
            for b in (e.get("message", {}).get("content") or []):
                if isinstance(b, dict) and b.get("type") == "tool_use": yield b

def inside(p, rd):
    try: return Path(os.path.normpath(os.path.join(rd, p))).is_relative_to(rd)
    except Exception: return False

def audit2(ev, rd):
    flags = []
    for b in tool_uses(ev):
        inp = b.get("input") or {}; nm = b.get("name")
        for k in ("file_path", "path", "notebook_path"):
            v = inp.get(k)
            if isinstance(v, str) and v and not inside(v, rd) and not v.startswith(ALLOWED):
                flags.append(f"{nm}.{k} outside run dir: {v[:120]}")
        for k in ("pattern", "glob"):
            v = inp.get(k)
            if isinstance(v, str) and v.startswith("/") and not v.startswith(rd):
                flags.append(f"{nm}.{k} absolute outside: {v[:120]}")
        if nm == "Bash":
            c = (inp.get("command") or "").replace(rd, "<RUN>")
            if TOOL: c = c.replace(str(Path(TOOL).parent), "<TOOL>")
            for m in re.finditer(r"(?<![\w$.])(/[\w.@+~-]+(?:/[\w.@+~-]*)*)", c):
                path = m.group(1)
                if path.startswith(ROOTS) and not path.startswith(ALLOWED): flags.append(f"Bash absolute path {path[:110]}")
            if re.search(r"(^|[\s;&|=\"'(])\.\./|\bcd\s+\.\.(/|\s|$)|(^|[\s\"'=])~(/|\s|$)|\$HOME|\$\{HOME\}", c):
                flags.append(f"Bash relative/home escape: {c[:120]}")
    seen, out = set(), []
    for f in flags:
        if f not in seen: seen.add(f); out.append(f)
    return out[:20]

