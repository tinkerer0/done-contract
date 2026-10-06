# done-contract evidence — slugify

- verdict: **PAUSED**
- checked_at: 2026-10-06T12:08:38Z  duration: 0.25s
- tree: `b9145cf0ab819a1a220574d65ec0209ea61b8010`  baseline: `df5696397a10a0be86278fb857908bbad4118a43`
- contract sha256: `5809dc5655fe8457693e17de3a481c8492928f3525588e5419427f8d86e9b48f`
- approval: 2026-10-06T12:07:25Z by jinwoo-test
- paused: 사용자 결정 대기: test_keeps_case 삭제/수정 허용(보호 경로 변경 승인) 여부 (2026-10-06T12:08:24Z)

| item | status | strength | exit | time | check |
|---|---|---|---|---|---|
| Q1 slugify 구현이 테스트를 통과한다(한글 유지 포함) | BLOCKED | test | 1 | 0.04s | `python3 -m unittest -q tests.test_slugify` |
| Q2 기존 테스트도 그대로 통과한다 | PASS | test | 0 | 0.04s | `python3 -m unittest -q tests.test_app` |
| Q3 README에 사용법 절이 있다 | PASS | content | 0 | 0.01s | `grep -q '^## 사용법' README.md` |

- Q1 blocked: test_basic와 test_keeps_case가 같은 입력 'Hello World'에 서로 다른 출력(hello-world vs Hello-World)을 요구해 동시 통과 불가. 계약 명세는 소문자화이므로 test_keeps_case가 명세와 충돌. tests/**는 보호 경로(allow_protected_changes=false)라 수정하지 않음.
