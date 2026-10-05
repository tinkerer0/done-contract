# done-contract — 설계 (v1.2, 2026-10-05)

v1.0은 Codex(gpt-6-astra/high) 설계 검토에서 FAIL(결함 9건), v1.1 구현(v0.2)은 같은 검토자의 구현 검토에서 FAIL(재현 결함 12건)을 받았다. 이 판은 두 검토를 반영한 v0.3의 설계다. 검토 원문: `work/orchestration/20261005-m1/review-design/REPORT.md`, `REPORT_impl.md`(미추적). 반영 내역은 §10.

## 1. 목적

코드 작업에서 에이전트가 "완료"라고 말하는 것과 실제 완료를 분리한다. 완료 판정은 에이전트의 산문이 아니라 **작업 전에 사람이 승인하고 잠근 항목별 계약을 도구가 직접 실행한 결과**다.

선행 조사(`../evidence-gate/docs/PRIOR_ART_2026-10-05.md`)에서 비어 있던 조합 하나를 만든다. 책임표는 §9.

## 2. 원칙

1. 도구는 에이전트의 메시지·transcript를 **읽지 않는다**. hook 입력의 `last_assistant_message`·`transcript_path`는 파싱만 되고 어떤 판정·로그에도 쓰지 않는다. 입력은 계약 파일, git 상태, 명령 exit code·출력뿐이다.
2. 계약은 작업 전에 사람이 승인하고 해시로 잠근다. 승인 뒤 계약 변경은 승인 무효(UNAPPROVED)다.
3. 항목마다 실행 가능한 check가 반드시 있다. check 없는 항목, 자명한 check, 빈 items는 lint가 거부한다.
4. **완료 판정과 종료 허용은 다른 축이다.** 완료는 PASS뿐이다. 종료는 PASS 외에 "못 했다고 명령으로 선언한" INCOMPLETE·PAUSED에도 허용되며, 차단 상한으로 통과시킬 때는 증빙에 `released`를 남기고 성공 영수증을 내지 않는다.
5. **게이트가 직접 실행한다는 약속이 캐시보다 우선한다.** check·Stop·close·verify는 모두 실제 실행 결과를 쓴다. 재사용은 사람이 `cache: true`로 승인한 항목에만, 작업 트리가 그대로일 때만 한다.
6. 위조 방지는 보장하지 않는다. 에이전트는 사용자 권한으로 실행되므로 상태 파일을 지우거나 hook을 끌 수 있다. 목표는 정직한 실수와 습관적 과장을 막는 것이다. 모든 결정은 로그에 남기고, 로그 실패도 표시한다.

## 3. 파일

```
<repo>/.done-contract/active                 # 활성 task slug (한 줄)
<repo>/.done-contract/<task>/contract.json   # 계약. 승인 뒤 불변
<repo>/.done-contract/<task>/marks.json      # 항목 blocked·paused·closed·abandoned. 가변, 도구 명령으로만
<repo>/.done-contract/<task>/evidence.json   # 마지막 check 결과(+ evidence.md), HMAC. 검사 시작 시 in_progress 스텁으로 먼저 덮는다
<repo>/.done-contract/.lock                  # worktree 내 직렬화(flock)
~/.done-contract/approved/<sha256>.json      # 승인 기록(repo 밖, 0600, repo 경로 바인딩)
~/.done-contract/key                         # 증빙 HMAC 키(0600, O_EXCL 생성)
~/.done-contract/state/<session+task+sha>.json  # hook 차단 횟수
~/.done-contract/log.jsonl                   # 모든 결정 로그
```

`.done-contract/`는 `git rev-parse --git-path info/exclude`가 가리키는 exclude 파일에 넣는다(`.git`이 파일인 linked worktree·separate git dir 포함). 쓰지 못하면 오류다. 사용자 .gitignore는 건드리지 않는다.

작업 트리 해시: 임시 index에 `read-tree HEAD` + `add -A` + `write-tree`. tracked·untracked 포함, .gitignore·exclude 존중, symlink는 링크 텍스트, submodule은 gitlink, 사용자 index는 건드리지 않는다. 경로 diff는 `git diff --name-only -z --no-renames`로 받아 한글·제어문자·rename(삭제+추가)을 그대로 본다. **해시에 들어가지 않는 입력**(symlink 대상, ignored 산출물, submodule 내용, 환경·서비스)이 있으므로 캐시는 opt-in이다.

## 4. 계약 스키마

