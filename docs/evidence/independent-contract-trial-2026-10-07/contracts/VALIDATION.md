# 계약 자기 검증

프로젝트 원본은 변경하지 않았고, 각 과제를 `scratch/Tn` 복사본에서 실행했다. 명령은 계약 JSON의 `check` 문자열 그대로 실행했다. 모든 명령은 해당 복사본 루트 기준이다. `repo1`은 `npx --no-install vitest run`, `repo2`는 `npx --no-install tsc --noEmit`이다. 설치·외부 모델 호출은 하지 않았다.

시작 상태는 각 과제의 새 요구사항 항목에서 실패해야 하며 이미 충족하는 기존 동작·보존 항목은 통과할 수 있다. 정상 구현은 모든 items와 repo_checks가 0이어야 한다. 의도적 위반은 해당 항목의 비영 종료를 확인했다.

## T1

정상 구현: 유효 점마다 반 굵기 여백을 합쳐 최소 경계를 계산하고 테스트 2개를 추가했다.

| 상태 | 항목 | 종료 코드 | 결정적 출력 |
|---|---|---:|---|
| base | Q1 | 1 | AssertionError [ERR_ASSERTION]: Expected values to be strictly equal: |
| base | Q2 | 1 | TypeError: m.strokesBounds is not a function |
| base | Q3 | 1 | TypeError: m.strokesBounds is not a function |
| base | Q4 | 1 | TypeError: m.strokesBounds is not a function |
| base | Q5 | 1 | TypeError: m.strokesBounds is not a function |
| base | Q6 | 1 | AssertionError [ERR_ASSERTION]: additional executed tests required |
| base | repo1 | 0 | Tests  10 passed (10) |
| base | repo2 | 0 | (출력 없음) |
| good | Q1 | 0 | PASS |
| good | Q2 | 0 | PASS |
| good | Q3 | 0 | PASS |
| good | Q4 | 0 | PASS |
| good | Q5 | 0 | PASS |
| good | Q6 | 0 | PASS: added tests run and detect target behavior changes |
| good | repo1 | 0 | Tests  12 passed (12) |
| good | repo2 | 0 | (출력 없음) |
| bad-no-width-margin | Q3 | 1 | AssertionError [ERR_ASSERTION]: Expected values to be strictly deep-equal: |
| bad-unrelated-added-test | Q6 | 1 | AssertionError [ERR_ASSERTION]: new tests must detect wrong strokesBounds behavior |

### 실행 명령

아래는 실제 실행된 인라인 계약 명령이다. 동일 항목은 상태마다 같은 명령을 사용했다.

`Q1`

```sh
node --input-type=module -e 'import assert from "node:assert/strict";
import {registerHooks} from "node:module";registerHooks({resolve(s,c,next){try{return next(s,c);}catch(e){if((s.startsWith("./")||s.startsWith("../"))&&e.code==="ERR_MODULE_NOT_FOUND"){for(const v of [s+".ts",s.replace(/\.js$/,".ts"),s+"/index.ts"]){try{return next(v,c);}catch{}}}throw e;}}});
const m=await import("./src/shared/drawing.ts");
assert.equal(typeof m.strokesBounds,"function");
console.log("PASS");'
```

`Q2`

```sh
node --input-type=module -e 'import assert from "node:assert/strict";
import {registerHooks} from "node:module";registerHooks({resolve(s,c,next){try{return next(s,c);}catch(e){if((s.startsWith("./")||s.startsWith("../"))&&e.code==="ERR_MODULE_NOT_FOUND"){for(const v of [s+".ts",s.replace(/\.js$/,".ts"),s+"/index.ts"]){try{return next(v,c);}catch{}}}throw e;}}});
const m=await import("./src/shared/drawing.ts");
for(const points of [[{x:7,y:-3}],[{x:-8,y:6},{x:4,y:-2},{x:1,y:1}]]){const strokes=points.map(p=>({points:[p],width:0}));assert.deepEqual(m.strokesBounds(strokes),{minX:Math.min(...points.map(p=>p.x)),minY:Math.min(...points.map(p=>p.y)),maxX:Math.max(...points.map(p=>p.x)),maxY:Math.max(...points.map(p=>p.y))});}
console.log("PASS");'
```

`Q3`

```sh
node --input-type=module -e 'import assert from "node:assert/strict";
import {registerHooks} from "node:module";registerHooks({resolve(s,c,next){try{return next(s,c);}catch(e){if((s.startsWith("./")||s.startsWith("../"))&&e.code==="ERR_MODULE_NOT_FOUND"){for(const v of [s+".ts",s.replace(/\.js$/,".ts"),s+"/index.ts"]){try{return next(v,c);}catch{}}}throw e;}}});
const m=await import("./src/shared/drawing.ts");
const strokes=[{points:[{x:0,y:0}],width:10},{points:[{x:10,y:3}],width:2}];assert.deepEqual(m.strokesBounds(strokes),{minX:-5,minY:-5,maxX:11,maxY:5});const h=m.DEFAULT_STROKE_WIDTH/2;assert.deepEqual(m.strokesBounds([{points:[{x:2,y:3}]}]),{minX:2-h,minY:3-h,maxX:2+h,maxY:3+h});
console.log("PASS");'
```

`Q4`

```sh
node --input-type=module -e 'import assert from "node:assert/strict";
import {registerHooks} from "node:module";registerHooks({resolve(s,c,next){try{return next(s,c);}catch(e){if((s.startsWith("./")||s.startsWith("../"))&&e.code==="ERR_MODULE_NOT_FOUND"){for(const v of [s+".ts",s.replace(/\.js$/,".ts"),s+"/index.ts"]){try{return next(v,c);}catch{}}}throw e;}}});
const m=await import("./src/shared/drawing.ts");
for(const s of [[],[{points:[]}],[{points:[],width:100},{points:[]}]])assert.equal(m.strokesBounds(s),null);
console.log("PASS");'
```

