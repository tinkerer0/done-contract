# 시험 도구

일상 과제 격리 시험에 쓴 코드다. 시험대(작은 TypeScript 프로젝트)와 에이전트의 원본 출력은 포함하지 않는다.

- `check_run.py`: 숨은 자동 검사 채점기. 인자는 실행 폴더와 과제 번호(T1~T5)
- `hidden/`: 과제별 숨은 검사와 구문 분석 도우미(`tokens.cjs`, `calls.cjs`)
- `run_trial.py`: Claude Code 헤드리스 실행. 네 군(off, ask, contract, gate)을 만든다
- `run_other.py`: Grok와 Cursor 헤드리스 실행
- `validate_reference.py`, `validate_adversarial.py`: 채점기 자체를 정답 해법과 적대적 사례로 검증
- `regrade.py`, `reprocess.py`, `contract_cross.py`, `summarize.py`, `make_report.py`, `build_results.py`: 재채점, 기록 재처리, 계약 교차 평가, 집계

환경 변수 `TRIAL_SCR`(시험대 폴더), `TRIAL_TOOL_BIN`(코드만 든 도구 복사본의 bin), `TRIAL_NODE_MODULES`, `TRIAL_BASE_MAIN`이 필요하다. 시험대는 `drawing.ts`와 테스트 10개를 담은 작은 프로젝트이며 `validate_reference.py`의 정답 해법으로 만든 기준본이 있어야 한다.
