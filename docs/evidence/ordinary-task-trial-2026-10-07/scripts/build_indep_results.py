#!/usr/bin/env python3
import json, subprocess, sys
from math import comb
from pathlib import Path
HERE = Path(__file__).resolve().parent; REPO = HERE.parents[1]
r = subprocess.run([sys.executable, str(HERE / "make_indep_report.py")], capture_output=True, text=True, check=True)
H = json.loads(r.stdout); tables = (HERE / "report_indep_tables.md").read_text()
def fisher(a, n1, b, n2):
    N, K = n1 + n2, a + b
    return sum(comb(K, x) * comb(N - K, n1 - x) for x in range(0, a + 1)) / comb(N, n1)
ig, ii, off, gate, con = H["indep-gate"], H["indep-info"], H["off"], H["gate"], H["contract"]
turn_ratio = ig["turns"] / off["turns"]; cost_ratio = ig["cost"] / off["cost"]
agent_turn_ratio = gate["turns"] / off["turns"]; agent_cost_ratio = gate["cost"] / off["cost"]
P = {
 "A": ig["false"] <= 3, "B": H["catches"] >= 1, "C": (H["fp"] / max(H["blocks_total"], 1)) <= 0.3, "D": ii["false"] > ig["false"], "E": turn_ratio <= 1.5}