`Q5`

```sh
node --input-type=module -e 'import assert from "node:assert/strict";
import {registerHooks} from "node:module";registerHooks({resolve(s,c,next){try{return next(s,c);}catch(e){if((s.startsWith("./")||s.startsWith("../"))&&e.code==="ERR_MODULE_NOT_FOUND"){for(const v of [s+".ts",s.replace(/\.js$/,".ts"),s+"/index.ts"]){try{return next(v,c);}catch{}}}throw e;}}});
const m=await import("./src/shared/drawing.ts");
const bad=[];for(const n of [NaN,Infinity,-Infinity]){bad.push({x:n,y:99999},{x:-99999,y:n});}assert.equal(m.strokesBounds([{points:bad,width:10}]),null);assert.deepEqual(m.strokesBounds([{points:[...bad,{x:4,y:7}],width:2},{points:[{x:Infinity,y:0}],width:2000}]),{minX:3,minY:6,maxX:5,maxY:8});
console.log("PASS");'
```

`Q6`

```sh
node --input-type=module -e 'import assert from "node:assert/strict";
import path from "node:path";
import {startVitest} from "vitest/node";
const originalNames = new Set(["erasing > keeps strokes the eraser does not touch", "erasing > splits a stroke around an erased middle point", "erasing > removes a stroke whose every point is erased", "erasing > cuts a segment the eraser path crosses even when both ends survive", "erasing > ignores non-finite eraser input", "erasing > drops empty strokes", "geometry helpers > measures distance to a segment", "geometry helpers > converts legacy normalized strokes with the canvas size", "geometry helpers > reports the lowest drawn point plus half the line and a margin", "geometry helpers > keeps the stroke width on every erased piece"]);
function flatten(files){const all=[];function visit(t){if(t.type==="test")all.push(t);for(const c of t.tasks??[])visit(c);}files.forEach(visit);return all;}
async function run(plugins=[]){const ctx=await startVitest("test",[],{watch:false,reporters:[{onTestRunEnd(){}}],silent:true,passWithNoTests:false},{plugins});assert.ok(ctx,"Vitest started");const tests=flatten(ctx.state.getFiles());const errors=ctx.state.getUnhandledErrors();await ctx.close();process.exitCode=0;return {tests,errors};}
const clean=await run();assert.equal(clean.errors.length,0,"no unhandled suite errors");assert.ok(clean.tests.length>10,"additional executed tests required");assert.ok(clean.tests.every(t=>t.result?.state==="pass"),"all tests must run and pass (no skips/todos)");
const added=new Set(clean.tests.filter(t=>!originalNames.has(t.fullTestName)).map(t=>t.fullName));assert.ok(added.size,"a new test is required");
for(const [file,name,change] of [["src/shared/drawing.ts", "strokesBounds", "return value===null?{minX:0,minY:0,maxX:0,maxY:0}:null;"]]){
 const target=path.resolve(file);const virtual="\0contract-probe-"+name;
 const plugin={name:"contract-probe-"+name,enforce:"pre",async resolveId(source,importer){if(source.includes("contract-original"))return;const r=await this.resolve(source,importer,{skipSelf:true});if(r?.id===target)return virtual;},load(id){if(id===virtual){const real=JSON.stringify(target+"?contract-original");return `export * from ${real};import * as actual from ${real};export function ${name}(...args){const value=actual[${JSON.stringify(name)}](...args);${change}}`;}}};
 const mutated=await run([plugin]);assert.equal(mutated.errors.length,0,"mutation must not cause infrastructure errors");assert.ok(mutated.tests.some(t=>added.has(t.fullName)&&t.result?.state==="fail"),"new tests must detect wrong "+name+" behavior");
}
console.log("PASS: added tests run and detect target behavior changes");
'
```

`repo1`

```sh
npx --no-install vitest run
```

`repo2`

```sh
npx --no-install tsc --noEmit
```

## T2

정상 구현: 좌표 변환 결과에 원래 stroke 속성을 보존했다.

| 상태 | 항목 | 종료 코드 | 결정적 출력 |
|---|---|---:|---|
| base | Q1 | 1 | AssertionError [ERR_ASSERTION]: Expected values to be strictly equal: |
| base | Q2 | 0 | PASS |
| base | repo1 | 0 | Tests  10 passed (10) |
| base | repo2 | 0 | (출력 없음) |
| good | Q1 | 0 | PASS |
| good | Q2 | 0 | PASS |
| good | repo1 | 0 | Tests  10 passed (10) |
| good | repo2 | 0 | (출력 없음) |
| bad-lost-width | Q1 | 1 | AssertionError [ERR_ASSERTION]: Expected values to be strictly equal: |

### 실행 명령

아래는 실제 실행된 인라인 계약 명령이다. 동일 항목은 상태마다 같은 명령을 사용했다.

`Q1`

```sh
node --input-type=module -e 'import assert from "node:assert/strict";
import {registerHooks} from "node:module";registerHooks({resolve(s,c,next){try{return next(s,c);}catch(e){if((s.startsWith("./")||s.startsWith("../"))&&e.code==="ERR_MODULE_NOT_FOUND"){for(const v of [s+".ts",s.replace(/\.js$/,".ts"),s+"/index.ts"]){try{return next(v,c);}catch{}}}throw e;}}});
const m=await import("./src/shared/drawing.ts");
const input=[0,1.5,2.75,5,8.25].map(width=>({points:[{x:0.25,y:0.75}],width}));input.push({points:[],width:11});const out=m.convertLegacyStrokes(input,320,180);assert.equal(out.length,input.length);for(let i=0;i<input.length;i++)assert.equal(out[i].width,input[i].width);
console.log("PASS");'
```

`Q2`