```json
{
  "version": 1,
  "task": "login-rate-limit",
  "request": "사용자 요청 원문 그대로",
  "created_at": "2026-10-05T06:10:00Z",
  "baseline_head": "<HEAD sha>",
  "baseline_tree": "<working tree hash at init>",
  "items": [
    {"id": "Q1", "text": "로그인에 분당 5회 제한", "check": "pytest tests/test_rate_limit.py -q", "expect": null, "timeout": 300, "cache": true},
    {"id": "Q2", "text": "서비스가 /health 200", "check": "curl -fsS http://localhost:8080/health"}
  ],
  "repo_checks": [],
  "protected": ["tests/**", "**/test_*.py", "..."],
  "allow_protected_changes": false
}
```

- `items[].check`: 셸 명령(`sh -c`, cwd=repo, `CI=1`, stdin 없음, 새 세션으로 실행해 timeout 시 프로세스 그룹 kill). **한 번만 실행**하고 출력은 임시 파일로 받아 `expect`를 전체 출력에서 찾는다. 증빙에는 꼬리 40줄·출력 해시·크기만 남긴다. exit 0이고 expect(있으면)가 포함되면 PASS.
- lint: 알 수 없는 키 거부, items 1개 이상, id 유일, check 필수·비자명, timeout 1..3600, expect 비어 있지 않은 문자열 또는 null, cache bool.
- 강도 분류(`http`·`test`·`build`·`content`·`existence`·`other`)는 **영수증 정보일 뿐** 캐시를 결정하지 않는다. 복합 명령은 가장 약한 쪽으로 분류한다.
- `cache: true`: 작업 트리·계약·marks가 같을 때 이전 결과 재사용. 승인 화면에 CACHED로 표시된다. repo_checks는 항상 실행한다. `verify`는 캐시를 쓰지 않는다.
- `allow_protected_changes: true`는 보호 경로의 추가·수정·**삭제**를 모두 허용한다. 승인 화면에 그 뜻을 표시한다.
- 계약 해시 = 정규화 JSON의 SHA-256. 승인 기록은 해시·repo 경로·승인 시점 tree·승인 전 변경 경로를 담는다.

## 5. 명령과 exit code

| 명령 | 역할 | exit |
|---|---|---|
| `init --task <slug> --request <text\|@file>` | 계약 골격 생성, baseline 기록, active 지정, 이전 증빙 제거. 활성 계약이 승인 상태면 `--abandon-reason`이 있어야 교체되고 이전 계약은 abandoned로 기록 | 0 / 2 |
| `approve [--accept-dirty]` | **사람이 실행**(TTY 필수). lint·강도·캐시 여부·보호 경로 의미를 보여주고 y/N. init 이후 트리가 바뀌었으면 거부(`--accept-dirty`면 기록). 답을 받은 뒤 lock 안에서 계약 sha·active·tree를 다시 확인해 기다리는 동안 바뀌었으면 거부 | 0 / 2 / 3(TTY 아님) |
| `check [--no-reuse] [--budget s]` | 승인 확인 → in_progress 스텁 기록 → tree(전) → 항목·repo check → tree(후) → 보호 경로 diff → 증빙 | PASS 0, FAIL·TESTS_CHANGED·STALE 1, UNAPPROVED 2, INCOMPLETE·PAUSED 4, ERROR 5 |
| `mark <id> blocked\|open --reason` | 항목 상태. blocked는 reason 필수 | 0 / 2 |
| `pause --reason` / `resume` | 사용자 답 대기 등 작업 중단 선언 | 0 |
| `status` | 계약·state(draft/approved/closed/abandoned)·마크·마지막 증빙 | 0 |
| `close` | lock 안에서 **check를 새로 실행**하고 그 판정이 PASS·INCOMPLETE·PAUSED일 때만 닫는다. 종료 verdict를 marks에 보존 | PASS 0, 그 외 4 |
| `verify` | 증빙의 명령을 기록된 timeout으로 캐시 없이 다시 실행해 대조. `agree`(재현)와 `current`(계약·승인·marks·tree가 지금과 같음)를 따로 보고 | 둘 다 참 0 / 1 |
| `hook stop\|pretool [--require-contract]` | Claude Code 어댑터 | 항상 0 |
| `hook install [--write] [--require-contract]` | 프로젝트 `.claude/settings.json` hooks 조각(Stop timeout 900s, PreToolUse 30s). 기존 done-contract 항목은 교체(정책·경로·timeout 갱신), 다른 도구 hook은 보존. 경로는 shell-quote | 0 |

내부 오류(OSError 등)는 CLI exit 5로 끝나고, 검사 중이던 증빙은 in_progress 스텁으로 남아 어떤 close도 그 위에서 성공하지 않는다.

## 6. 판정

우선순위: STALE > ERROR > PAUSED > FAIL > TESTS_CHANGED > INCOMPLETE > PASS.

