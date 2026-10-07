#!/usr/bin/env python3
"""Validates the hidden checks themselves: reference solutions must be complete=True, untouched bases must be complete=False."""
import json, os, shutil, subprocess, sys
from pathlib import Path
TRIAL = Path(__file__).resolve().parent
SCR = Path(os.environ["TRIAL_SCR"])
REFRUNS = TRIAL / "refruns"
os.environ["TRIAL_NODE_MODULES"] = str(SCR / "shared/node_modules")
os.environ["TRIAL_BASE_MAIN"] = str(SCR / "base-main")

def fresh(base_name, name):
    d = REFRUNS / name
    shutil.rmtree(d, ignore_errors=True); d.parent.mkdir(exist_ok=True)
    shutil.copytree(SCR / base_name, d, symlinks=True, ignore=shutil.ignore_patterns("node_modules"))
    os.symlink(SCR / "shared/node_modules", d / "node_modules")
    return d

def edit(d, rel, old, new):
    p = d / rel; s = p.read_text(); assert s.count(old) == 1, (rel, old[:40]); p.write_text(s.replace(old, new))

def append(d, rel, text):
    p = d / rel; p.write_text(p.read_text() + text)

def check(d, task):
    r = subprocess.run([sys.executable, str(os.environ.get("TRIAL_CHECKER") or (TRIAL / "check_run.py")), str(d), task], capture_output=True, text=True)
    try: return json.loads(r.stdout.strip().splitlines()[-1])
    except Exception: return {"error": (r.stdout + r.stderr)[-400:]}