```sh
node --input-type=module -e 'import assert from "node:assert/strict";
import {registerHooks} from "node:module";registerHooks({resolve(s,c,next){try{return next(s,c);}catch(e){if((s.startsWith("./")||s.startsWith("../"))&&e.code==="ERR_MODULE_NOT_FOUND"){for(const v of [s+".ts",s.replace(/\.js$/,".ts"),s+"/index.ts"]){try{return next(v,c);}catch{}}}throw e;}}});
const m=await import("./src/shared/drawing.ts");
const input=[{points:[{x:0,y:0},{x:0.25,y:0.75},{x:1,y:1}],width:5},{points:[{x:0.5,y:0.5}]}];const snapshot=structuredClone(input);const out=m.convertLegacyStrokes(input,320,180);assert.deepEqual(out[0].points,[{x:0,y:0},{x:80,y:135},{x:320,y:180}]);assert.deepEqual(out[1].points,[{x:160,y:90}]);assert.equal(out[1].width??m.DEFAULT_STROKE_WIDTH,m.DEFAULT_STROKE_WIDTH);assert.deepEqual(input,snapshot);
console.log("PASS");'
```

`repo1`

```sh
npx --no-install vitest run
```

`repo2`

```sh
npx --no-install tsc --noEmit
```

## T3

정상 구현: RDP 방식으로 구현하고 0 이하에서 새 Stroke 객체를 반환하며 테스트 2개를 추가했다.

| 상태 | 항목 | 종료 코드 | 결정적 출력 |
|---|---|---:|---|
| base | Q1 | 1 | AssertionError [ERR_ASSERTION]: Expected values to be strictly equal: |
| base | Q2 | 1 | TypeError: m.simplifyStroke is not a function |
| base | Q3 | 1 | TypeError: m.simplifyStroke is not a function |
| base | Q4 | 1 | TypeError: m.simplifyStroke is not a function |
| base | Q5 | 1 | TypeError: m.simplifyStroke is not a function |
| base | Q6 | 1 | TypeError: m.simplifyStroke is not a function |
| base | Q7 | 1 | AssertionError [ERR_ASSERTION]: additional executed tests required |
| base | repo1 | 0 | Tests  10 passed (10) |
| base | repo2 | 0 | (출력 없음) |
| good | Q1 | 0 | PASS |
| good | Q2 | 0 | PASS |
| good | Q3 | 0 | PASS |
| good | Q4 | 0 | PASS |
| good | Q5 | 0 | PASS |
| good | Q6 | 0 | PASS |
| good | Q7 | 0 | PASS: added tests run and detect target behavior changes |
| good | repo1 | 0 | Tests  12 passed (12) |
| good | repo2 | 0 | (출력 없음) |
| bad-same-object-zero-tolerance | Q5 | 1 | AssertionError [ERR_ASSERTION]: stroke copy required |
| bad-unrelated-added-test | Q7 | 1 | AssertionError [ERR_ASSERTION]: new tests must detect wrong simplifyStroke behavior |

### 실행 명령

아래는 실제 실행된 인라인 계약 명령이다. 동일 항목은 상태마다 같은 명령을 사용했다.

`Q1`

```sh
node --input-type=module -e 'import assert from "node:assert/strict";
import {registerHooks} from "node:module";registerHooks({resolve(s,c,next){try{return next(s,c);}catch(e){if((s.startsWith("./")||s.startsWith("../"))&&e.code==="ERR_MODULE_NOT_FOUND"){for(const v of [s+".ts",s.replace(/\.js$/,".ts"),s+"/index.ts"]){try{return next(v,c);}catch{}}}throw e;}}});
const m=await import("./src/shared/drawing.ts");
assert.equal(typeof m.simplifyStroke,"function");
console.log("PASS");'
```

`Q2`

```sh
node --input-type=module -e 'import assert from "node:assert/strict";
import {registerHooks} from "node:module";registerHooks({resolve(s,c,next){try{return next(s,c);}catch(e){if((s.startsWith("./")||s.startsWith("../"))&&e.code==="ERR_MODULE_NOT_FOUND"){for(const v of [s+".ts",s.replace(/\.js$/,".ts"),s+"/index.ts"]){try{return next(v,c);}catch{}}}throw e;}}});
const m=await import("./src/shared/drawing.ts");
for(const points of [[{x:0,y:0},{x:1,y:0},{x:2,y:0}],[{x:0,y:0},{x:3,y:7},{x:8,y:0}],[{x:2,y:2},{x:3,y:2},{x:2,y:2}]])for(const t of [0.1,2,100]){const r=m.simplifyStroke({points},t);assert.deepEqual(r.points[0],points[0]);assert.deepEqual(r.points.at(-1),points.at(-1));assert.ok(r.points.length>=2);}
console.log("PASS");'
```

`Q3`

```sh
node --input-type=module -e 'import assert from "node:assert/strict";
import {registerHooks} from "node:module";registerHooks({resolve(s,c,next){try{return next(s,c);}catch(e){if((s.startsWith("./")||s.startsWith("../"))&&e.code==="ERR_MODULE_NOT_FOUND"){for(const v of [s+".ts",s.replace(/\.js$/,".ts"),s+"/index.ts"]){try{return next(v,c);}catch{}}}throw e;}}});
const m=await import("./src/shared/drawing.ts");
for(const width of [undefined,0,1.5,5,12]){const s={points:[{x:0,y:0},{x:1,y:0},{x:2,y:0}],...(width===undefined?{}:{width})};for(const t of [-1,0,1,100])assert.equal(m.simplifyStroke(s,t).width,width);}
console.log("PASS");'
```

`Q4`

