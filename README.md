# done-contract

AI 코딩 에이전트가 "다 했다"고 말하는 것과 실제로 된 것을 분리하는 도구다. 작업 시작 때 **요구 항목마다 실행 가능한 확인 명령**을 적어 사람이 승인하면 잠기고, 에이전트가 끝내려 할 때 **도구가 그 명령들을 직접 실행**해 항목별로 판정한다. 에이전트의 말은 읽지 않는다.

Python 3.11+ 표준 라이브러리만 쓴다. 설계는 `DESIGN.md`, 실제 Claude Code 세션 확인 기록은 `docs/evidence/`.

## 흐름

```text
1. 에이전트  done-contract init --task login-rate-limit --request "요청 원문"
2. 에이전트  .done-contract/login-rate-limit/contract.json 의 items[] 채우기
3. 사람      done-contract approve            ← TTY에서 직접. 항목·check·강도를 보고 y
4. 에이전트  작업 … (보호 경로 편집은 거부됨)
5. Stop hook done-contract hook stop          ← 끝내려 할 때 자동. 실패면 차단, 이유 전달
6. 에이전트  고치거나  mark Q2 blocked --reason "…"  또는  pause --reason "…"
7. 사람      .done-contract/<task>/evidence.md 를 읽는다. 완료는 PASS뿐이다
8. 에이전트  done-contract close               ← 검사를 다시 실행해 통과할 때만 닫힌다
```

## 설치

```sh
git clone <this repo> ~/tools/done-contract
ln -s ~/tools/done-contract/bin/done-contract ~/.local/bin/done-contract   # PATH에 (symlink 가능)
cd <project>
done-contract hook install --write                      # <project>/.claude/settings.json 에 Stop·PreToolUse hook 병합
done-contract hook install --write --require-contract   # 승인된 계약 없이는 파일 변경을 거부하는 정책까지 (재실행하면 기존 항목을 갱신)
```

`--write` 없이 실행하면 조각만 출력한다. Stop hook의 timeout은 900초로 등록되고, 검사 시간 예산은 840초다(`DONE_CONTRACT_STOP_BUDGET`). 예산을 넘긴 항목은 FAIL이 아니라 ERROR로 기록되고 Stop이 차단되므로, 그때는 `done-contract check`를 직접 실행한다.

## 계약 예

```json
{
  "version": 1,
  "task": "login-rate-limit",
  "request": "로그인 엔드포인트에 분당 5회 제한을 넣고 설정 방법을 문서화해줘",
  "items": [
    {"id": "Q1", "text": "로그인에 분당 5회 제한", "check": "pytest tests/test_rate_limit.py -q", "cache": true},
    {"id": "Q2", "text": "설정 방법을 docs/config.md에 기술", "check": "grep -q RATE_LIMIT docs/config.md"},
    {"id": "Q3", "text": "서비스가 /health 200", "check": "curl -fsS http://localhost:8080/health"}
  ],
  "repo_checks": [],
  "protected": ["tests/**", "**/test_*.py", "**/*_test.py", "**/*.test.*", "**/*.spec.*", "**/conftest.py"],
  "allow_protected_changes": false
}
```

`init`이 `created_at`·`baseline_head`·`baseline_tree`를 채운다. 승인 뒤 이 파일을 고치면 승인이 무효가 된다. `cache: true`는 "작업 트리가 그대로면 이전 결과를 써도 된다"는 사람의 승인이다. 외부 서비스·환경·저장소 밖 파일에 의존하는 check에는 붙이지 않는다.

## 판정

| verdict | 뜻 | Stop |
|---|---|---|
| PASS | 모든 항목 통과. **완료는 이것뿐** | 통과 |
| INCOMPLETE | 실패 항목이 전부 `blocked`(이유 기록) | 통과, 영수증에 표시 |
| PAUSED | `pause --reason`으로 중단 선언 | 통과, 영수증에 표시 |
| FAIL | blocked 아닌 항목 실패 또는 repo check 실패 | 차단 |
| TESTS_CHANGED | 보호 경로가 바뀜(허용 안 됨) | 차단 |
| STALE | 검사 중 작업 트리가 바뀜 | 차단 |
| ERROR | 실행 못 한 항목 있음(예산·실행 불능·중단) | 차단 |
| UNAPPROVED | 승인 없음 | 게이트 꺼짐, 안내 |

차단은 세션·작업당 3회(`DONE_CONTRACT_MAX_BLOCKS`). 상한 뒤에도 검사는 매번 실행하며, 여전히 실패면 통과시키되 증빙에 `released`를 남기고 "완료가 아니다"를 표시한다.

exit code: `check`는 PASS 0, FAIL·TESTS_CHANGED·STALE 1, UNAPPROVED 2, INCOMPLETE·PAUSED 4, ERROR 5. `close`는 PASS 0, 그 외 4. 내부 오류는 5.

## require-contract 정책

`hook install --require-contract`로 켜면 승인된 계약 없이는 Edit·Write가 거부된다. Bash는 읽기 전용 명령(cat, grep, git status, pytest …)과 `done-contract` 명령만 허용하고, 쓰기 힌트·heredoc·인터프리터 실행은 거부하며, 그 밖의 명령은 사람에게 묻는다(`ask`). 셸 명령의 쓰기 여부를 완전히 가려낼 수는 없으므로 Edit류는 강제, Bash는 best-effort다.

## 검증자용

```sh
done-contract verify          # 증빙의 명령을 다시 실행해 기록과 대조. agree(재현)와 current(지금 상태와 일치)를 따로 보고
cat .done-contract/<task>/evidence.md
tail ~/.done-contract/log.jsonl
```

## 보장하지 않는 것

- 에이전트가 설정을 고쳐 hook을 끄거나 `~/.done-contract/`를 지우는 것. 로그와 config-guard로 보완한다.
- check의 적절성. `test -f` 한 줄도 통과한다. 승인하는 사람이 강도 표시를 보고 정한다.
- 소스가 테스트 입력만 특수 처리하는 변조. 검증자 몫이다.
- Bash 명령의 쓰기 여부 완전 판정.

## 테스트

```sh
python3 -m unittest -q tests.test_done_contract
```
