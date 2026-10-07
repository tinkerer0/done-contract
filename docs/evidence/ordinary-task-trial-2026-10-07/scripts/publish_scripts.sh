#!/usr/bin/env bash
# publish_scripts.sh: copies the experiment tooling into docs/evidence (no data, no logs), replacing the local home path with ~.
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"; REPO="$HERE/../.."; OUT="$REPO/docs/evidence/ordinary-task-trial-2026-10-07"
rm -rf "$OUT/scripts"; mkdir -p "$OUT/scripts/hidden"
for f in check_run.py common.py run_trial.py run_other.py regrade.py reprocess.py contract_cross.py summarize.py make_report.py build_results.py validate_reference.py validate_adversarial.py grade_snapshots.py indep_sanity.py make_indep_report.py build_indep_results.py publish_scripts.sh; do cp "$HERE/$f" "$OUT/scripts/$f"; done
cp "$HERE"/hidden/*.test.ts "$HERE"/hidden/*.cjs "$OUT/scripts/hidden/"
cp "$HERE/results.csv" "$OUT/results.csv"
cp "$HERE/data/classification.json" "$OUT/classification.json"; cp "$HERE/data/classification_notes.json" "$OUT/classification_notes.json"
cp "$(sed 's/TOOL=//' "$HERE/tool_path.txt")/wrap/done-contract" "$OUT/scripts/snapshot_wrapper.py"
OUT2="$REPO/docs/evidence/independent-contract-trial-2026-10-07"; mkdir -p "$OUT2/contracts"
cp "$HERE"/contracts/T*.json "$OUT2/contracts/"; cp "$HERE/results_indep.csv" "$OUT2/results_indep.csv"
cp "$HERE/data/classification.json" "$OUT2/classification.json"; cp "$HERE/data/classification_notes.json" "$OUT2/classification_notes.json"; cp "$HERE/data/escapes.json" "$OUT2/escapes.json"
TMPX="$(mktemp -d)"; tar xzf "$HERE/data/author_archive.tgz" -C "$TMPX" author/VALIDATION.md 2>/dev/null && cp "$TMPX/author/VALIDATION.md" "$OUT2/contracts/VALIDATION.md"; rm -rf "$TMPX"
python3 - "$OUT" "$OUT2" <<'PY'
import sys, pathlib
home = "~"
for out in [pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])]:
  for p in list(out.rglob("*.py")) + list(out.rglob("*.sh")) + list(out.rglob("*.json")) + list(out.rglob("*.md")) + list(out.rglob("*.csv")):
    t = p.read_text()
    if home in t: p.write_text(t.replace(home, "~"))
PY
cat > "$OUT/scripts/README.md" <<'MD'
# 시험 도구

일상 과제 격리 시험에 쓴 코드다. 시험대(작은 TypeScript 프로젝트)와 에이전트의 원본 출력은 포함하지 않는다.

- `check_run.py`: 숨은 자동 검사 채점기. 인자는 실행 폴더와 과제 번호(T1~T5)
- `hidden/`: 과제별 숨은 검사와 구문 분석 도우미(`tokens.cjs`, `calls.cjs`)
- `run_trial.py`: Claude Code 헤드리스 실행. 네 군(off, ask, contract, gate)을 만든다
- `run_other.py`: Grok와 Cursor 헤드리스 실행
- `validate_reference.py`, `validate_adversarial.py`: 채점기 자체를 정답 해법과 적대적 사례로 검증
- `regrade.py`, `reprocess.py`, `contract_cross.py`, `summarize.py`, `make_report.py`, `build_results.py`: 재채점, 기록 재처리, 계약 교차 평가, 집계

환경 변수 `TRIAL_SCR`(시험대 폴더), `TRIAL_TOOL_BIN`(코드만 든 도구 복사본의 bin), `TRIAL_NODE_MODULES`, `TRIAL_BASE_MAIN`이 필요하다. 시험대는 `drawing.ts`와 테스트 10개를 담은 작은 프로젝트이며 `validate_reference.py`의 정답 해법으로 만든 기준본이 있어야 한다.
MD
echo "복사 완료:"; find "$OUT" -type f | sed "s|$OUT/||" | sort | head -50