| verdict | 조건 | 종료 허용 |
|---|---|---|
| STALE | 검사 중 작업 트리가 바뀜(전후 tree 불일치). 바뀐 경로를 증빙에 기록 | 아니오 |
| ERROR | 실행하지 못한 항목이 있음(시간 예산 초과·실행 불능·검사 중단) | 아니오 |
| PAUSED | `pause`로 중단 선언(다른 상태와 무관하게 표시) | 예(완료 아님) |
| FAIL | blocked 아닌 항목이 FAIL(exit≠0·expect 불일치·timeout)이거나 repo_check FAIL | 아니오 |
| TESTS_CHANGED | 보호 경로가 baseline 대비 바뀌었고 허용 안 됨 | 아니오 |
| INCOMPLETE | 실패 항목이 전부 `blocked`(reason 있음) | 예(완료 아님) |
| PASS | 모든 항목 PASS(blocked 표시가 있어도 실제 통과하면 PASS), repo_checks PASS, 보호 경로 조건 충족 | 예(완료) |
| UNAPPROVED | 승인 없음 또는 승인 뒤 계약 변경 | 예(게이트 꺼짐, 안내) |

항목 상태는 PASS·FAIL·BLOCKED·ERROR이고 실행 결과(exit·timeout·출력 꼬리·출력 해시·소요)와 blocked 표시를 별도 필드로 보존한다.

시간 예산: `check --budget s`와 Stop hook(기본 840초, `DONE_CONTRACT_STOP_BUDGET`)은 deadline을 잡고 각 명령의 timeout을 `min(항목 timeout, 남은 예산)`으로 자른다. 예산 때문에 잘린 명령과 시작하지 못한 항목은 FAIL이 아니라 ERROR다.

## 7. Stop hook

1. `cwd`의 git 최상위에서 `.done-contract/active`를 찾는다. 없으면 exit 0(`--require-contract`면 안내 systemMessage).
2. 계약이 draft(미승인)면 차단하지 않고 안내한다. closed·abandoned면 침묵.
3. `check`를 실행한다(예산 840초, opt-in 캐시만 재사용). PASS·INCOMPLETE·PAUSED면 exit 0 + 영수증 systemMessage(blocked 항목과 이유 포함), 차단 횟수를 0으로 되돌린다.
4. 그 외(FAIL·TESTS_CHANGED·STALE·ERROR)는 `{"decision":"block","reason":…}`. reason에는 항목별 상태, 실패 출력 꼬리, 다음 행동(고치고 `check`, `mark … blocked --reason`, `pause --reason`), 차단 횟수를 적는다. 차단 횟수는 `session+task+contract sha` 단위다.
5. 차단 상한(기본 3, `DONE_CONTRACT_MAX_BLOCKS`)에 도달한 뒤에도 check는 매번 실행한다. 통과 판정이 나오면 정상 영수증이고, 여전히 실패면 차단 대신 통과시키되 증빙에 `released: {reason: block_cap}`을 기록하고 systemMessage에 "완료가 아니다"와 판정·실패 항목을 보여준다. "변경이 없으면 통과" 같은 지름길은 없다.
6. 내부 오류: 차단하지 않지만 systemMessage로 "검증되지 않은 종료"와 원인을 알리고 로그한다. 로그 실패는 어떤 메시지에든 덧붙인다.

## 8. PreToolUse hook

경로 해석: 상대 경로는 payload의 `cwd` 기준으로 저장소 상대 경로를 만들고, symlink를 따라가지 않은 lexical 경로와 따라간 resolved 경로 **둘 다** 보호 경로와 대조한다. Bash 토큰의 절대 경로도 저장소 상대 경로로 바꿔 본다.

- 승인된 계약이 활성일 때: `Edit`·`Write`·`MultiEdit`·`NotebookEdit`가 보호 경로(허용 안 됨)·`contract.json`·`.done-contract/` 아래를 대상으로 하면 deny. `Bash`가 `done-contract approve`·승인 디렉터리·`DONE_CONTRACT_APPROVE_NO_TTY`·`DONE_CONTRACT_HOME`을 포함하면 deny. `.done-contract/` 경로와 쓰기 힌트·heredoc이 함께 있으면 deny. 보호 경로 토큰과 쓰기 힌트가 함께 있으면 `ask`.
- `--require-contract` 정책: 승인된 활성 계약이 없으면 Edit류는 deny(draft 계약의 `contract.json`만 허용). Bash는 3단으로 판정한다. ① approve 관련·heredoc → deny ② `done-contract init/status/...`와 쓰기 힌트 없는 읽기 전용 allowlist(cat, ls, grep, rg, find, git status/log/diff/show, pytest, npm test, cargo test …) → allow ③ 쓰기 힌트(`>`, `sed -i`, `rm`, `mv`, `git checkout` …)·인터프리터 실행(python, node, sh, bash, env …) → deny ④ 그 밖의 명령 → `ask`(사람이 정함). 셸 명령의 쓰기 여부를 완전히 판정할 수는 없으므로 Edit류는 강제, Bash는 best-effort + ask다.
- 내부 오류는 결정 없음(fail-open).