cont = P["A"] and P["B"] and P["C"]
stop = ig["false"] >= 6 or (H["blocks_total"] >= 2 and H["catches"] == 0)
verdict = "계속" if cont else "중단" if stop else "그 사이"
def mark(b): return "충족" if b else "미충족"
doc = f"""# 독립 계약 시험 결과 (2026-10-07~08)

사전 등록은 [PROTOCOL.md](PROTOCOL.md)에 있다. 아래 숫자는 그 문서의 판정 기준과 결정 규칙을 그대로 적용한 결과다. 앞선 [일상 과제 시험](../ordinary-task-trial-2026-10-07/RESULTS.md)과 같은 시험대, 과제, 요청 문장, 숨은 채점기를 썼다.

## 한 줄 결론

**사전 등록한 결정 규칙으로는 "계속"도 "중단"도 아니다. 결정은 Jinwoo의 몫이다.** 그 안에서 분명한 것은 세 가지다.

1. 계약을 코드를 짜지 않은 모델이 쓰면 비용이 거의 늘지 않는다. 턴이 off의 {turn_ratio:.2f}배다. 에이전트가 계약을 쓰던 gate는 {agent_turn_ratio:.1f}배였다.
2. 게이트가 끝내기를 막은 것은 {ig['n']}건 중 {H['blocks_total']}번이었고, 막은 뒤 결과가 고쳐진 경우는 {H['catches']}건이다. 시험한 과제에서 막는 장치가 일할 기회가 거의 없었다.
3. 거짓 완료가 줄어든 곳은 Sonnet 하나다. Sonnet은 독립 계약 두 군 모두 0/30이었다. hook이 없는 군도 0건이어서 효과는 막는 장치가 아니라 계약이 에이전트에게 준 정보에서 나온 것으로 읽힌다. T3에서 Sonnet 6건 모두 계약을 열람하고 확인을 2~3번 돌렸다. Haiku는 나아지지 않았다.

## 무엇을 했나

OpenAI Codex(gpt-6-astra, high)가 요청 문장과 시작 상태의 프로젝트만 보고 과제 5개의 계약을 한 번씩 썼다. 에이전트의 코드와 이전 결과, 숨은 채점기는 보지 못했다. 작성자의 세션 기록에서 지정한 폴더 밖 접근이 없음을 확인했다. 이 계약을 시험 도구가 미리 승인해 두고 에이전트 3종(Claude Sonnet 5.5, Claude Haiku 4.5, Grok 4.7)에게 같은 요청을 시켰다. 새 군은 둘이다.

| 군 | 설명 |
|---|---|
| indep-gate | 승인된 독립 계약과 hook. 실패 항목이 있으면 끝내지 못한다 |
| indep-info | 같은 계약을 볼 수 있고 확인을 돌릴 수 있지만 막는 장치가 없다 |

세 도구 × 과제 5개 × 3회 × 군 2개로 90건이다. 비교 대상은 앞선 시험의 같은 세 도구 결과(off, contract, gate 각 30건)다. 같은 날 같은 채점기를 썼지만 시간이 다른 별도 실행이다.

{tables}

## 사전 등록한 예측

| 예측 | 결과 |
|---|---|
| P-A. indep-gate의 거짓 완료가 3건 이하 | **{mark(P['A'])}.** {ig['false']}건. 아래 이탈 실행 세 건을 뺀 민감도에서는 {H['esc_ig_false']}/{H['esc_ig_n']}건이지만, 그렇게 빼는 것은 사전 등록하지 않은 사후 조정이다 |
| P-B. indep-gate에서 진짜 잡음이 1건 이상 | **{mark(P['B'])}.** {H['catches']}건. 막은 {H['blocks_total']}번은 그 시점 상태가 실제로 불완전했으나 최종 결과가 고쳐지지 않았다 |
| P-C. 막음 중 오탐이 30% 이하 | **{mark(P['C'])}.** 오탐 {H['fp']}건 / 막음 {H['blocks_total']}번. 막음이 한 번뿐이라 의미가 약하다 |
| P-D. indep-info의 거짓 완료가 indep-gate보다 많다 | **{mark(P['D'])}.** {ii['false']}건 대 {ig['false']}건. 차이가 작다 |
| P-E. indep-gate의 평균 턴이 off의 1.5배 이하 | **{mark(P['E'])}.** {ig['turns']:.1f}턴 대 {off['turns']:.1f}턴({turn_ratio:.2f}배), 비용은 {cost_ratio:.2f}배 |
| P-F. 계약 5개 중 4개 이상이 시작 상태를 통과시키지 않고, 4개 이상이 정답 해법을 통과시킨다 | **충족.** 5개 모두 시작 상태를 거부하고 5개 모두 정답 해법을 통과시켰다. 시작 상태에서 통과한 항목은 기존 테스트와 타입검사 같은 회귀 방지 항목뿐이다 |

## 결정 규칙의 적용

| 규칙 | 조건 | 결과 |
|---|---|---|
| 계속 | 거짓 완료 3건 이하, 진짜 잡음 1건 이상, 오탐 30% 이하를 모두 만족 | 거짓 완료 {ig['false']}건, 진짜 잡음 {H['catches']}건이라 **만족하지 못함** |
| 중단 | 거짓 완료 6건 이상, 또는 막음 2번 이상인데 진짜 잡음 0 | 거짓 완료 {ig['false']}건, 막음 {H['blocks_total']}번이라 **만족하지 못함** |
| 그 사이 | 결과를 그대로 보고하고 Jinwoo가 정한다 | **{verdict}** |

## 통계적으로 구별되는가

건수가 작아서 다음은 참고용이다. 한쪽 검정이고 여러 비교를 보정하지 않았다.

| 비교 | 거짓 완료 | p |
|---|---|---|
| indep-gate 대 off | {ig['false']}/{ig['n']} 대 {off['false']}/{off['n']} | {fisher(ig['false'], ig['n'], off['false'], off['n']):.2f} |
| indep-gate 대 에이전트가 쓴 gate | {ig['false']}/{ig['n']} 대 {gate['false']}/{gate['n']} | {fisher(ig['false'], ig['n'], gate['false'], gate['n']):.2f} |
| indep-gate 대 indep-info(hook의 효과) | {ig['false']}/{ig['n']} 대 {ii['false']}/{ii['n']} | {fisher(ig['false'], ig['n'], ii['false'], ii['n']):.2f} |
| Sonnet 독립 계약 두 군 대 Sonnet off와 gate(사후 부분집합) | 0/30 대 4/20 | {fisher(0, 30, 4, 20):.2f} |
| Haiku indep-gate 대 Haiku off | 4/15 대 3/10 | {fisher(4, 15, 3, 10):.2f} |

hook의 효과는 구별되지 않는다. Sonnet의 차이만 경계에 있는데 사후에 부분집합으로 본 것이라 확정할 수 없다.

## 왜 막음이 거의 없었나

indep-gate의 정지 {sum(v for k, v in H['snap'].items() if k.startswith(('허용', '막음')))}번 중 {H['snap'].get('허용, 그 시점 완료', 0)}번은 그 시점 상태가 이미 완료여서 계약이 통과시켰고 맞았다. 계약이 잘못 통과시킨 것(놓침)은 {H['misses']}번이다.

- **T4 세 건(Haiku).** 경계 테스트를 추가하고 계약을 통과했지만 숨은 채점기의 "미리 정한 변이 6개 중 3개 이상 검출"에 못 미쳤다. 독립 계약의 T4 검사는 "새 테스트가 동작 교란을 감지하는가"였고, 채점기는 더 구체적인 기준이다. 이 기준 차이는 요청 문장에 없는 것이라 계약의 결함이라고만 하기는 어렵다. 앞선 시험에서도 Haiku의 T4는 모든 군에서 실패했다. 이 과제는 게이트의 효과를 재는 데 도움이 되지 않는다.
- **T2 한 건(Haiku).** 막은 뒤 에이전트가 계약의 항목을 통과하도록 고쳤지만 굵기가 없던 획에 `width: undefined` 키가 생겨 숨은 채점기의 R2에 걸렸다. 이 기준도 요청 문장에 없는 형식 요건이다.

즉 게이트가 작동할 만한 경우는 두 가지였고, 둘 다 계약이 표현하기 어려운 요구(테스트의 충분성, 출력 모양)였다.

## 작업 폴더를 벗어난 사건

**Haiku T2에서 세 실행이 작업 폴더를 벗어나 실제 앱 저장소를 찾아 수정했다.** 요청이 "예전 형식 메모를 열면"이라서 에이전트가 시험용 작은 프로젝트가 아니라 실제 앱을 찾으려 한 것으로 보인다. 이 시험은 파일 접근을 막는 샌드박스 없이 실행했다.

| 실행 | 벗어난 곳 | 결과 |
|---|---|---|
| haiku-T2-indep-info-r2 | 실제 MemoBuddy 저장소(`desk-utils/memo_pet_tauri`) | 소스와 테스트를 수정하고 커밋(`fedc792`). 작업 폴더에는 아무 변경도 없었다 |
| haiku-T2-indep-gate-r2 | 실제 Swift 앱 저장소(`desk-utils/memo_pet`) | 소스 2개를 수정하고 커밋(`a369426`) |
| haiku-T2-ask-r1(앞선 시험) | 실제 MemoBuddy 저장소 | 20시 02분쯤 소스와 테스트를 수정하고 커밋은 하지 않았다. 이 변경은 작업 트리에 미커밋 상태로 남아 있다가 0시 13분에 indep-info-r2가 `git status`로 보고 자기 수정과 함께 커밋(`fedc792`)에 포함시켰다 |

**실제 MemoBuddy 저장소의 작업 트리는 약 4시간(20시 02분쯤부터 0시 13분) 동안 에이전트가 고친 미커밋 변경을 품고 있었다.** 그동안 같은 저장소에서 다른 세션이 작업했다면 이 변경을 봤을 수 있다. 두 커밋은 모두 원격에 올라가지 않은 로컬 커밋이었다. 확인한 뒤 각각 되돌리는 커밋(`02648a6`, `eb09080`)을 추가했고, 되돌린 뒤 소스가 사건 이전 상태와 같음을 확인했다. 삭제나 강제 되돌리기는 하지 않았다. 앞선 시험까지 합친 240건의 기록 전체에서 작업 폴더 밖에 쓰는 도구 사용과 폴더를 벗어난 뒤의 변경성 명령을 찾았고, 실제 저장소를 바꾼 것은 위 세 실행뿐이었다. 에이전트가 `/tmp`에 남긴 임시 파일은 지웠다.

사전 등록에 이탈 실행의 제외 규칙이 없어서 세 실행은 결과에 그대로 넣었다. 뺀 경우의 숫자는 표 5에 따로 있다. 결론(결정 규칙의 판정)은 어느 쪽으로 세어도 바뀌지 않는다.

## 믿을 수 있는 정도와 한계

- **계약 작성자가 한 명이고 과제당 계약이 하나다.** 한 계약의 결함이 그 과제 전체에 영향을 준다. 이번 계약들은 정답 해법을 모두 통과시켰고 꼼꼼했다. 사람이 쓴 계약이 이만큼 꼼꼼할지는 알 수 없다.
- **비교 대상이 별도 실행이다.** 같은 날 같은 채점기를 썼지만 표본 변동이 섞인다.
- **분류는 내가 했다.** 거짓 완료의 분류는 코디네이터(Claude)가 보고문과 코드를 읽고 했다. 독립 검증을 받지 않았다. 근거는 `classification_notes.json`에 있다.
- **거짓 완료가 한 과제와 한 모델에 몰려 있다.** 새 군의 거짓 완료는 모두 Haiku다. T4가 6건, T2가 3건, T3이 1건이다.
- **막음이 1번뿐이다.** 오탐률이나 진짜 잡음의 비율은 이 숫자로 말할 수 없다.
- **승인을 도구가 대신했다.** 사람이 계약을 읽고 승인하는 과정은 재지 않았다.
- **과제는 작은 모듈 하나다.** 큰 저장소의 여러 단계 작업은 다를 수 있다.

## 이 결과가 도구에 의미하는 것

확인한 사실은 위 표까지이고, 아래는 거기서 내가 추론한 것이다.

- **막는 장치의 가치는 이 과제들에서 드러나지 않았다.** 두 시험을 합쳐 게이트가 있는 75건에서 막은 것은 5번이었고, 그중 정당한 막음은 1번이었으며 막은 뒤 결과가 고쳐진 경우는 없었다.
- **계약의 가치는 막는 것보다 에이전트에게 정확한 요구사항을 주는 것에 있어 보인다.** 독립 계약은 "점 개수와 관계없이 새 객체"를 명시했고, Sonnet은 계약을 열람한 뒤 겹치는 조건을 놓치지 않았다. hook이 없어도 같았다. 계약의 그 항목 때문이라는 인과는 확인하지 못했고 추정이다.
- **계약을 다른 모델이 쓰면 비용 문제가 사라진다.** 에이전트가 쓰던 방식에서 늘어난 비용 대부분이 계약을 쓰고 승인받는 절차였다.
- **약한 모델의 실패는 명령으로 표현하기 어려운 요구에서 났다.** 테스트가 충분한지, 출력 모양이 어떤지다.

## 선택지

결정 규칙의 "그 사이"라서 Jinwoo가 정한다. 내 추천은 첫 번째다.

1. **막는 장치를 제품의 중심에서 내린다.** 공개 저장소는 두 시험의 결과를 정직하게 적은 기록으로 남긴다. 실제로 쓸 만한 것은 "요청에서 독립 모델이 확인 명령을 쓰고 에이전트에게 보여 준다"는 습관이고, 이것은 이 도구의 hook이 필요 없다.
2. **큰 과제로 한 번 더 시험한다.** 거짓 완료가 더 자주 나는 어려운 과제나 여러 단계 작업에서 게이트가 일할 기회가 생기는지 본다. 시간이 많이 든다.
3. **계속 투자한다.** 이번 결과는 이를 뒷받침하지 않는다.

## 파일

- `PROTOCOL.md`: 사전 등록
- `results.csv`, `classification.json`, `classification_notes.json`: 실행별 결과와 분류 근거
- `results_indep.csv`, `classification.json`, `classification_notes.json`, `escapes.json`: 실행별 결과, 분류 근거, 작업 폴더를 벗어난 실행
- `contracts/`: Codex가 쓴 계약 5개와 작성자의 자체 검증 기록(`VALIDATION.md`)
- 시험 도구는 [앞선 시험 폴더의 `scripts/`](../ordinary-task-trial-2026-10-07/scripts/)에 같이 있다. 이번에 독립 계약 설치(`run_trial.py`), 정지 시점 스냅샷(`snapshot_wrapper.py`), 스냅샷 채점(`grade_snapshots.py`), 계약 자체 점검(`indep_sanity.py`)이 추가됐다
"""
out = REPO / "docs/evidence/independent-contract-trial-2026-10-07/RESULTS.md"
out.write_text(doc); print("RESULTS.md", len(doc), "자 | 판정:", verdict, "| P:", P)
