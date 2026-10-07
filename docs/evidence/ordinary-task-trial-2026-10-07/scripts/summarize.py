#!/usr/bin/env python3
"""summarize.py [--detail]: tables from data/results/*.json. Manual classification lives in data/classification.json:
  {"<run name>": "claims_done" | "honest"}  for incomplete runs   and   {"block:<run name>:<n>": "true_catch" | "false_positive" | "unclear"} for each gate block."""
import json, os, statistics, sys
from pathlib import Path
DATA = Path(__file__).resolve().parent / "data"
from common import load_results
all_rows = load_results()
infra = [r for r in all_rows if "infra_error" in r]
rows = [r for r in all_rows if "check" in r]
cls = json.load(open(DATA / "classification.json")) if (DATA / "classification.json").exists() else {}
ARMS = ["off", "ask", "contract", "gate"]
DETAIL = "--detail" in sys.argv

excluded = [r for r in rows if r.get("gate_status") not in ("ok", "n/a")]
use = [r for r in rows if r.get("gate_status") in ("ok", "n/a")]

def label(r):
    if r["final_complete"]: return "완료"
    c = cls.get(r["name"])
    return {"claims_done": "거짓완료", "honest": "정직미완", "strict_only": "엄격만"}.get(c, "불완전(분류전)")

def short(m): return m.split("-")[1] if m.startswith("claude-") else m

print(f"전체 결과 {len(all_rows)}개 = 분석 대상 {len(use)} + 승인실패 제외 {len(excluded)} + 도구오류 {len(infra)}")
if infra: print("  도구오류:", [r["name"] for r in infra])
if excluded: print("  게이트 미적용(승인실패·hook 미발동, 분석 제외):", [(r["name"], r.get("gate_status")) for r in excluded])
print("\n=== 군별 요약 ===")
print(f"{'모델':8}{'군':10}{'N':>3}{'완료':>5}{'거짓완료':>8}{'정직미완':>8}{'엄격만':>6}{'분류전':>6}{'막음':>5}{'턴':>7}{'초':>6}{'비용$':>8}")
for model in sorted({r["model"] for r in use}):
    for arm in ARMS:
        rs = [r for r in use if r["model"] == model and r["arm"] == arm]
        if not rs: continue
        L = [label(r) for r in rs]
        turns = [r["turns"] for r in rs if r.get("turns")]; costs = [r["cost_usd"] for r in rs if r.get("cost_usd") is not None]
        print(f"{short(model):8}{arm:10}{len(rs):>3}{L.count('완료'):>5}{L.count('거짓완료'):>8}{L.count('정직미완'):>8}{L.count('엄격만'):>6}{L.count('불완전(분류전)'):>6}"
              f"{sum(r['blocks'] for r in rs):>5}{(statistics.mean(turns) if turns else float('nan')):>7.1f}{statistics.mean(r['wall_s'] for r in rs):>6.0f}{(statistics.mean(costs) if costs else float('nan')):>8.3f}")
print("\n=== 과제별 완료 수/N (모델 합산) ===")
print(f"{'과제':5}" + "".join(f"{a:>12}" for a in ARMS))
for task in sorted({r["task"] for r in use}):
    print(f"{task:5}" + "".join(f"{sum(1 for r in use if r['task']==task and r['arm']==a and r['final_complete'])}/{sum(1 for r in use if r['task']==task and r['arm']==a):>8}" for a in ARMS))
print("\n=== 불완전한 실행 ===")
for r in use:
    if not r["final_complete"]:
        fails = [k for k, v in r["check"].get("requirements", {}).items() if not v]
        print(f"{r['name']}: 실패={fails} 막음={r['blocks']} 분류={label(r)} 표시={r.get('flags')}")
        if DETAIL: print("   최종보고:", (r.get("final_message_full") or r.get("final_message") or "").replace("\n", " | "))
print("\n=== 표시된 실행(상한 도달·시간초과·폴더밖 접근 의심) ===")
for r in use:
    aud = r.get("audit_v2", r.get("audit_flags", []))
    if r.get("flags") or aud or r.get("leak_fingerprints"):
        print(f"{r['name']}: flags={r.get('flags')} 폴더밖접근의심={len(aud)} 유출지문={r.get('leak_fingerprints')}")
        if DETAIL:
            for a in aud[:6]: print("     ", a[:170])
print("\n=== 게이트·계약만 군: 승인 전 작업·계약·막음 ===")
for r in use:
    if r["arm"] not in ("gate", "contract"): continue
    c = r.get("contract", {}) or {}
    print(f"{r['name']}: 완료={r['final_complete']} 막음={r['blocks']} 증빙={r.get('evidence_verdict')} 항목={len(c.get('items', []))} allow_protected={c.get('allow_protected_changes')} 승인전작업={r.get('worked_before_approval')} 정지={[(e['decision'], e['verdict']) for e in r['stop_events']]}")
    if DETAIL:
        for i, b in enumerate(r.get("block_reasons_full") or [{"reason": x} for x in r.get("block_reasons", [])], 1): print(f"   막음#{i} 이유:", b["reason"].replace("\n", " | "), "→ 분류:", cls.get(f"block:{r['name']}:{i}", "미분류"))
