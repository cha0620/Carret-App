#!/usr/bin/env bash
# SessionStart: "지난번 여기까지 / 남은 일"을 사용자에게 보여주고 Claude 컨텍스트에도 넣는다.
# 출처: 가장 최근 study 노트의 마지막 "하루 마무리" 섹션의 체크 안 된 항목 + git 상태.
cd "$CLAUDE_PROJECT_DIR" || exit 0

note=$(ls -1 study/*.md 2>/dev/null | sort | tail -1)
[ -z "$note" ] && exit 0

# 마지막 "## ... 하루 마무리" 섹션부터 다음 "## " 전까지의 "- [ ]" 항목 (없으면 파일 전체에서)
todo=$(awk '
  /^## .*하루 마무리/ { buf=""; on=1; next }
  /^## / { if (on) done=1; on=0 }
  on && /^- \[ \]/ { buf = buf $0 "\n" }
  END { printf "%s", buf }' "$note")
[ -z "$todo" ] && todo=$(grep -E '^- \[ \]' "$note" | tail -10)
todo=$(printf '%s' "$todo" | sed -E 's/\*\*//g' | cut -c1-140 | head -15)

branch=$(git branch --show-current 2>/dev/null)
dirty=$(git status --porcelain 2>/dev/null | grep -vc '^??')
last=$(git log -1 --format='%h %s' 2>/dev/null | cut -c1-80)

msg="📋 지난 노트: ${note#study/}
브랜치 ${branch:-?} · 마지막 커밋 ${last} · 커밋 안 된 변경 ${dirty}개

남은 일:
${todo:-(없음)}"

jq -n --arg m "$msg" '{systemMessage: $m,
  hookSpecificOutput: {hookEventName: "SessionStart",
    additionalContext: ("세션 시작 브리핑 (study 노트·git 에서 자동 수집). 사용자가 \"오늘 뭐 하지\" 류를 물으면 이걸 기준으로 답한다:\n" + $m)}}'