```sh
node --input-type=module -e 'import assert from "node:assert/strict";
import {registerHooks} from "node:module";registerHooks({resolve(s,c,next){try{return next(s,c);}catch(e){if((s.startsWith("./")||s.startsWith("../"))&&e.code==="ERR_MODULE_NOT_FOUND"){for(const v of [s+".ts",s.replace(/\.js$/,".ts"),s+"/index.ts"]){try{return next(v,c);}catch{}}}throw e;}}});
const m=await import("./src/shared/drawing.ts");
for(let n=0;n<=2;n++){const s={points:[{x:1,y:2},{x:3,y:4}].slice(0,n),width:7};for(const t of [-1,0,5])assert.deepEqual(m.simplifyStroke(s,t),s);}
console.log("PASS");'
```

`Q5`

```sh
node --input-type=module -e 'import assert from "node:assert/strict";
import {registerHooks} from "node:module";registerHooks({resolve(s,c,next){try{return next(s,c);}catch(e){if((s.startsWith("./")||s.startsWith("../"))&&e.code==="ERR_MODULE_NOT_FOUND"){for(const v of [s+".ts",s.replace(/\.js$/,".ts"),s+"/index.ts"]){try{return next(v,c);}catch{}}}throw e;}}});
const m=await import("./src/shared/drawing.ts");
for(let n=0;n<=5;n++){const s={points:Array.from({length:n},(_,i)=>({x:i,y:i%2})),width:4};for(const t of [0,-1,-100]){const r=m.simplifyStroke(s,t);assert.notStrictEqual(r,s,"stroke copy required");assert.deepEqual(r.points,s.points);assert.equal(r.width,s.width);}}
console.log("PASS");'
```

`Q6`

```sh
node --input-type=module -e 'import assert from "node:assert/strict";
import {registerHooks} from "node:module";registerHooks({resolve(s,c,next){try{return next(s,c);}catch(e){if((s.startsWith("./")||s.startsWith("../"))&&e.code==="ERR_MODULE_NOT_FOUND"){for(const v of [s+".ts",s.replace(/\.js$/,".ts"),s+"/index.ts"]){try{return next(v,c);}catch{}}}throw e;}}});
const m=await import("./src/shared/drawing.ts");
for(const points of [[{x:0,y:0},{x:1,y:0},{x:3,y:0}],[{x:2,y:-3},{x:2,y:1},{x:2,y:8}],[{x:-4,y:-4},{x:0,y:0},{x:6,y:6}]])assert.deepEqual(m.simplifyStroke({points},0.01).points,[points[0],points.at(-1)]);
console.log("PASS");'
```

`Q7`

```sh
node --input-type=module -e 'import assert from "node:assert/strict";
import path from "node:path";
import {startVitest} from "vitest/node";
const originalNames = new Set(["erasing > keeps strokes the eraser does not touch", "erasing > splits a stroke around an erased middle point", "erasing > removes a stroke whose every point is erased", "erasing > cuts a segment the eraser path crosses even when both ends survive", "erasing > ignores non-finite eraser input", "erasing > drops empty strokes", "geometry helpers > measures distance to a segment", "geometry helpers > converts legacy normalized strokes with the canvas size", "geometry helpers > reports the lowest drawn point plus half the line and a margin", "geometry helpers > keeps the stroke width on every erased piece"]);
function flatten(files){const all=[];function visit(t){if(t.type==="test")all.push(t);for(const c of t.tasks??[])visit(c);}files.forEach(visit);return all;}
async function run(plugins=[]){const ctx=await startVitest("test",[],{watch:false,reporters:[{onTestRunEnd(){}}],silent:true,passWithNoTests:false},{plugins});assert.ok(ctx,"Vitest started");const tests=flatten(ctx.state.getFiles());const errors=ctx.state.getUnhandledErrors();await ctx.close();process.exitCode=0;return {tests,errors};}
const clean=await run();assert.equal(clean.errors.length,0,"no unhandled suite errors");assert.ok(clean.tests.length>10,"additional executed tests required");assert.ok(clean.tests.every(t=>t.result?.state==="pass"),"all tests must run and pass (no skips/todos)");
const added=new Set(clean.tests.filter(t=>!originalNames.has(t.fullTestName)).map(t=>t.fullName));assert.ok(added.size,"a new test is required");
for(const [file,name,change] of [["src/shared/drawing.ts", "simplifyStroke", "return {...value,points:[]};"]]){
 const target=path.resolve(file);const virtual="\0contract-probe-"+name;
 const plugin={name:"contract-probe-"+name,enforce:"pre",async resolveId(source,importer){if(source.includes("contract-original"))return;const r=await this.resolve(source,importer,{skipSelf:true});if(r?.id===target)return virtual;},load(id){if(id===virtual){const real=JSON.stringify(target+"?contract-original");return `export * from ${real};import * as actual from ${real};export function ${name}(...args){const value=actual[${JSON.stringify(name)}](...args);${change}}`;}}};
 const mutated=await run([plugin]);assert.equal(mutated.errors.length,0,"mutation must not cause infrastructure errors");assert.ok(mutated.tests.some(t=>added.has(t.fullName)&&t.result?.state==="fail"),"new tests must detect wrong "+name+" behavior");
}
console.log("PASS: added tests run and detect target behavior changes");
'
```

`repo1`

```sh
npx --no-install vitest run
```

`repo2`

```sh
npx --no-install tsc --noEmit
```

## T4

정상 구현: 반지름 접점의 단일 점과 반지름 0 테스트를 별도 파일에 추가했다.

| 상태 | 항목 | 종료 코드 | 결정적 출력 |
|---|---|---:|---|
| base | Q1 | 1 | AssertionError [ERR_ASSERTION]: additional executed tests required |
| base | Q2 | 0 | PASS: all ten existing test bodies and helpers preserved |
| base | repo1 | 0 | Tests  10 passed (10) |
| base | repo2 | 0 | (출력 없음) |
| good | Q1 | 0 | PASS: added tests run and detect target behavior changes |
| good | Q2 | 0 | PASS: all ten existing test bodies and helpers preserved |
| good | repo1 | 0 | Tests  12 passed (12) |
| good | repo2 | 0 | (출력 없음) |
| bad-changed-existing-test | Q2 | 1 | AssertionError [ERR_ASSERTION]: an existing test was changed or removed |
| bad-unrelated-added-test | Q1 | 1 | AssertionError [ERR_ASSERTION]: new tests must detect wrong erasing behavior |

