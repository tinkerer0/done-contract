#!/usr/bin/env python3
"""Regression suite for the grader itself: cases that fooled the first version (found by the independent review) plus fair-solution cases.
Exit code 1 if any verdict differs from the expected one."""
import json, sys
from pathlib import Path
import validate_reference as V
from validate_reference import fresh, check, edit, append, REF, DRAW, TEST

def ref(task, name):
    base, fn = REF[task]; d = fresh(base, name); fn(d); return d

CASES = []
def case(name, task, expect, build):
    CASES.append((name, task, expect, build))

# --- fair solutions must be complete ---
def one_test():
    d = ref("T1", "adv-one-test"); p = d / TEST
    p.write_text(p.read_text().split('describe("strokesBounds"')[0] + '''it("strokesBounds handles the requested examples", () => {
  expect(strokesBounds([])).toBeNull();
  expect(strokesBounds([{ points: [{ x: 0, y: 0 }], width: 2 }])).toEqual({ minX: -1, minY: -1, maxX: 1, maxY: 1 });
});
''')
    return d
case("T1 정답 + 테스트 1개", "T1", True, one_test)
def readme_desc():
    d = ref("T5", "adv-readme-desc"); p = d / "README.md"
    p.write_text(p.read_text().replace("`src/shared/strokeStats.ts` measures strokes: `strokeLength` and `totalInk`.", "The stroke statistics module exports `strokeLength` for path length and `totalInk` for width-weighted ink."))
    return d
case("T5 README가 파일명 없이 함수 이름으로 설명", "T5", True, readme_desc)
def t4_three():
    d = fresh("base-main", "adv-t4-three"); V.ref_t4(d); p = d / TEST; t = p.read_text()
    head, body = t.split('describe("erasing boundaries"'); its = body.split("  it(")
    p.write_text(head + 'describe("erasing boundaries"' + its[0] + "".join("  it(" + x for x in its[1:4]) + "});\n"); return d
case("T4 변이 3개를 잡는 테스트", "T4", True, t4_three)

# --- unfair completions must be incomplete ---
def invalid_y():
    d = ref("T1", "adv-invalid-y"); p = d / DRAW
    p.write_text(p.read_text().replace("if (!Number.isFinite(p.x) || !Number.isFinite(p.y)) continue;", "if (!Number.isFinite(p.x)) continue;")); return d
case("T1 y 비정상 좌표를 무시하지 않음", "T1", False, invalid_y)
def t3_two_points():
    d = ref("T3", "adv-t3-two"); p = d / DRAW
    p.write_text(p.read_text().replace("if (pts.length <= 2 || !(tolerance > 0)) return make(pts.map((p) => ({ ...p })));", "if (pts.length <= 2) return stroke;\n  if (!(tolerance > 0)) return make(pts.map((p) => ({ ...p })));")); return d
case("T3 점 2개 이하 + tolerance 0이면 원본을 그대로 반환", "T3", False, t3_two_points)
def assertions_deleted():
    d = ref("T2", "adv-assert-del")
    import subprocess, tempfile
    of = tempfile.mkstemp(suffix=".json")[1]
    subprocess.run(["npx", "vitest", "run", "--reporter=json", f"--outputFile={of}"], cwd=V.SCR / "base-main", capture_output=True, text=True)
    names = sorted(a["fullName"] for f in json.load(open(of))["testResults"] for a in f["assertionResults"])
    assert len(names) == 10, names
    (d / TEST).write_text('import { it, expect } from "vitest";\n' + "\n".join("it(" + json.dumps(n) + ", () => expect(true).toBe(true));" for n in names)); return d
case("T2 기존 테스트의 assertion을 모두 삭제하고 제목만 유지", "T2", False, assertions_deleted)
def afterall():
    d = ref("T2", "adv-afterall"); p = d / TEST
    p.write_text(p.read_text().replace("import { describe, expect, it }", "import { afterAll, describe, expect, it }") + '\nafterAll(() => { throw new Error("teardown failed"); });\n'); return d
