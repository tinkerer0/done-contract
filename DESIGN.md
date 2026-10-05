# done-contract — 설계 (v1.1, 2026-10-05)

v1.0은 Codex(gpt-6-astra/high) 설계 검토에서 FAIL(결함 9건)을 받았고, 이 판은 그 반영이다. 검토 원문: `work/orchestration/20261005-m1/review-design/REPORT.md`(미추적). 반영 내역은 §10.

## 1. 목적

코드 작업에서 에이전트가 "완료"라고 말하는 것과 실제 완료를 분리한다. 완료 판정은 에이전트의 산문이 아니라 **작업 전에 사람이 승인하고 잠근 항목별 계약을 도구가 직접 실행한 결과**다.

선행 조사(`../evidence-gate/docs/PRIOR_ART_2026-10-05.md`)에서 비어 있던 조합 하나를 만든다. 책임표는 §9.

## 2. 원칙

1. 도구는 에이전트의 메시지·transcript를 **읽지 않는다**. hook 입력의 `last_assistant_message`·`transcript_path`는 파싱만 되고 어떤 판정·로그에도 쓰지 않는다. 입력은 계약 파일, git 상태, 명령 exit code뿐이다.
2. 계약은 작업 전에 사람이 승인하고 해시로 잠근다. 승인 뒤 계약 변경은 승인 무효(UNAPPROVED)다.
3. 항목마다 실행 가능한 check가 반드시 있다. check 없는 항목, 자명한 check, 빈 items는 lint가 거부한다.
4. **완료 판정과 종료 허용은 다른 축이다.** 완료는 PASS뿐이다. 종료는 PASS 외에 "못 했다고 명령으로 선언한" INCOMPLETE·PAUSED에도 허용되며, 차단 상한으로 통과시킬 때는 증빙에 `released`를 남기고 성공 영수증을 내지 않는다.
5. 위조 방지는 보장하지 않는다. 에이전트는 사용자 권한으로 실행되므로 상태 파일을 지우거나 hook을 끌 수 있다. 목표는 정직한 실수와 습관적 과장을 막는 것이다. 모든 결정은 로그에 남기고, 로그 실패도 표시한다.

## 3. 파일

```
<repo>/.done-contract/active                 # 활성 task slug (한 줄)
<repo>/.done-contract/<task>/contract.json   # 계약. 승인 뒤 불변
<repo>/.done-contract/<task>/marks.json      # 항목 blocked·paused·closed·abandoned. 가변, 도구 명령으로만
<repo>/.done-contract/<task>/evidence.json   # 마지막 check 결과(+ evidence.md), HMAC
<repo>/.done-contract/.lock                  # worktree 내 직렬화(flock)
~/.done-contract/approved/<sha256>.json      # 승인 기록(repo 밖, 0600, repo 경로 바인딩)
~/.done-contract/key                         # 증빙 HMAC 키(0600)
~/.done-contract/state/<session+task+sha>.json  # hook 차단 횟수
~/.done-contract/log.jsonl                   # 모든 결정 로그
```

`.done-contract/`는 `.git/info/exclude`에 넣는다(사용자 .gitignore는 건드리지 않음). 워크트리마다 따로 간다.

