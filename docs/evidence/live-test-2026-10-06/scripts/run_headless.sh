#!/usr/bin/env bash
# run_headless.sh <scenario-dir> <prompt>
D="$1"; PROMPT="$2"; BIN_DIR=~/workspace/skills-tools/done-contract/bin
cd "$D" || exit 9
S=$(date +%s)
DONE_CONTRACT_HOME="$D.home" PATH="$BIN_DIR:$PATH" GOAL_REANCHOR_DISABLED=1 DELEGATION_GATE_SUPPRESS=1 NOTE_REMINDER_DISABLE=1 \
  claude -p "$PROMPT" --permission-mode acceptEdits \
  --allowedTools "Write" "Edit" "Read" "Bash(python3 -m unittest:*)" "Bash(done-contract:*)" "Bash(grep:*)" "Bash(cat:*)" "Bash(ls:*)" \
  --max-turns 25 --output-format json < /dev/null > "$D.out.json" 2> "$D.err.txt"
echo "exit=$? wall_s=$(( $(date +%s) - S ))" > "$D.meta.txt"