### 실행 명령

아래는 실제 실행된 인라인 계약 명령이다. 동일 항목은 상태마다 같은 명령을 사용했다.

`Q1`

```sh
node --input-type=module -e 'import assert from "node:assert/strict";
import path from "node:path";
import {startVitest} from "vitest/node";
const originalNames = new Set(["erasing > keeps strokes the eraser does not touch", "erasing > splits a stroke around an erased middle point", "erasing > removes a stroke whose every point is erased", "erasing > cuts a segment the eraser path crosses even when both ends survive", "erasing > ignores non-finite eraser input", "erasing > drops empty strokes", "geometry helpers > measures distance to a segment", "geometry helpers > converts legacy normalized strokes with the canvas size", "geometry helpers > reports the lowest drawn point plus half the line and a margin", "geometry helpers > keeps the stroke width on every erased piece"]);
function flatten(files){const all=[];function visit(t){if(t.type==="test")all.push(t);for(const c of t.tasks??[])visit(c);}files.forEach(visit);return all;}
async function run(plugins=[]){const ctx=await startVitest("test",[],{watch:false,reporters:[{onTestRunEnd(){}}],silent:true,passWithNoTests:false},{plugins});assert.ok(ctx,"Vitest started");const tests=flatten(ctx.state.getFiles());const errors=ctx.state.getUnhandledErrors();await ctx.close();process.exitCode=0;return {tests,errors};}
const clean=await run();assert.equal(clean.errors.length,0,"no unhandled suite errors");assert.ok(clean.tests.length>10,"additional executed tests required");assert.ok(clean.tests.every(t=>t.result?.state==="pass"),"all tests must run and pass (no skips/todos)");
const added=new Set(clean.tests.filter(t=>!originalNames.has(t.fullTestName)).map(t=>t.fullName));assert.ok(added.size,"a new test is required");
for(const [file,name,change] of [["src/shared/drawing.ts", "erasing", "const [strokes,start,end,radius=actual.ERASER_RADIUS]=args;\nconst invalid=!(radius>0)||!Number.isFinite(radius*radius)||![start.x,start.y,end.x,end.y].every(Number.isFinite);\nconst edge=invalid||strokes.length===0||start.x===end.x&&start.y===end.y||strokes.some(s=>s.points.length<=1||s.width!==undefined||s.points.some((p,i)=>i>0&&p.x===s.points[i-1].x&&p.y===s.points[i-1].y)||s.points.some(p=>Math.abs(actual.pointToSegmentDistanceSquared(p,start,end)-radius*radius)<1e-8));\nreturn edge?undefined:value;"]]){
 const target=path.resolve(file);const virtual="\0contract-probe-"+name;
 const plugin={name:"contract-probe-"+name,enforce:"pre",async resolveId(source,importer){if(source.includes("contract-original"))return;const r=await this.resolve(source,importer,{skipSelf:true});if(r?.id===target)return virtual;},load(id){if(id===virtual){const real=JSON.stringify(target+"?contract-original");return `export * from ${real};import * as actual from ${real};export function ${name}(...args){const value=actual[${JSON.stringify(name)}](...args);${change}}`;}}};
 const mutated=await run([plugin]);assert.equal(mutated.errors.length,0,"mutation must not cause infrastructure errors");assert.ok(mutated.tests.some(t=>added.has(t.fullName)&&t.result?.state==="fail"),"new tests must detect wrong "+name+" behavior");
}
console.log("PASS: added tests run and detect target behavior changes");
'
```

`Q2`

