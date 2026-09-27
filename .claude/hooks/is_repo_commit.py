"""pre-commit-tests.sh 도우미 — stdin 의 PreToolUse JSON 을 보고, 이 명령이 **이 저장소에 대한
git commit** 이면 exit 0, 아니면 exit 1.

- 명령을 쉘 구분자(; & | 줄바꿈 괄호) 로 나눈 조각 중 하나가 `git [-C 경로] commit` 으로 시작해야 한다
  (heredoc 본문이나 따옴표 안의 "git commit" 글자는 조각의 시작이 아니라 걸리지 않는다)
- 그 커밋이 도는 곳이 llm-wiki(별도 저장소)면 제외: 작업 폴더(cwd), 앞선 `cd`, `git -C` 로 판단
"""
import json
import os
import re
import shlex
import sys

WIKI = "llm-wiki"


def segments(cmd: str) -> list[str]:
    # heredoc 본문은 명령이 아니다 — <<'EOF' … EOF 를 지운다
    cmd = re.sub(r"<<-?\s*'?\"?(\w+)'?\"?.*?\n.*?\n\1\s*$", "", cmd, flags=re.S | re.M)
    return [s.strip() for s in re.split(r"&&|\|\||[;&|\n()]", cmd) if s.strip()]


def commit_dir(cmd: str, cwd: str) -> str | None:
    """git commit 이 도는 폴더 (없으면 None)."""
    here = cwd
    for seg in segments(cmd):
        try:
            words = shlex.split(seg)
        except ValueError:
            words = seg.split()
        if not words:
            continue
        if words[0] == "cd" and len(words) > 1:
            here = os.path.normpath(os.path.join(here, os.path.expanduser(words[1])))
            continue
        if words[0] != "git":
            continue
        rest, where = words[1:], here
        while rest and rest[0] == "-C" and len(rest) > 1:
            where = os.path.normpath(os.path.join(where, rest[1]))
            rest = rest[2:]
        if rest and rest[0] == "commit":
            return where
    return None


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except ValueError:
        return 0   # 입력을 못 읽으면 예전처럼 검사한다 (안전한 쪽)
    cmd = (data.get("tool_input") or {}).get("command") or ""
    cwd = data.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    where = commit_dir(cmd, cwd)
    if where is None:
        return 1
    project = os.path.normpath(os.environ.get("CLAUDE_PROJECT_DIR") or cwd)
    rel = os.path.relpath(where, project)
    if rel == WIKI or rel.startswith(WIKI + os.sep):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
