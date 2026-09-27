---
name: wrap-up
description: Carret 에서 코드 변경을 마무리하는 절차 — tester·reviewer 병렬 실행, 지적 반영, 보안 리뷰, 전체 테스트, README·study 갱신, 커밋과 PR. "마무리해줘", "정리해서 PR", "커밋까지" 같은 요청에 쓴다.
---

# wrap-up

## 1. 검증 (병렬)

- **tester** 서브에이전트: 변경 요약(시그니처·새 필드·삭제한 것)을 주고, 깨진 기존 테스트 갱신 + 새 테스트 작성.
  비즈니스 로직은 고치지 않게 한다 — 버그로 보이는 건 보고만.
- **reviewer** 서브에이전트: 의도(무엇을 왜)와 특히 볼 곳(실패 정책, 모든 그래프 경로의 state 키, 비용·지연, 스레드)을 준다.
- tester 가 테스트 파일을 고치는 동안 production 코드를 바꾸지 않는다 — 끝난 뒤 리뷰 반영 → 테스트 재실행.
- 스펙이 도중에 바뀌면 tester 를 멈추고 최종 스펙으로 다시 돌린다.

## 2. 반영

- reviewer 지적은 전부 표로: 지적 / 조치 / 안 한 이유. 안 한 것도 적는다.
- `frontend/` 에 VLM 출력이 화면에 들어가는 변경이 있으면 `/security-review` 를 제안한다 (innerHTML XSS 전례).

## 3. 확인

```bash
cd backend && python -m pytest test/software/unit -o addopts="" -q   # 요약 줄이 보이게
npx --yes markdownlint-cli2 Readme.md Readme.ko.md study/*.md
```

실패가 있으면 커밋하지 않고 출력과 함께 보고한다. (`git commit` 은 PreToolUse 훅이 유닛 테스트를 다시 돌려 실패 시 막는다.)

## 4. 문서

- README **한/영 둘 다**: 파이프라인 mermaid·단계 설명, 설계 결정, 실패와 교훈, 최근 변경(날짜), 로드맵 체크, 테스트 수.
- study 노트: `study-note` 스킬 규칙대로 섹션 추가.

## 5. 커밋 / PR

- 브랜치가 main 이면 먼저 새 브랜치. 커밋 메시지는 한국어, 무엇을·왜 + 테스트 수.
- push 후 PR 이 있으면 본문에 섹션 추가, 없으면 `gh pr create` (base 확인 — 쌓인 PR 이면 아래 브랜치).
- 머지는 사용자가 한다.