```sh
node --input-type=module -e 'import fs from "node:fs";import assert from "node:assert/strict";import ts from "typescript";
const source=fs.readFileSync("src/shared/drawing.test.ts","utf8");const baseline="import { describe, expect, it } from \"vitest\";\nimport { convertLegacyStrokes, drawingExtent, erasing, pointToSegmentDistanceSquared, type Stroke } from \"./drawing\";\n\nconst line = (...xs: number[]): Stroke => ({ points: xs.map((x) => ({ x, y: 0 })) });\n\ndescribe(\"erasing\", () => {\n  it(\"keeps strokes the eraser does not touch\", () => {\n    const strokes = [line(0, 10, 20)];\n    expect(erasing(strokes, { x: 0, y: 50 }, { x: 20, y: 50 }, 9)).toEqual(strokes);\n  });\n\n  it(\"splits a stroke around an erased middle point\", () => {\n    const result = erasing([line(0, 10, 20, 30, 40)], { x: 20, y: 0 }, { x: 20, y: 0 }, 5);\n    expect(result).toEqual([line(0, 10), line(30, 40)]);\n  });\n\n  it(\"removes a stroke whose every point is erased\", () => {\n    expect(erasing([line(0, 1, 2)], { x: -5, y: 0 }, { x: 5, y: 0 }, 9)).toEqual([]);\n  });\n\n  it(\"cuts a segment the eraser path crosses even when both ends survive\", () => {\n    const stroke: Stroke = { points: [{ x: 0, y: -50 }, { x: 0, y: 50 }] };\n    const result = erasing([stroke], { x: -30, y: 0 }, { x: 30, y: 0 }, 1);\n    expect(result).toEqual([{ points: [{ x: 0, y: -50 }] }, { points: [{ x: 0, y: 50 }] }]);\n  });\n\n  it(\"ignores non-finite eraser input\", () => {\n    const strokes = [line(0, 10)];\n    expect(erasing(strokes, { x: Number.NaN, y: 0 }, { x: 0, y: 0 })).toBe(strokes);\n  });\n\n  it(\"drops empty strokes\", () => {\n    expect(erasing([{ points: [] }], { x: 100, y: 100 }, { x: 100, y: 100 })).toEqual([]);\n  });\n});\n\ndescribe(\"geometry helpers\", () => {\n  it(\"measures distance to a segment\", () => {\n    expect(pointToSegmentDistanceSquared({ x: 5, y: 3 }, { x: 0, y: 0 }, { x: 10, y: 0 })).toBe(9);\n    expect(pointToSegmentDistanceSquared({ x: -3, y: 4 }, { x: 0, y: 0 }, { x: 10, y: 0 })).toBe(25);\n  });\n\n  it(\"converts legacy normalized strokes with the canvas size\", () => {\n    expect(convertLegacyStrokes([{ points: [{ x: 0.5, y: 0.25 }] }], 320, 180)).toEqual([{ points: [{ x: 160, y: 45 }] }]);\n    const untouched = [{ points: [{ x: 0.5, y: 0.5 }] }];\n    expect(convertLegacyStrokes(untouched, 0, 100)).toBe(untouched);\n  });\n\n  it(\"reports the lowest drawn point plus half the line and a margin\", () => {\n    expect(drawingExtent([])).toBe(0);\n    expect(drawingExtent([{ points: [{ x: 1, y: 10 }, { x: 2, y: 400 }] }])).toBeCloseTo(400 + 1.375 + 4);\n    expect(drawingExtent([{ points: [{ x: 2, y: 400 }], width: 10 }])).toBe(409);\n  });\n\n  it(\"keeps the stroke width on every erased piece\", () => {\n    const result = erasing([{ points: line(0, 10, 20, 30, 40).points, width: 5 }], { x: 20, y: 0 }, { x: 20, y: 0 }, 5);\n    expect(result).toEqual([{ ...line(0, 10), width: 5 }, { ...line(30, 40), width: 5 }]);\n  });\n});\n";
function tokens(s){const scanner=ts.createScanner(ts.ScriptTarget.Latest,true,ts.LanguageVariant.Standard,s);const out=[];for(let k=scanner.scan();k!==ts.SyntaxKind.EndOfFileToken;k=scanner.scan()){out.push([k,(k===ts.SyntaxKind.StringLiteral||k===ts.SyntaxKind.NumericLiteral)?scanner.getTokenValue():scanner.getTokenText()]);}return JSON.stringify(out);}
function cases(s){const f=ts.createSourceFile("tests.ts",s,ts.ScriptTarget.Latest,true);const out=[];function visit(n){if(ts.isCallExpression(n)&&ts.isIdentifier(n.expression)&&["it","test"].includes(n.expression.text))out.push(tokens(n.getText(f)));ts.forEachChild(n,visit);}visit(f);return out;}
const current=cases(source);for(const c of cases(baseline)){const i=current.indexOf(c);assert.ok(i>=0,"an existing test was changed or removed");current.splice(i,1);}
const f=ts.createSourceFile("tests.ts",source,ts.ScriptTarget.Latest,true);const b=ts.createSourceFile("tests.ts",baseline,ts.ScriptTarget.Latest,true);for(const s of b.statements.filter(ts.isVariableStatement))assert.ok(f.statements.some(x=>tokens(x.getText(f))===tokens(s.getText(b))),"existing test helper changed");
console.log("PASS: all ten existing test bodies and helpers preserved");'
```

`repo1`

```sh
npx --no-install vitest run
```

`repo2`

```sh
npx --no-install tsc --noEmit
```

## T5

정상 구현: 거리 합과 굵기 가중 합 모듈, 기능별 테스트 2개, 지정 README 섹션 설명을 추가했다.

| 상태 | 항목 | 종료 코드 | 결정적 출력 |
|---|---|---:|---|
| base | Q1 | 1 | Error [ERR_MODULE_NOT_FOUND]: Cannot find module '~/workspace/skills-tools/done-contract/work/trial-2026-10-07/author/scratch/T5/src/shared/strokeStats.ts' imported from ~/workspace/skills-tools/done-contract/work/trial-2026-10-07/author/scratch/T5/[eval1] |
| base | Q2 | 1 | Error [ERR_MODULE_NOT_FOUND]: Cannot find module '~/workspace/skills-tools/done-contract/work/trial-2026-10-07/author/scratch/T5/src/shared/strokeStats.ts' imported from ~/workspace/skills-tools/done-contract/work/trial-2026-10-07/author/scratch/T5/[eval1] |
| base | Q3 | 1 | Error [ERR_MODULE_NOT_FOUND]: Cannot find module '~/workspace/skills-tools/done-contract/work/trial-2026-10-07/author/scratch/T5/src/shared/strokeStats.ts' imported from ~/workspace/skills-tools/done-contract/work/trial-2026-10-07/author/scratch/T5/[eval1] |
| base | Q4 | 1 | Error [ERR_MODULE_NOT_FOUND]: Cannot find module '~/workspace/skills-tools/done-contract/work/trial-2026-10-07/author/scratch/T5/src/shared/strokeStats.ts' imported from ~/workspace/skills-tools/done-contract/work/trial-2026-10-07/author/scratch/T5/[eval1] |
| base | Q5 | 1 | AssertionError [ERR_ASSERTION]: additional executed tests required |
| base | Q6 | 1 | AssertionError [ERR_ASSERTION]: module and a description must occur inside the section |
| base | repo1 | 0 | Tests  10 passed (10) |
| base | repo2 | 0 | (출력 없음) |
| good | Q1 | 0 | PASS |
| good | Q2 | 0 | PASS |
| good | Q3 | 0 | PASS |
| good | Q4 | 0 | PASS |
| good | Q5 | 0 | PASS: added tests run and detect target behavior changes |
| good | Q6 | 0 | PASS: README module description in requested section |
| good | repo1 | 0 | Tests  12 passed (12) |
| good | repo2 | 0 | (출력 없음) |
| bad-zero-width-default | Q3 | 1 | AssertionError [ERR_ASSERTION]: Expected values to be strictly equal: |
| bad-wrong-readme-section | Q6 | 1 | AssertionError [ERR_ASSERTION]: How it is built section required |
| bad-unrelated-added-test | Q5 | 1 | AssertionError [ERR_ASSERTION]: new tests must detect wrong strokeLength behavior |

