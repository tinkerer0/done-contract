#!/usr/bin/env python3
"""make_report.py: builds the numeric tables for RESULTS.md and results.csv from data/results + data/classification.json (+ data/contract_cross.json)."""
import csv, json, statistics, sys, collections
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import load_results
HERE = Path(__file__).resolve().parent; DATA = HERE / "data"
cls = json.load(open(DATA / "classification.json")); ARMS = ["off", "ask", "contract", "gate"]
rows = [r for r in load_results() if r["arm"] in ARMS]   # the independent-contract arms belong to the second experiment
TOOLS = [("claude-sonnet", "Claude Sonnet 5.5"), ("claude-haiku", "Claude Haiku 4.5"), ("grok", "Grok 4.7"), ("cursor", "Gemini 3.8 Flash (Cursor)")]
def tool(r): return "claude-sonnet" if "sonnet" in r["name"] else "claude-haiku" if "haiku" in r["name"] else "grok" if r["name"].startswith("grok") else "cursor"
def lab(r):
    if r["final_complete"]: return "완료"
    return {"claims_done": "거짓완료", "honest": "정직미완", "strict_only": "엄격만"}.get(cls.get(r["name"]), "분류전")
for r in rows: r["tool"] = tool(r); r["label"] = lab(r)
def mean(xs): xs = [x for x in xs if x is not None]; return statistics.mean(xs) if xs else None
def fmt(x, nd=1): return "-" if x is None else f"{x:.{nd}f}"
out = []
out.append("### 표 1. 도구와 군별 결과\n\n| 도구 | 군 | N | 완료 | 거짓 완료 | 정직한 미완료 | 엄격 판정만 불완전 | 게이트가 막은 횟수 | 평균 턴 | 평균 초 | 평균 비용(달러) |\n|---|---|---|---|---|---|---|---|---|---|---|")
for t, tn in TOOLS:
    for a in ARMS:
        rs = [r for r in rows if r["tool"] == t and r["arm"] == a]
        if not rs: continue
        c = collections.Counter(r["label"] for r in rs)
        turns = mean([r.get("turns") for r in rs]) if t != "cursor" else None
        cost = mean([r.get("cost_usd") for r in rs]) if t != "cursor" else None
        out.append(f"| {tn} | {a} | {len(rs)} | {c['완료']} | {c['거짓완료']} | {c['정직미완']} | {c['엄격만']} | {sum(r['blocks'] for r in rs)} | {fmt(turns)} | {fmt(mean([r['wall_s'] for r in rs]),0)} | {fmt(cost,3)} |")
out.append("\nCursor의 턴과 비용은 측정하지 못해 `-`로 둔다.")
three = [r for r in rows if r["tool"] != "cursor"]
out.append("\n### 표 2. 네 군이 모두 있는 세 도구(Sonnet, Haiku, Grok)를 합친 결과\n\n| 군 | N | 완료 | 거짓 완료 | 정직한 미완료 | 평균 턴 | 평균 비용(달러) |\n|---|---|---|---|---|---|---|")
pooled = {}
for a in ARMS:
    rs = [r for r in three if r["arm"] == a]; c = collections.Counter(r["label"] for r in rs)
    pooled[a] = dict(n=len(rs), done=c["완료"], false=c["거짓완료"], honest=c["정직미완"], turns=mean([r["turns"] for r in rs]), cost=mean([r["cost_usd"] for r in rs]))
    p = pooled[a]; out.append(f"| {a} | {p['n']} | {p['done']} | {p['false']} | {p['honest']} | {fmt(p['turns'])} | {fmt(p['cost'],3)} |")
out.append("\n### 표 3. 과제별 완료 수(모든 도구 합산, 군마다 도구 수가 달라 분모를 같이 적는다)\n\n| 과제 | " + " | ".join(ARMS) + " |\n|---|" + "---|" * len(ARMS))
for tk in ["T1", "T2", "T3", "T4", "T5"]:
    cells = []
    for a in ARMS:
        rs = [r for r in rows if r["task"] == tk and r["arm"] == a]; cells.append(f"{sum(1 for r in rs if r['final_complete'])}/{len(rs)}")
    out.append(f"| {tk} | " + " | ".join(cells) + " |")
