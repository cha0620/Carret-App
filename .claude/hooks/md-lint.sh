#!/usr/bin/env bash
# PostToolUse(Write|Edit): 방금 고친 .md 를 markdownlint 로 검사. 문제가 있으면 exit 2 → Claude 에게 전달.
f=$(jq -r '.tool_input.file_path // .tool_response.filePath // empty')
[[ "$f" == *.md ]] || exit 0
cd "$CLAUDE_PROJECT_DIR" || exit 0
out=$(npx --yes markdownlint-cli2 "$f" 2>&1) && exit 0
issues=$(printf '%s\n' "$out" | grep -E ':[0-9]+(:[0-9]+)? (error|warning) ')
[ -z "$issues" ] && exit 0   # .markdownlintignore 로 빠진 파일 등
printf 'markdownlint 경고 (%s):\n%s\n' "$f" "$issues" >&2
exit 2
