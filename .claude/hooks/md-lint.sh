#!/usr/bin/env bash
# PostToolUse(Write|Edit): 방금 고친 .md 를 markdownlint 로 검사. 문제가 있으면 exit 2 → Claude 에게 전달.
f=$(jq -r '.tool_input.file_path // .tool_response.filePath // empty')
[[ "$f" == *.md ]] || exit 0
cd "$CLAUDE_PROJECT_DIR" || exit 0
# 파일 경로를 직접 넘기면 markdownlint-cli2 가 .markdownlintignore 를 적용하지 않는다 —
# 여기서 먼저 걸러 낸다 (프롬프트 조각의 "7." 번호 같은 오탐, 09-27)
python3 - "$f" <<'PY' || exit 0
import fnmatch, os, sys
rel = os.path.relpath(os.path.abspath(sys.argv[1]))
try:
    pats = [p.strip() for p in open(".markdownlintignore") if p.strip() and not p.startswith("#")]
except OSError:
    pats = []
for p in pats:
    base = p.rstrip("/")
    if fnmatch.fnmatch(rel, base) or fnmatch.fnmatch(rel, base + "/*"):
        sys.exit(1)   # 무시 목록 → 검사 안 함
PY
out=$(npx --yes markdownlint-cli2 "$f" 2>&1) && exit 0
issues=$(printf '%s\n' "$out" | grep -E ':[0-9]+(:[0-9]+)? (error|warning) ')
[ -z "$issues" ] && exit 0
printf 'markdownlint 경고 (%s):\n%s\n' "$f" "$issues" >&2
exit 2