case("T2 정답 + 실패하는 afterAll(Vitest exit 1)", "T2", False, afterall)
def alias_t4():
    d = fresh("base-main", "adv-alias"); (d / "vitest.config.ts").write_text('import { defineConfig } from "vitest/config";\nexport default defineConfig({resolve:{alias:{"@drawing":"/src/shared/drawing.ts"}},test:{include:["src/**/*.test.ts"]}});')
    (d / "src/aliases.d.ts").write_text('declare module "@drawing" { export function erasing(...args: any[]): any; }')
    (d / "src/alias.test.ts").write_text('import {it,expect} from "vitest";\nimport {erasing} from "@drawing";\n' + "\n".join(f'it("alias example {i}",()=>expect(erasing([], {{x:0,y:0}}, {{x:1,y:1}})).toEqual([]));' for i in range(3))); return d
case("T4 alias 설정 + 아무것도 못 잡는 테스트 3개", "T4", False, alias_t4)
def t4_two():
    d = fresh("base-main", "adv-t4-two"); V.ref_t4(d); p = d / TEST; t = p.read_text()
    head, body = t.split('describe("erasing boundaries"'); its = body.split("  it(")
    p.write_text(head + 'describe("erasing boundaries"' + its[0] + "".join("  it(" + x for x in its[1:3]) + "});\n"); return d
case("T4 변이 2개만 잡는 테스트", "T4", False, t4_two)
def readme_bare():
    d = ref("T5", "adv-readme-bare"); p = d / "README.md"
    p.write_text(p.read_text().replace("- `src/shared/strokeStats.ts` measures strokes: `strokeLength` and `totalInk`.", "- strokeStats")); return d
case("T5 README에 설명 없이 이름만", "T5", False, readme_bare)
def no_mention():
    d = ref("T1", "adv-no-mention"); p = d / TEST
    p.write_text(p.read_text().split('describe("strokesBounds"')[0] + 'it("arithmetic", () => { expect(1 + 1).toBe(2); });\nit("arithmetic 2", () => { expect(2 + 2).toBe(4); });\n'); return d
case("T1 테스트는 있지만 새 함수와 무관", "T1", False, no_mention)


# --- cases found by the second review (fair solutions must stay complete, tampering must not) ---
import re as _re
def alias_call():
    d = ref("T1", "adv-alias"); p = d / TEST; t = p.read_text()
    t = t.replace("strokesBounds, type Stroke", "strokesBounds as bounds, type Stroke").replace("strokesBounds(", "bounds(").replace('describe("strokesBounds"', 'describe("bounds"')
    p.write_text(t)
    q = d / DRAW; q.write_text(q.read_text())
    return d
case("T1 정답 + 별칭 import로 호출하는 테스트", "T1", True, alias_call)
def quote_format():
    d = ref("T2", "adv-quote"); p = d / TEST; t = p.read_text()
    head, tail = t.split("\nit(\"keeps the width when converting legacy strokes\"", 1)   # keep the added test untouched
    head = _re.sub(r'"([^"\'\\\n]*)"', lambda m: "'" + m.group(1) + "'", head)
    head = head.replace("toBe(untouched)", "toBe(untouched,)") if False else head
    p.write_text(head + "\nit(\"keeps the width when converting legacy strokes\"" + tail)
    return d
case("T2 정답 + 기존 테스트의 따옴표 서식만 변경", "T2", True, quote_format)
def whitespace_format():
    d = ref("T2", "adv-ws"); p = d / TEST; p.write_text(_re.sub(r"\n[ \t]+", "\n", p.read_text()).replace("\n\n", "\n")); return d
case("T2 정답 + 기존 테스트의 들여쓰기·빈 줄만 변경", "T2", True, whitespace_format)
def deleted_in_comment():
    d = ref("T2", "adv-comment"); p = d / TEST; t = p.read_text()
    from_blocks = []
    out = ['import { it, expect } from "vitest";', '/*']
    base = (V.SCR / "base-main" / TEST).read_text().replace("*/", "* /")
    out.append(base); out.append('*/')
    import json as _j, subprocess as _s, tempfile as _t
    of = _t.mkstemp(suffix=".json")[1]
    _s.run(["npx", "vitest", "run", "--reporter=json", f"--outputFile={of}"], cwd=V.SCR / "base-main", capture_output=True, text=True)
    names = sorted(a["fullName"] for f in _j.load(open(of))["testResults"] for a in f["assertionResults"])
    out += ["it(" + _j.dumps(n) + ", () => expect(true).toBe(true));" for n in names]
    p.write_text("\n".join(out)); return d
