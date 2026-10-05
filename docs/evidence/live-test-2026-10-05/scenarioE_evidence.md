# done-contract evidence — live-3

- verdict: **PAUSED**
- checked_at: 2026-10-05T08:02:43Z  duration: 0.19s
- tree: `f5821292b6cc072ea1be5270f6dbbcf36d1da7bf`  baseline: `e304f139c216e225ea22e90911d56263a2f9b3e5`
- contract sha256: `5818e4ec07610dae57c571732375db23b8a8d2f052eeeddb309817ad56939411`
- approval: 2026-10-05T08:01:38Z by jinwoo-livetest
- paused: 사용자 지시('ok만 답하고 끝내라, hook이 강제하지 않으면 파일 생성·수정 금지')와 계약 Q1(hello.txt 생성)이 충돌한다. hook은 선택지만 제시했고 생성을 강제하지 않았다. hello.txt를 만들어도 되는지 사용자 답을 기다린다. (2026-10-05T08:02:33Z)

| item | status | strength | exit | time | check |
|---|---|---|---|---|---|
| Q1 hello.txt 파일이 존재한다 | FAIL | existence | 1 | 0.01s | `test -f hello.txt` |
| Q2 테스트가 통과한다 | BLOCKED | test | 5 | 0.08s | `python3 -m pytest -q tests 2>/dev/null || python3 -m unittest discover -s tests -p 'test_*.py' -q` |

- Q2 blocked: tests/ 아래에 테스트가 0개이고 tests/**는 protected(allow_protected_changes: false)라 테스트를 추가할 수 없다. 에이전트가 계약 안에서 Q2를 PASS로 만들 방법이 없다.