작업 트리 해시: 임시 index에 `read-tree HEAD` + `add -A` + `write-tree`. tracked·untracked 포함, .gitignore·exclude 존중, symlink는 blob, submodule은 gitlink, 사용자 index는 건드리지 않는다. tree 객체가 저장되므로 `git diff <tree> <tree>`로 경로별 변경을 복구한다. baseline은 init 시점의 tree이며 dirty 상태를 그대로 포함한다.

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
    {"id": "Q1", "text": "로그인에 분당 5회 제한", "check": "pytest tests/test_rate_limit.py -q", "expect": null, "timeout": 300},
    {"id": "Q2", "text": "서비스가 /health 200", "check": "curl -fsS http://localhost:8080/health", "cache": false}
  ],
  "repo_checks": ["pytest -q"],
  "protected": ["tests/**", "**/test_*.py", "..."],
  "allow_protected_changes": false
}
```

- `items[].check`: 셸 명령(`sh -c`, cwd=repo, `CI=1`, stdin 없음, 새 세션으로 실행해 timeout 시 프로세스 그룹 전체 kill). exit 0이고 `expect`(있으면)가 stdout+stderr에 포함되면 PASS.
- lint: 알 수 없는 키 거부(오타 방지), items 1개 이상, id 유일, check 필수·비자명, timeout 1..3600, expect 비어 있지 않은 문자열 또는 null.
- 강도 분류(영수증 표시): `test`, `build`, `content`, `existence`, `http`, `other`.
- **캐시**: tree·계약·marks가 같을 때 `test`·`build`·`content`·`existence` 항목만 이전 결과를 재사용한다. `http`·`other`와 `"cache": false` 항목은 항상 재실행한다. `verify`는 캐시를 쓰지 않는다.
- `allow_protected_changes: true`는 보호 경로의 추가·수정·**삭제**를 모두 허용한다. 승인 화면에 그 뜻을 표시한다.
- 계약 해시 = 정규화 JSON(sort_keys, 공백 제거)의 SHA-256. 승인 기록은 해시·repo 경로·승인 시점 tree·승인 전 변경 경로 목록을 담는다.

## 5. 명령과 exit code

| 명령 | 역할 | exit |
|---|---|---|
| `init --task <slug> --request <text\|@file>` | 계약 골격 생성, baseline 기록, active 지정. 활성 계약이 승인 상태면 `--abandon-reason`이 있어야 교체되고 이전 계약은 abandoned로 기록 | 0 / 2 |
| `approve [--accept-dirty]` | **사람이 실행**(TTY 필수). lint·강도·캐시 여부·보호 경로 의미를 보여주고 y/N. init 이후 트리가 바뀌었으면 거부하며, `--accept-dirty`로 승인하면 바뀐 경로가 승인 기록과 증빙에 남는다 | 0 / 2 / 3(TTY 아님) |
| `check [--no-reuse] [--budget s]` | 승인 확인 → tree(전) → 항목·repo check → tree(후) → 보호 경로 diff → 증빙 | PASS 0, FAIL·TESTS_CHANGED·STALE 1, UNAPPROVED 2, INCOMPLETE·PAUSED 4, ERROR 5 |
| `mark <id> blocked\|open --reason` | 항목 상태. blocked는 reason 필수 | 0 / 2 |
| `pause --reason` / `resume` | 사용자 답 대기 등 작업 중단 선언 | 0 |
| `status` | 계약·state(draft/approved/closed/abandoned)·마크·마지막 증빙 | 0 |
| `close` | 현재 계약·승인·marks·tree와 일치하고 HMAC이 유효한 PASS·INCOMPLETE·PAUSED 증빙이 있을 때만. 종료 verdict를 marks에 보존 | PASS 0, 그 외 4 |
| `verify` | 증빙의 명령을 캐시 없이 다시 실행해 기록과 대조(검증자용) | 0 일치 / 1 |
| `hook stop\|pretool [--require-contract]` | Claude Code 어댑터 | 항상 0 |
| `hook install [--write] [--require-contract]` | 프로젝트 `.claude/settings.json` hooks 조각(Stop timeout 900s, PreToolUse 30s) | 0 |

## 6. 판정

우선순위: STALE > ERROR > PAUSED > FAIL > TESTS_CHANGED > INCOMPLETE > PASS.

| verdict | 조건 | 종료 허용 |
|---|---|---|
| STALE | 검사 중 작업 트리가 바뀜(전후 tree 불일치). 바뀐 경로를 증빙에 기록 | 아니오 |
| ERROR | 실행하지 못한 항목이 있음(시간 예산 초과, 실행 불능) | 아니오 |
| PAUSED | `pause`로 중단 선언(다른 상태와 무관하게 표시) | 예(완료 아님) |
| FAIL | blocked 아닌 항목이 FAIL(exit≠0·expect 불일치·timeout)이거나 repo_check FAIL | 아니오 |
| TESTS_CHANGED | 보호 경로가 baseline 대비 바뀌었고 허용 안 됨 | 아니오 |
| INCOMPLETE | 실패 항목이 전부 `blocked`(reason 있음) | 예(완료 아님) |
| PASS | 모든 항목 PASS(blocked 표시가 있어도 실제 통과하면 PASS), repo_checks PASS, 보호 경로 조건 충족 | 예(완료) |
| UNAPPROVED | 승인 없음 또는 승인 뒤 계약 변경 | 예(게이트 꺼짐, 안내) |

항목 상태는 PASS·FAIL·BLOCKED·ERROR이고 실행 결과(exit·출력 꼬리·소요 시간·출력 해시)와 blocked 표시를 별도 필드로 보존한다.

## 7. Stop hook

1. `cwd`의 git 최상위에서 `.done-contract/active`를 찾는다. 없으면 exit 0(`--require-contract`면 안내 systemMessage).
2. 계약이 draft(미승인)면 차단하지 않고 안내한다. closed·abandoned면 침묵.
3. 차단 횟수는 `session+task+contract sha` 단위로 센다. 상한(기본 3, `DONE_CONTRACT_MAX_BLOCKS`)에 도달하면 통과시키되 증빙에 `released: {reason: block_cap}`을 기록하고 systemMessage에 "완료가 아니다"와 마지막 판정·실패 항목을 보여준다. "변경이 없으면 통과" 같은 지름길은 없다.
4. `check`를 실행한다(시간 예산 `DONE_CONTRACT_STOP_BUDGET`, 기본 840초). PASS·INCOMPLETE·PAUSED면 exit 0 + 영수증 systemMessage(blocked 항목과 이유 포함).
5. FAIL·TESTS_CHANGED·STALE·ERROR면 `{"decision":"block","reason":…}`. reason에는 항목별 상태, 실패 출력 꼬리, 다음 행동(고치고 `check`, `mark … blocked --reason`, `pause --reason`), 차단 횟수를 적는다.
6. 내부 오류: 차단하지 않지만 systemMessage로 "검증되지 않은 종료"와 원인을 알리고 로그한다. 로그 실패도 메시지에 표시한다.

## 8. PreToolUse hook

- 승인된 계약이 활성일 때: `Edit`·`Write`·`MultiEdit`·`NotebookEdit`가 보호 경로(허용 안 됨)·`contract.json`·증빙 파일을 대상으로 하면 deny. `Bash`가 `done-contract approve`·승인 디렉터리·`DONE_CONTRACT_APPROVE_NO_TTY`·`DONE_CONTRACT_HOME`을 포함하면 deny. `.done-contract/` 경로와 쓰기 힌트가 함께 있으면 deny. 보호 경로 토큰과 쓰기 힌트(`>`, `sed -i`, `tee`, `rm`, `mv`, `git checkout` 등)가 함께 있으면 `ask`.
- `--require-contract` 정책(hook 등록 시 선택): 승인된 활성 계약이 없으면 파일 변경(Edit류, 쓰기 힌트가 있는 Bash)을 deny한다. 단 draft 계약의 `contract.json` 편집과 `done-contract init/status/...` 명령은 허용한다. 이것이 "작업 전에 계약"을 강제하는 장치다. 정책은 `.claude/settings.json`의 hook 인자로 표현되므로 config-guard가 지키는 범위에 든다.
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

## 10. v1.0 검토 반영

| 결함 | 반영 |
|---|---|
| F1 승인 전 작업 허용 | `--require-contract` 정책으로 미승인 상태의 파일 변경 거부. approve는 init 이후 변경을 거부하고 `--accept-dirty` 시 기록 |
| F2 INCOMPLETE/FAIL 중복, exit 혼합 | 항목 상태와 blocked 표시 분리, 우선순위 명문화, exit 0은 PASS만, INCOMPLETE·PAUSED는 4, close는 종료 verdict 보존 |
| F3 외부 상태 캐시 | 강도 `http`·`other`·`cache:false`는 항상 재실행, verify는 무캐시 |
| F4 close 조건 약함 | check·close가 같은 유효성 규칙(task·repo·계약 sha·승인·marks digest·tree·HMAC) 사용 |
| F5 init이 활성 계약 대체 | 승인된 활성 계약은 `--abandon-reason` 없이는 교체 불가, abandoned 기록 |
| F6 변경 없으면 통과 | 규칙 삭제. 차단 budget은 session+task+sha 단위, 상한 통과 시 `released` 기록·성공 영수증 없음 |
| F7 fail-open이 오류 은폐 | ERROR verdict, 내부 오류 systemMessage, 로그 실패 표시, CLI exit 5 |
| F8 검사 전후 snapshot | tree 전후 비교, 불일치면 STALE(차단) |
| F9 범위·책임 | §9 책임표, HMAC 보장 범위 명시 |
| 질문 2 lint | 알 수 없는 키 거부, 빈 items 거부, timeout 범위 |
| 질문 3 시간 예산 | hook timeout 900s, 예산 840s, 초과 항목 ERROR, 프로세스 그룹 kill |
| 질문 8 동시성 | flock으로 worktree 내 직렬화, 원자적 파일 교체 |
| 대안(판정·종료 분리) | §2.4, §6, §7 |

미해결: 질문 7(systemMessage가 사용자 화면에 보이는지)은 실제 Claude Code 실행으로 확인한다.