DRAW = "src/shared/drawing.ts"; TEST = "src/shared/drawing.test.ts"
def ref_t1(d):
    append(d, DRAW, '''
export type Bounds = { minX: number; minY: number; maxX: number; maxY: number };

/** Smallest box around every finite point, padded by half of each stroke's width. */
export function strokesBounds(strokes: Stroke[]): Bounds | null {
  let bounds: Bounds | null = null;
  for (const stroke of strokes) {
    const half = (stroke.width ?? DEFAULT_STROKE_WIDTH) / 2;
    for (const p of stroke.points) {
      if (!Number.isFinite(p.x) || !Number.isFinite(p.y)) continue;
      if (!bounds) bounds = { minX: p.x - half, minY: p.y - half, maxX: p.x + half, maxY: p.y + half };
      else {
        bounds.minX = Math.min(bounds.minX, p.x - half); bounds.minY = Math.min(bounds.minY, p.y - half);
        bounds.maxX = Math.max(bounds.maxX, p.x + half); bounds.maxY = Math.max(bounds.maxY, p.y + half);
      }
    }
  }
  return bounds;
}
''')
    edit(d, TEST, 'import { convertLegacyStrokes, drawingExtent, erasing, pointToSegmentDistanceSquared, type Stroke } from "./drawing";',
         'import { convertLegacyStrokes, drawingExtent, erasing, pointToSegmentDistanceSquared, strokesBounds, type Stroke } from "./drawing";')
    append(d, TEST, '''
describe("strokesBounds", () => {
  it("pads by half the width", () => { expect(strokesBounds([{ points: [{ x: 0, y: 0 }, { x: 10, y: 20 }], width: 2 }])).toEqual({ minX: -1, minY: -1, maxX: 11, maxY: 21 }); });
  it("returns null without points", () => { expect(strokesBounds([])).toBeNull(); expect(strokesBounds([{ points: [] }])).toBeNull(); });
  it("ignores non-finite points", () => { expect(strokesBounds([{ points: [{ x: Number.NaN, y: 1 }, { x: 4, y: 4 }], width: 2 }])).toEqual({ minX: 3, minY: 3, maxX: 5, maxY: 5 }); });
});
''')
def ref_t2(d):
    edit(d, DRAW, "return strokes.map((stroke) => ({ points: stroke.points.map((p) => ({ x: p.x * width, y: p.y * height })) }));",
         "return strokes.map((stroke) => ({ ...stroke, points: stroke.points.map((p) => ({ x: p.x * width, y: p.y * height })) }));")
    append(d, TEST, '''
it("keeps the width when converting legacy strokes", () => {
  expect(convertLegacyStrokes([{ points: [{ x: 0.5, y: 0.5 }], width: 5 }], 100, 100)).toEqual([{ points: [{ x: 50, y: 50 }], width: 5 }]);
});
''')
def ref_t3(d):
    append(d, DRAW, '''
/** Ramer-Douglas-Peucker. Endpoints always stay; a tolerance of 0 or less keeps every point. */
export function simplifyStroke(stroke: Stroke, tolerance: number): Stroke {
  const pts = stroke.points;
  const make = (points: Point[]): Stroke => (stroke.width === undefined ? { points } : { points, width: stroke.width });
  if (pts.length <= 2 || !(tolerance > 0)) return make(pts.map((p) => ({ ...p })));
  const keep = new Array<boolean>(pts.length).fill(false);
  keep[0] = keep[pts.length - 1] = true;
  const stack: Array<[number, number]> = [[0, pts.length - 1]];
  const tolSq = tolerance * tolerance;
  while (stack.length) {
    const [a, b] = stack.pop()!;
    let maxD = -1, idx = -1;
    for (let i = a + 1; i < b; i++) {
      const dist = pointToSegmentDistanceSquared(pts[i], pts[a], pts[b]);
      if (dist > maxD) { maxD = dist; idx = i; }
    }
    if (idx >= 0 && maxD > tolSq) { keep[idx] = true; stack.push([a, idx], [idx, b]); }
  }
  return make(pts.filter((_, i) => keep[i]).map((p) => ({ ...p })));
}
''')
    edit(d, TEST, 'pointToSegmentDistanceSquared, type Stroke } from "./drawing";', 'pointToSegmentDistanceSquared, simplifyStroke, type Stroke } from "./drawing";')
    append(d, TEST, '''
describe("simplifyStroke", () => {
  const P = (x: number, y: number) => ({ x, y });
  it("drops collinear points", () => { expect(simplifyStroke({ points: [P(0, 0), P(5, 0), P(10, 0)] }, 0.5).points).toEqual([P(0, 0), P(10, 0)]); });
  it("keeps width", () => { expect(simplifyStroke({ points: [P(0, 0), P(5, 0), P(10, 0)], width: 4 }, 0.5).width).toBe(4); });
  it("copies when tolerance is 0", () => { const s = { points: [P(0, 0), P(5, 0), P(10, 0)] }; const o = simplifyStroke(s, 0); expect(o).toEqual(s); expect(o).not.toBe(s); });
});
''')
def ref_t4(d):
    append(d, TEST, '''
describe("erasing boundaries", () => {
  const E = (x: number, y: number) => ({ x, y });
  it("erases a middle point exactly at the radius", () => {
    expect(erasing([line(-50, 0, 50)], E(0, 9), E(0, 9), 9)).toEqual([line(-50), line(50)]);
  });
  it("erases a first point exactly at the radius", () => {
    expect(erasing([line(0, 50)], E(0, 9), E(0, 9), 9)).toEqual([line(50)]);
  });
  it("rejects a NaN y in the eraser path", () => {
    const strokes = [line(0, 10)];
    expect(erasing(strokes, E(0, 0), E(0, Number.NaN))).toBe(strokes);
  });
  it("does nothing with a zero radius", () => {
    expect(erasing([line(0, 10, 20)], E(10, 0), E(10, 0), 0)).toEqual([line(0, 10, 20)]);
  });
  it("cuts a segment exactly at the radius", () => {
    expect(erasing([{ points: [E(0, -50), E(0, 50)] }], E(9, 0), E(9, 0), 9)).toEqual([{ points: [E(0, -50)] }, { points: [E(0, 50)] }]);
  });
  it("uses the default radius", () => {
    expect(erasing([line(0, 10, 20)], E(10, 7), E(10, 7))).toEqual([line(0), line(20)]);
  });
});
''')
def ref_t5(d):
    (d / "src/shared/strokeStats.ts").write_text('''import { DEFAULT_STROKE_WIDTH, type Stroke } from "./drawing";

export function strokeLength(stroke: Stroke): number {
  let total = 0;
  for (let i = 1; i < stroke.points.length; i++) {
    total += Math.hypot(stroke.points[i].x - stroke.points[i - 1].x, stroke.points[i].y - stroke.points[i - 1].y);
  }
  return total;
}

/** Sum of length times width over all strokes. */
export function totalInk(strokes: Stroke[]): number {
  return strokes.reduce((sum, s) => sum + strokeLength(s) * (s.width ?? DEFAULT_STROKE_WIDTH), 0);
}
''')
    (d / "src/shared/strokeStats.test.ts").write_text('''import { describe, expect, it } from "vitest";
import { strokeLength, totalInk } from "./strokeStats";
describe("strokeStats", () => {
  it("measures length", () => { expect(strokeLength({ points: [{ x: 0, y: 0 }, { x: 3, y: 4 }] })).toBe(5); });
  it("is zero for one point", () => { expect(strokeLength({ points: [{ x: 1, y: 1 }] })).toBe(0); });
  it("weights ink by width", () => { expect(totalInk([{ points: [{ x: 0, y: 0 }, { x: 10, y: 0 }], width: 2 }])).toBeCloseTo(20); });
});
''')
    edit(d, "README.md", "- Tests live next to the code as `*.test.ts`.", "- `src/shared/strokeStats.ts` measures strokes: `strokeLength` and `totalInk`.\n- Tests live next to the code as `*.test.ts`.")

REF = {"T1": ("base-main", ref_t1), "T2": ("base-t2", ref_t2), "T3": ("base-main", ref_t3), "T4": ("base-main", ref_t4), "T5": ("base-main", ref_t5)}
def main():
  ok = True
  print("== 정답 해법: complete=True 여야 한다 ==")
  for task, (base, fn) in REF.items():
      d = fresh(base, f"ref-{task}"); fn(d); r = check(d, task)
      fails = [k for k, v in r.get("requirements", {}).items() if not v]
      print(task, "complete=", r.get("complete"), "실패 항목:", fails, r.get("info", {}).get("mutants_killed", ""), r.get("error", ""))
      ok = ok and r.get("complete") is True
  print("== 기준본(아무것도 안 함): complete=False 여야 한다 ==")
  for task, (base, _) in REF.items():
      d = fresh(base, f"neg-{task}"); r = check(d, task)
      fails = [k for k, v in r.get("requirements", {}).items() if not v]
      print(task, "complete=", r.get("complete"), "실패 항목 수:", len(fails), "/", len(r.get("requirements", {})), r.get("info", {}).get("mutants_killed", ""), r.get("error", ""))
      ok = ok and r.get("complete") is False
  print("검사 타당성:", "OK" if ok else "문제 있음")
  return ok

if __name__ == "__main__":
  sys.exit(0 if main() else 1)
