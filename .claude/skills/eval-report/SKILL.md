---
name: eval-report
description: Carret 파이프라인 수치 보기 — 쌓인 inspect JSON·성적표를 모아 composite 비율, detect/verify 실패율, 가드 실패, DINO·item_dino 분포, judge 점수를 표로 요약한다. "eval 돌려줘", "숫자 보자", "임계값 정하자", "가드 오차단률" 같은 요청에 쓴다. 비용이 드는 실제 eval 은 사용자 확인 후에만.
---

# eval-report

숫자 없이 임계값을 고치지 않는다. 이 스킬은 **무료 → 유료** 순서로 수치를 모은다.

## 1. 무료: 이미 쌓인 결과 요약 (항상 먼저)

```bash
python .claude/skills/eval-report/inspect_stats.py            # 전체
python .claude/skills/eval-report/inspect_stats.py --preset studio_white
```

- 입력: `backend/storage/quality/*_inspect.json` (+ 같은 이름의 성적표 `.json`)
- `STORAGE_BACKEND=s3` 면 로컬에 없다 — 사용자에게 알리고 dev 서버의 `GET /dev/results` 를 대신 쓴다.
- 표본이 적으면(n < 20) 결론 대신 "표본 부족"이라고 쓴다.

## 2. Langfuse (MCP `langfuse` 가 연결돼 있을 때)

- 노드별 소요 시간(span 이름 = 노드 이름), `detect_failed` / `verify_failed` / `composite_reason` 빈도,
  `item_dino` · `dino_band` · `item_patch` Score 분포 (`ocr_match` 는 09-27 에 가드와 함께 없앰 — 옛 트레이스에만 있다)를 조회한다.
- 연결 안 돼 있으면 건너뛰고 그렇다고 적는다.

## 3. 유료 (사용자 확인 필수 — VLM·fal.ai 비용)

먼저 예상 호출 수를 알려주고 승인을 받는다.

| 명령 | 재는 것 |
|---|---|
| `make eval` (`pytest backend/test -m eval`) | detect item_acc / recall / precision (GT = `backend/test/eval/dataset.json`) |
| dev 서버 `POST /dev/run-inbox` | inbox 원본을 실제 파이프라인으로 → inspect JSON 이 쌓임 → 1번 다시 |

## 출력 형식

1. 한 줄 결론 (예: "item_dino 는 정상 결과에서 0.86~0.95, 0.80 기준이면 오차단 0건")
2. 표: 지표 / 값 / n / 이전 대비
3. **결정 제안**: 어떤 임계값을 어떻게 바꿀지, 근거가 되는 수치, 표본이 충분한지
4. 눈으로 볼 후보 (낮은 점수 상위 몇 건의 file_id·preset)

결과는 오늘 날짜 study 노트(`study-note` 스킬)의 해당 섹션에 붙인다.