### 실행 명령

아래는 실제 실행된 인라인 계약 명령이다. 동일 항목은 상태마다 같은 명령을 사용했다.

`Q1`

```sh
node --input-type=module -e 'import assert from "node:assert/strict";
import {registerHooks} from "node:module";registerHooks({resolve(s,c,next){try{return next(s,c);}catch(e){if((s.startsWith("./")||s.startsWith("../"))&&e.code==="ERR_MODULE_NOT_FOUND"){for(const v of [s+".ts",s.replace(/\.js$/,".ts"),s+"/index.ts"]){try{return next(v,c);}catch{}}}throw e;}}});
const m=await import("./src/shared/strokeStats.ts");
assert.equal(typeof m.strokeLength,"function");assert.equal(typeof m.totalInk,"function");
console.log("PASS");'
```

`Q2`

```sh
node --input-type=module -e 'import assert from "node:assert/strict";
import {registerHooks} from "node:module";registerHooks({resolve(s,c,next){try{return next(s,c);}catch(e){if((s.startsWith("./")||s.startsWith("../"))&&e.code==="ERR_MODULE_NOT_FOUND"){for(const v of [s+".ts",s.replace(/\.js$/,".ts"),s+"/index.ts"]){try{return next(v,c);}catch{}}}throw e;}}});
const m=await import("./src/shared/strokeStats.ts");
for(const points of [[],[{x:9,y:8}],[{x:0,y:0},{x:3,y:4}],[{x:0,y:0},{x:3,y:4},{x:3,y:4},{x:-9,y:-1}]]){let expected=0;for(let i=1;i<points.length;i++)expected+=Math.hypot(points[i].x-points[i-1].x,points[i].y-points[i-1].y);assert.ok(Math.abs(m.strokeLength({points,width:9})-expected)<1e-10);}
console.log("PASS");'
```

`Q3`

```sh
node --input-type=module -e 'import assert from "node:assert/strict";
import {registerHooks} from "node:module";registerHooks({resolve(s,c,next){try{return next(s,c);}catch(e){if((s.startsWith("./")||s.startsWith("../"))&&e.code==="ERR_MODULE_NOT_FOUND"){for(const v of [s+".ts",s.replace(/\.js$/,".ts"),s+"/index.ts"]){try{return next(v,c);}catch{}}}throw e;}}});
const m=await import("./src/shared/strokeStats.ts");
const line={points:[{x:0,y:0},{x:3,y:4},{x:6,y:8}]};assert.equal(m.totalInk([]),0);assert.equal(m.totalInk([{...line,width:2},{...line,width:5},{...line,width:0},{points:[],width:100}]),70);
console.log("PASS");'
```

`Q4`

```sh
node --input-type=module -e 'import assert from "node:assert/strict";
import {registerHooks} from "node:module";registerHooks({resolve(s,c,next){try{return next(s,c);}catch(e){if((s.startsWith("./")||s.startsWith("../"))&&e.code==="ERR_MODULE_NOT_FOUND"){for(const v of [s+".ts",s.replace(/\.js$/,".ts"),s+"/index.ts"]){try{return next(v,c);}catch{}}}throw e;}}});
const m=await import("./src/shared/strokeStats.ts");
const {DEFAULT_STROKE_WIDTH}=await import("./src/shared/drawing.ts");const p=[{x:0,y:0},{x:3,y:4}];assert.equal(m.totalInk([{points:p},{points:p,width:2},{points:p,width:0}]),5*DEFAULT_STROKE_WIDTH+10);
console.log("PASS");'
```

`Q5`

```sh
node --input-type=module -e 'import assert from "node:assert/strict";
import path from "node:path";
import {startVitest} from "vitest/node";
const originalNames = new Set(["erasing > keeps strokes the eraser does not touch", "erasing > splits a stroke around an erased middle point", "erasing > removes a stroke whose every point is erased", "erasing > cuts a segment the eraser path crosses even when both ends survive", "erasing > ignores non-finite eraser input", "erasing > drops empty strokes", "geometry helpers > measures distance to a segment", "geometry helpers > converts legacy normalized strokes with the canvas size", "geometry helpers > reports the lowest drawn point plus half the line and a margin", "geometry helpers > keeps the stroke width on every erased piece"]);
function flatten(files){const all=[];function visit(t){if(t.type==="test")all.push(t);for(const c of t.tasks??[])visit(c);}files.forEach(visit);return all;}
async function run(plugins=[]){const ctx=await startVitest("test",[],{watch:false,reporters:[{onTestRunEnd(){}}],silent:true,passWithNoTests:false},{plugins});assert.ok(ctx,"Vitest started");const tests=flatten(ctx.state.getFiles());const errors=ctx.state.getUnhandledErrors();await ctx.close();process.exitCode=0;return {tests,errors};}
const clean=await run();assert.equal(clean.errors.length,0,"no unhandled suite errors");assert.ok(clean.tests.length>10,"additional executed tests required");assert.ok(clean.tests.every(t=>t.result?.state==="pass"),"all tests must run and pass (no skips/todos)");
const added=new Set(clean.tests.filter(t=>!originalNames.has(t.fullTestName)).map(t=>t.fullName));assert.ok(added.size,"a new test is required");
for(const [file,name,change] of [["src/shared/strokeStats.ts", "strokeLength", "return value+1;"], ["src/shared/strokeStats.ts", "totalInk", "return value+1;"]]){
 const target=path.resolve(file);const virtual="\0contract-probe-"+name;
 const plugin={name:"contract-probe-"+name,enforce:"pre",async resolveId(source,importer){if(source.includes("contract-original"))return;const r=await this.resolve(source,importer,{skipSelf:true});if(r?.id===target)return virtual;},load(id){if(id===virtual){const real=JSON.stringify(target+"?contract-original");return `export * from ${real};import * as actual from ${real};export function ${name}(...args){const value=actual[${JSON.stringify(name)}](...args);${change}}`;}}};
 const mutated=await run([plugin]);assert.equal(mutated.errors.length,0,"mutation must not cause infrastructure errors");assert.ok(mutated.tests.some(t=>added.has(t.fullName)&&t.result?.state==="fail"),"new tests must detect wrong "+name+" behavior");
}
console.log("PASS: added tests run and detect target behavior changes");
'
```