## 9. 책임표 (v1)

| 기능 | 담당 |
|---|---|
| 항목별 계약·승인·잠금·항목 check 실행·항목별 증빙 | done-contract |
| 보호 경로 변경 탐지(baseline 대비)와 Edit 차단 | done-contract(작업 baseline 기준이라 자체 구현) |
| 테스트 삭제·skip 차단(패턴) | protect-tests(선택, 보완) |
| hook 설정 자체 보호 | config-guard(선택) |
| 저장소 전체 test·lint·build | isitdone(선택). 함께 쓰면 `repo_checks`는 비워 중복 실행을 피한다 |
| 테스트 변조 의미 분석(기대값 조정·mock) | 사람·검증자 |

HMAC의 보장: 증빙 파일의 손 편집·손상을 감지한다. 키는 사용자 계정이 읽을 수 있으므로 보안 경계가 아니다.

## 10. 검토 반영

### v1.0 설계 검토 (F1~F9)

| 결함 | 반영 |
|---|---|
| F1 승인 전 작업 허용 | `--require-contract` 정책, approve의 dirty 거부와 `--accept-dirty` 기록, 답변 뒤 재확인(R12) |
| F2 INCOMPLETE/FAIL 중복, exit 혼합 | 항목 상태와 blocked 표시 분리, 우선순위 명문화, exit 0은 PASS만, INCOMPLETE·PAUSED는 4 |
| F3 외부 상태 캐시 | 캐시는 `cache: true` 항목만(R1), repo_checks 무캐시, verify 무캐시 |
| F4 close 조건 약함 | close가 lock 안에서 check를 새로 실행(R2) |
| F5 init이 활성 계약 대체 | `--abandon-reason` 없이는 교체 불가, abandoned 기록 |
| F6 변경 없으면 통과 | 규칙 삭제. 상한 뒤에도 check 실행, 통과 시 `released` 기록 |
| F7 fail-open이 오류 은폐 | ERROR verdict, in_progress 스텁, 실행 예외를 항목 ERROR로, CLI exit 5, systemMessage |
| F8 검사 전후 snapshot | tree 전후 비교, 불일치면 STALE |
| F9 범위·책임 | §9 책임표, HMAC 보장 범위 |
| 대안(판정·종료 분리) | §2.4, §6, §7 |

### v0.2 구현 검토 (R1~R12)

| 결함 | 반영 |
|---|---|
| R1 강도 기반 캐시가 외부 입력 무시 | 강도는 정보만. 캐시는 `cache: true` opt-in, 승인 화면에 CACHED 표시 |
| R2 close가 재검사 안 함 | close = lock 안 fresh check |
| R3 실행 예외 시 과거 PASS 생존 | 검사 시작 시 in_progress 스텁으로 덮음, 예외는 항목 ERROR, 증빙 쓰기 실패 시 기존 증빙 삭제 후 오류 |
| R4 diff 경로 인용·rename | `-z --no-renames`, `core.quotePath=false` |
| R5 PreToolUse 경로 해석 | cwd 기준 정규화, lexical+resolved 둘 다 대조, Bash 절대 경로 변환 |
| R6 require-contract Bash 우회 | 3단 정책(allowlist/deny/ask), 선행 공백·heredoc·인터프리터 처리, 문서에 best-effort 명시 |
| R7 재설치가 정책 미갱신 | merge_hooks가 자사 항목 교체, 설치 후 effective 명령 출력 |
| R8 `.git` 파일 레이아웃 | `git rev-parse --git-path info/exclude`, 실패는 오류 |
| R9 예산이 실행 중 명령 미제한 | deadline 기반 timeout 절단, 잘린 명령은 ERROR |
| R10 expect 이중 실행·verify 꼬리만 | 단일 실행, 임시 파일 전체 검색, verify도 같은 경로·기록된 timeout |
| R11 symlink 설치 실패 | 진입점 realpath, hook 경로 shell-quote |
| R12 승인 대기 중 변경 | 답변 뒤 lock 안에서 sha·active·tree 재확인 |
| 질문 1 동시성 | approve·mark_released도 lock, 고유 tmp 이름, HMAC 키 O_EXCL |
| 질문 2 상한 뒤 복구 | 상한 뒤에도 check, PASS면 영수증·카운터 초기화 |
| 질문 3 verify 의미 | `agree`와 `current` 분리, ok는 둘 다 |
| 질문 4 로그 실패 | 모든 systemMessage에 로그 실패 표시 |

미해결: systemMessage가 대화형 화면에 보이는지(headless에서는 관측 불가). 셸 명령의 쓰기 판정은 원리적으로 불완전하다(§8).