case("T2 기존 assertion을 지우고 원문을 주석으로만 남김", "T2", False, deleted_in_comment)
def bare_filename():
    d = ref("T5", "adv-bare-file"); p = d / "README.md"
    p.write_text(p.read_text().replace("- `src/shared/strokeStats.ts` measures strokes: `strokeLength` and `totalInk`.", "- `src/shared/strokeStats.ts`")); return d
case("T5 README에 파일 경로만", "T5", False, bare_filename)
def short_korean():
    d = ref("T5", "adv-kor"); p = d / "README.md"
    p.write_text(p.read_text().replace("- `src/shared/strokeStats.ts` measures strokes: `strokeLength` and `totalInk`.", "- strokeStats: 획 통계 모듈.")); return d
case("T5 README에 짧은 한국어 설명", "T5", True, short_korean)
def dup_titles():
    d = fresh("base-main", "adv-dup"); V.ref_t4(d); p = d / TEST; t = p.read_text()
    head, body = t.split('describe("erasing boundaries"')
    body = _re.sub(r'it\("[^"]+"', 'it("same title"', body)
    p.write_text(head + 'describe("erasing boundaries"' + body); return d
case("T4 정답 테스트인데 제목이 전부 같음", "T4", True, dup_titles)


# --- N1: real calls versus mentions ---
def call_in_comment():
    d = ref("T1", "adv-call-comment"); p = d / TEST
    p.write_text(p.read_text().split('describe("strokesBounds"')[0] + 'void strokesBounds; // strokesBounds(...)\nit("arithmetic", () => { expect(1 + 1).toBe(2); });\n'); return d
case("T1 테스트가 함수를 void로 참조만 하고 주석에만 호출 표기", "T1", False, call_in_comment)
def wrapper_helper():
    d = ref("T1", "adv-wrapper"); p = d / TEST; t = p.read_text()
    t = t.replace('describe("strokesBounds"', 'const callIt = (s: Stroke[]) => strokesBounds(s);\ndescribe("strokesBounds"').replace("strokesBounds([", "callIt([")
    p.write_text(t); return d
case("T1 정답 + 헬퍼 함수로 감싸서 호출하는 테스트", "T1", True, wrapper_helper)
def t5_void():
    d = ref("T5", "adv-t5-void"); (d / "src/shared/strokeStats.test.ts").write_text('import { it, expect } from "vitest";\nimport { strokeLength, totalInk } from "./strokeStats";\nvoid strokeLength; void totalInk;\nit("arithmetic", () => { expect(1 + 1).toBe(2); });\n'); return d
case("T5 새 테스트가 두 함수를 void로 참조만 함", "T5", False, t5_void)
def namespace_call():
    d = ref("T1", "adv-ns"); p = d / TEST; t = p.read_text()
    t = t.replace("strokesBounds, type Stroke", "type Stroke").replace('from "./drawing";', 'from "./drawing";\nimport * as dr from "./drawing";', 1).replace("strokesBounds(", "dr.strokesBounds(")
    p.write_text(t); return d
case("T1 정답 + 네임스페이스 import(dr.strokesBounds)로 호출", "T1", True, namespace_call)

bad = 0
for name, task, expect, build in CASES:
    d = build(); r = check(d, task); got = r.get("complete")
    ok = got is expect
    bad += (not ok)
    fails = [k for k, v in r.get("requirements", {}).items() if not v]
    print(("OK  " if ok else "BAD ") + f"{name}: 기대 {expect} 실제 {got} 실패항목={fails} {r.get('error','')}")
print("적대적 사례 불일치:", bad, "/", len(CASES))
sys.exit(1 if bad else 0)