`Q6`

```sh
node --input-type=module -e 'import fs from "node:fs";import assert from "node:assert/strict";const s=fs.readFileSync("README.md","utf8");const lines=s.split(/\r?\n/);const start=lines.findIndex(x=>/^#{1,6}\s+How it is built\s*#*\s*$/i.test(x));assert.ok(start>=0,"How it is built section required");const level=lines[start].match(/^#+/)[0].length;let end=start+1;while(end<lines.length&&!(new RegExp("^#{1,"+level+"}\s")).test(lines[end]))end++;assert.ok(lines.slice(start+1,end).some(x=>/strokeStats(?:\.ts)?/.test(x)&&(/strokeLength|totalInk|length|ink|길이|굵기|잉크|통계|metrics|statistic/i.test(x.replace(/strokeStats(?:\.ts)?/g,"")))),"module and a description must occur inside the section");console.log("PASS: README module description in requested section");'
```

`repo1`

```sh
npx --no-install vitest run
```

`repo2`

```sh
npx --no-install tsc --noEmit
```

## 범위와 한계

- 새 테스트 검사는 Vitest를 정상 실행한 뒤 메모리 내 export 동작 교란을 실행하여 새 테스트가 실제로 실패하는지 확인한다. 프로젝트 내 검사 파일은 만들지 않는다.
- 테스트 의미와 자연어 문서의 모든 가능한 표현을 자동으로 증명할 수는 없다. 유한 입력, 테스트 동작 교란, 기존 테스트의 토큰 보존 및 README 모듈·역할 어휘를 사용했으며 해석은 각 계약 notes에 남겼다.
- 초기 탐색에서 작성자 루트의 vitest import는 의존성 해석 실패(1)였다. 이후 모든 검사는 의존성이 제공된 프로젝트 복사본 루트에서 실행했다.
- 검증용 구현과 스크립트는 scratch에만 만들었고 검증 완료 후 scratch 전체를 삭제했다. 납품 대상은 contracts/T1.json~T5.json, VALIDATION.md이며 DONE.md는 완료 표식이다.

## 최종 집계

총 76개 명령을 실행했다. 정상 구현 33개(계약 23개 + 회귀/타입 10개)는 모두 종료 코드 0이며 의도적 위반 10개는 모두 비영 종료였다.

원본 프로젝트 파일 SHA-256 (node_modules 제외):

- `projects/base-main/.gitignore`: `790ed3f043465e741341834624c37856d51fe3e1874073cb1c22ebe91b633f40`
- `projects/base-main/.vitest/json/output.json`: `7d2c5d76821add5efd4c44c6576d867917640d2b0ac9ba808b7ba52fc3680d4f`
- `projects/base-main/README.md`: `d139ea78537b39753db90a9d60826cae2709c2df1aa89dc5544c50138aa9d70c`
- `projects/base-main/package.json`: `3c6fac445df351cdb79296b4b4a84220cf51949ddb385aa19db4494adfd2e7b7`
- `projects/base-main/src/shared/drawing.test.ts`: `8322d5c7beb69a96f85c4ebbcc8de5bacb4ee4ee3d02c49fa9533f9101b22e1f`
- `projects/base-main/src/shared/drawing.ts`: `d7fe76e85e62a3253d2a9ae427fa40ee2663cfd0650df7a3557e79b4d64879ce`
- `projects/base-main/tsconfig.json`: `5111d20ae96cb794d4221d1a81ba64fe780799ca9866801003d0c650b4fefde1`
- `projects/base-main/vitest.config.ts`: `ef37271d6e36a38ac90641cb406afb1ef6e919047e5b09d08b3e0c5108a8ebc8`
- `projects/base-t2/.gitignore`: `790ed3f043465e741341834624c37856d51fe3e1874073cb1c22ebe91b633f40`
- `projects/base-t2/README.md`: `d139ea78537b39753db90a9d60826cae2709c2df1aa89dc5544c50138aa9d70c`
- `projects/base-t2/package.json`: `3c6fac445df351cdb79296b4b4a84220cf51949ddb385aa19db4494adfd2e7b7`
- `projects/base-t2/src/shared/drawing.test.ts`: `8322d5c7beb69a96f85c4ebbcc8de5bacb4ee4ee3d02c49fa9533f9101b22e1f`
- `projects/base-t2/src/shared/drawing.ts`: `e6157d173b6f74305129fe3d5c7c8673eca313641d75988f4687ec4301d4cd17`
- `projects/base-t2/tsconfig.json`: `5111d20ae96cb794d4221d1a81ba64fe780799ca9866801003d0c650b4fefde1`
- `projects/base-t2/vitest.config.ts`: `ef37271d6e36a38ac90641cb406afb1ef6e919047e5b09d08b3e0c5108a8ebc8`