out.append("\n### 표 4. 도구별 거짓 완료가 난 과제\n\n| 도구 | 과제별 거짓 완료 수 |\n|---|---|")
for t, tn in TOOLS:
    c = collections.Counter(r["task"] for r in rows if r["tool"] == t and r["label"] == "거짓완료")
    out.append(f"| {tn} | " + (", ".join(f"{k}: {v}" for k, v in sorted(c.items())) or "없음") + " |")
out.append("\n### 표 5. 게이트가 막은 경우\n\n| 실행 | 막은 뒤 결과 | 분류 |\n|---|---|---|")
for r in rows:
    if r["blocks"]:
        for i in range(1, r["blocks"] + 1):
            out.append(f"| {r['name']} | {r['label']} | {cls.get(f'block:{r['name']}:{i}', '미분류')} |")
caps = [r["name"] for r in rows if r.get("capped")]
out.append(f"\n턴 또는 시간 상한에 걸린 실행: {', '.join(caps) if caps else '없음'}")
cp = DATA / "contract_cross.json"
if cp.exists():
    j = json.load(open(cp)); s = j["summary"]; d = j["detail"]
    out.append("\n### 표 6. 에이전트가 쓴 계약의 품질\n\n| 도구 | 군 | 계약 수 | 작업 전에 이미 통과 | 불완전한 결과물을 하나라도 통과시킴 | 비교한 불완전 결과물이 있는 계약 |\n|---|---|---|---|---|---|")
    agg = collections.defaultdict(collections.Counter)
    for rec in d:
        n = rec["owner"]; t = "claude-sonnet" if "sonnet" in n else "claude-haiku" if "haiku" in n else "grok" if n.startswith("grok") else "cursor"
        a = agg[(t, rec["arm"])]; a["n"] += 1; a["base"] += rec["base"] == "accepts"
        bad = [v for v in rec["vs"] if not v["state_is_complete"]]; a["bad"] += bool(bad); a["badacc"] += any(v["contract"] == "accepts" for v in bad)
    tot = collections.Counter()
    for t, tn in TOOLS:
        for arm in ("contract", "gate"):
            a = agg.get((t, arm))
            if a: out.append(f"| {tn} | {arm} | {a['n']} | {a['base']} | {a['badacc']} | {a['bad']} |"); tot.update(a)
    out.append(f"\n합계: 계약 {tot['n']}개, 작업 전 통과 {tot['base']}개, 불완전 결과물 통과 {tot['badacc']}개(비교 가능한 {tot['bad']}개 중 {100*tot['badacc']/max(tot['bad'],1):.0f}%).")
    out.append(f"계약 쌍 기준: 불완전한 결과물 {s['bad_pairs']}쌍 중 {s['bad_accepted']}쌍 통과, 완료된 결과물 {s['good_pairs']}쌍 중 {s['good_rejected']}쌍 거부.")
Path(HERE / "report_tables.md").write_text("\n".join(out) + "\n")
with open(HERE / "results.csv", "w", newline="") as f:
    w = csv.writer(f); w.writerow(["run", "tool", "task", "arm", "rep", "complete_strict", "label", "blocks", "turns", "wall_s", "cost_usd", "capped", "gate_status"])
    for r in sorted(rows, key=lambda r: r["name"]):
        w.writerow([r["name"], r["tool"], r["task"], r["arm"], r.get("rep"), r["final_complete"], r["label"], r["blocks"], r.get("turns") if r["tool"] != "cursor" else "", r["wall_s"], r.get("cost_usd") if r["tool"] != "cursor" else "", r.get("capped"), r.get("gate_status")])
print(json.dumps({"pooled": pooled}, ensure_ascii=False, indent=1, default=lambda x: round(x, 3) if isinstance(x, float) else str(x)))
