#!/usr/bin/env bash
# PreToolUse(Bash: git commit): 무료 유닛 테스트가 실패하면 커밋을 막는다.
cd "$CLAUDE_PROJECT_DIR/backend" || exit 0
py=python; [ -x ../venv1/bin/python ] && py=../venv1/bin/python
out=$($py -m pytest test/software/unit -o addopts="" -q -x 2>&1)
[ $? -eq 0 ] && exit 0
summary=$(printf '%s\n' "$out" | grep -E '^(FAILED|ERROR)|[0-9]+ (passed|failed)' | tail -8)
jq -n --arg r "유닛 테스트 실패 — 커밋 차단:
$summary" '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"deny",permissionDecisionReason:$r}}'
