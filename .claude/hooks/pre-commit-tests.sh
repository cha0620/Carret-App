#!/usr/bin/env bash
# PreToolUse(Bash: git commit): 무료 유닛 테스트가 실패하면 커밋을 막는다.
#
# settings.json 의 if "Bash(git commit*)" 는 명령을 안전하게 해석하지 못하면(heredoc · {} · $변수)
# 보수적으로 "해당"으로 본다 — 그래서 커밋이 아닌 명령에도 테스트가 돌았다 (09-27).
# 여기서 명령을 직접 보고: 이 저장소에 대한 git commit 일 때만 테스트한다.
input=$(cat)
if ! printf '%s' "$input" | CLAUDE_PROJECT_DIR="$CLAUDE_PROJECT_DIR" python3 "$(dirname "$0")/is_repo_commit.py"; then
  exit 0
fi
cd "$CLAUDE_PROJECT_DIR/backend" || exit 0
py=python; [ -x ../venv1/bin/python ] && py=../venv1/bin/python
out=$($py -m pytest test/software/unit -o addopts="" -q -x 2>&1)
[ $? -eq 0 ] && exit 0
summary=$(printf '%s\n' "$out" | grep -E '^(FAILED|ERROR)|[0-9]+ (passed|failed)' | tail -8)
jq -n --arg r "유닛 테스트 실패 — 커밋 차단:
$summary" '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"deny",permissionDecisionReason:$r}}'
