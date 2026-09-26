# 2026-09-26 — 파이프라인 점검: 게이트 구멍 · 옛 점수 · 직렬 대기 · 글자 사후검증

> 한 줄 요약: **"검사를 못 했다"를 "검사해서 괜찮았다"로 처리하던 곳이 두 군데 있었다**
> (detect 실패 → 게이트 생략, judge 캐시 → 옛 점수). 둘 다 고쳤고, 덤으로
> read_text ∥ detect 병렬화와 TEXT_LOCK 사후 검증을 붙였다.

관련 노트: `2026-09-26-next-study-scores-and-decisions.md` (결정 지점),
`2026-09-26-langgraph-langfuse-syntax.md` (그래프 문법)

---

## 1. detect 실패가 Wear Gate 를 통째로 건너뛰던 문제 🔴

### 증상 (고치기 전)

```text
detect 예외 ─▶ anchors = []  (print 한 줄 남기고 무시)
            ─▶ verify: "앵커가 없네" → 검사 안 함, gate_passed = None
            ─▶ _route_after_verify: None 은 False 가 아님 → "done"
            ─▶ 생성본이 결함 검증 없이 그대로 나감
```

- 원칙이 "상태는 바꾸지 않는다"인데, Gemini 가 한 번 삐끗하면 FLUX 가 흠집을 지운
  결과물도 **아무 검사 없이** 나갈 수 있었다.
- `_call` 이 JSON 파싱 실패를 `{}` 로 삼키기 때문에, 예외조차 안 나고
  `defects` 키가 없는 빈 응답 → "하자 0개" 로 조용히 통과하는 경로도 있었다.

### detect 가 실패하는 경우는 구체적으로?

`detect` 는 Gemini VLM 호출 한 번이다 (`detector._call`). 실패 원인은 크게 셋:

| 종류 | 예 | 전에는 |
|---|---|---|
| **호출 자체 실패 (예외)** | 네트워크 끊김·타임아웃, 429 (분당 한도/쿼터 초과), 500/503 (Gemini 과부하 — flash 계열에서 흔함), API 키 만료·오류 | 예외 → `anchors=[]` |
| **응답은 왔는데 JSON 이 아님** | 출력이 중간에 잘림 (생각 토큰이 길어져 출력 한도 도달 — detect 는 thinking 기본값), 모델이 JSON 앞뒤에 설명 문장을 붙임 | `_call` 이 `{}` 반환 → **예외도 없이** 하자 0개 |
| **응답이 비어 있음** | 안전 필터로 차단되면 `resp.text` 가 `None` → `json.loads(None)` 이 `TypeError` (JSONDecodeError 가 아니라서 안 잡히고 예외로 올라옴) | 예외 → `anchors=[]` |
| **모양이 틀림** | `{"defects": "none"}` 처럼 목록이 아닌 값 | 반복문이 문자열을 돎 → 이상한 앵커 or 0개 |

즉 **"진짜 하자가 없는 물건"과 "모델한테 못 물어본 것"이 둘 다 `[]` 로 같아 보였다**는 게 핵심.

> 참고: genai 클라이언트에 타임아웃을 따로 안 걸어서, "실패"가 아니라 **오래 매달리는**
> 경우도 있을 수 있다 (별도 과제).

### 수정

- `detector.detect_defects`: 응답에 `defects` **목록**이 없으면 `ValueError` — 빈 응답을 실패로 격상.
- `pipeline.detect`: `DETECT_ATTEMPTS = 2` 회 시도, 둘 다 실패면 `detect_failed=True`.
- `verify`:
  - `detect_failed` + 생성본 → VLM 안 부르고 `gate_passed=False`
  - `detect_failed` + 합성본(composite) → `gate_passed=None` (물건 픽셀이 원본이라 검증 대상 아님)
- `_route_after_verify`: `detect_failed` 면 재생성(`mark_gate_retry`) 건너뛰고 **바로 composite**
  — 재생성해도 비교 기준(원본 하자 목록)이 없으니 돈만 쓴다.
- `detect_failed` 는 inspect JSON · `run_transform` 결과 · Langfuse output 에 남는다.
  (API 응답 스키마 `TransformResponse` 에는 아직 없음 — UI "검증 불가" 표시하려면 추가 필요)

```text
detect 2회 실패 ─▶ detect_failed=True ─▶ plan: 생성 전에 바로 composite (FLUX 안 부름)
  ├ 오리기 성공 → score/verify (gate=None, 검증 대상 아님) → 저장, mode=composite
  └ 오리기 실패 → 그때 generate → verify: gate_passed=False
        → composite (이미 실패한 오리기라 재호출 안 함) → mode=composite_failed, 생성본
          (UI: "하자 검사를 하지 못했습니다 — 생성 이미지에서 하자가 지워졌을 수 있어요")
```

> 처음엔 generate 를 1회 돌린 뒤 composite 로 보냈는데, 리뷰에서 "결과를 어차피 버린다"는
> 지적 → `plan` 노드로 생성 전 분기 (아래 7-6).

---

## 2. judge 캐시가 옛 점수를 돌려주던 문제 🔴

- `run_judge` 는 `quality/{file_id}_{preset}.json` 이 **있으면 다시 채점 안 함**.
- 파일 이름에 결과 이미지 정보가 없다 → 같은 사진·같은 프리셋을 다시 변환하면
  **이미지는 새것, 점수는 지난번 것**.
- `blocked` (원본을 내보냄) 이면 judge 를 건너뛰는데, `api/routes/transform.py` 는
  그 파일을 그대로 읽어 응답에 넣으므로 → 원본 사진에 옛 생성본 점수가 붙어 나감.

수정:
- 캐시 삭제. `run_judge` 시작 시 옛 성적표를 **먼저 지우고** 항상 새로 채점
  (blocked · judge 실패여도 옛 점수가 남지 않음). mock 경로도 지움.
- `storage.delete(kind, name)` 신설 — Local(`unlink(missing_ok)`) / S3(`delete_object`).
  S3 모드여도 **로컬 사본까지 지운다**: `storage.load` 가 로컬로 폴백하므로 안 지우면 되살아남.

교훈: **캐시 키에 입력(결과 이미지)이 안 들어가 있으면 그건 캐시가 아니라 오래된 값이다.**

---

## 3. read_text → detect 직렬 대기 🟡

- 둘 다 `classify` 결과(item)만 쓰고 서로 독립인데 순서대로 돌고 있었다 → VLM 왕복 1회 낭비.
- LangGraph 팬아웃/조인으로 변경:

```python
g.add_edge("classify", "read_text")
g.add_edge("classify", "detect")
g.add_edge(["read_text", "detect"], "generate")   # 둘 다 끝나야 generate
```

- 리스트 형태 `add_edge([...], target)` = **조인(대기) 엣지**. 한쪽만 끝나서는 generate 가 안 돈다.
- 두 가지가 같은 superstep 에서 돌기 때문에 **쓰는 state 키가 겹치면 안 된다**
  (reducer 없는 키를 둘이 쓰면 `InvalidUpdateError`). read_text 는 `item_texts/item_box`,
  detect 는 `anchors/detect_failed` 라 안전.
- generate 로 들어오는 다른 엣지(validate_result 재시도, mark_gate_retry)는 조인과 별개 트리거라
  재시도 루프에는 영향 없음.

---

## 4. TEXT_LOCK 사후 검증 🟢

- TEXT_LOCK 은 생성 **전에** "이 글자 그대로 둬"라고 부탁만 한다. 지켜졌는지 확인하는 곳이 없었다.
- `_verify_targets()`: verify 체크리스트 = detect 앵커 + read_text 글자
  (`{"category": "print", "what": 'text: "ABC"', "where": "top-center"}`).
  - detect 가 같은 글자를 이미 print 앵커로 올렸으면(대소문자 무시 부분일치) 중복 제외
  - 최대 `TEXT_LOCK_MAX_LINES` 개
- 뭉개지면 `preserved:false` → 게이트 실패 → 사라진 글자를 프롬프트에 넣어 재생성 → 그래도면 composite.
- `rules_verify.md` 의 Rule 2 ("printed text must remain EXACTLY identical")가 이미 있어서
  프롬프트는 안 건드림. 말풍선은 `bubbles()` 가 `text:` 접두어를 🏷️ 로 바꿔 보여준다.
- ⚠️ 트레이드오프: VLM 이 작은 글씨를 잘못 읽으면 멀쩡한 생성본도 composite 로 빠진다
  (**false fail**). eval 에서 composite 비율이 튀는지 봐야 함.

---

## 5. 테스트 쪽에서 바뀐 것 (1차)

- 기존 `fake_verify` 가 요청 개수와 상관없이 항상 1건만 답했다 → 글자 항목이 추가되자
  "답이 모자람 = 게이트 실패"로 루프를 돌다 가짜 가드 이터레이터가 바닥나 `StopIteration`.
  → **요청 항목마다 1건** 답하도록 수정. (가짜가 실제 계약 — `expected` 개수 — 을 안 지키면
  이런 식으로 엉뚱한 곳에서 터진다.)
- `_expected()` / obs output 기대값에 `detect_failed` 추가.

## 6. 1차 리뷰 반영 (reviewer 13건 중 주요)

| 지적 | 조치 |
|---|---|
| 글자 30줄을 전부 게이트 조건으로 → 오판으로 composite 급증 | `_key_texts()`: 박스 큰 순 최대 8개, 1글자·80자 넘게 잘린 문장 제외, print 앵커하고만 중복 비교 |
| S3 `DeleteObject` 권한 없으면 비용 다 쓴 변환이 500 | `_clear_quality()` 로 감싸서 무시 |
| 성적표 404 폴링이 무한히 쌓임 | 상한 2분, 새 업로드/변환 시 끊기 + 세대 토큰으로 늦은 응답 버림 |
| detect 실패여도 생성 루프 다 돌고 버림 | `plan` 노드에서 **생성 전에** composite 로 |
| 합성본(원본 픽셀)에 "보존 안 됨" 경고 | composite 결과는 글자 항목 없이 앵커만 검사 |
| eval/dev 호출부가 새 예외에 죽음 | `detect_defects(strict=False)` 기본값 유지, 파이프라인만 `strict=True` |

## 7. 추가로 한 것 (3 · 6 · 7 · 9)

### 3. OCR 가드 살리기
- 원본 글자(read_text 가 이미 읽음, `_key_texts` 로 고른 것) vs 결과 글자(결과를 한 번 더 읽음)를
  `metric.text_match` (줄 단위·순서 무관)로 비교. `ocr_match` recall ≥ 0.95 = hard.
- 결과 읽기가 깨지면(`{}`) "글자 전부 사라짐"이 아니라 `ocr_read_failed` — `read_item_text(strict=True)`.
- `no_added_text` 는 **soft 로 내림**: 두 번의 VLM 읽기가 줄만 다르게 쪼개도("NIKE AIR" → "NIKE"+"AIR")
  "새 글자"로 잡힌다. 새로 얹힌 자막은 check_photo 가 따로 잡음.
- hard fail 2회 → 예전엔 blocked(원본). 이제 **composite**, 그것도 실패해야 blocked.
- 가드의 dino 값을 `visual_similarity` 로 재사용 (DINOv2 중복 계산 제거). dino 는 soft 라 계산 실패해도 500 안 냄.
- 비용: 원본에 글자가 있는 물건만, 생성 1회당 VLM 1회 추가.
- ⚠️ 임계값 0.95 는 경험값. eval 로 오차단률 봐야 함.

### 6. `plan` — 생성 전 composite
```python
detect_failed                          → "detect_failed"
len(item_texts) >= 12 (설정, 0=끔)      → "text_heavy"
len(anchors) >= N (기본 꺼짐)           → "many_defects"
```
- 오리기가 실패하면 그때 정상 생성으로. 같은 실행에서 이미 실패한 오리기는 다시 안 부름.
- 이유는 `composite_reason` 으로 응답·inspect·Langfuse 에 남음.

### 7. judge 백그라운드
- 라우트: `run_transform(defer_judge=True)` → 응답 → `BackgroundTasks` 로 `judge_later()`.
- 점수는 같은 Langfuse 트레이스에(`trace_context={"trace_id", "parent_span_id"}`).
- 채점 중 같은 쌍이 재변환되면(결과 이미지 바뀜) 저장 전·후 두 번 확인해서 옛 점수를 버림.
- 새 `GET /api/quality/{file_id}/{preset}` — `/storage` 정적 마운트는 로컬 디스크만 서빙해서 S3 모드에선 늘 404 였다.
- eval/dev 는 기본값(동기 채점) 그대로.

### 9. dev 경로 = 운영 그래프
- `build(dev=True)`: generate 자리에 `use_provided`, 재생성·composite 루프만 뺀 **같은 그래프**.
- 매 호출 빌드 → 테스트의 monkeypatch 된 노드 함수가 반영됨.

## 8. 2차 리뷰 반영

- **UI 정직성**: blocked(원본을 보여줌)·composite·detect_failed 가 화면에 안 드러났다 →
  응답에 `status` 추가, 게이트 배지를 **무엇을 보여주는지**(원본 / 합성 / 생성)에 따라 다르게.
  예: detect 실패 + 합성 실패 후 생성본인데 "원본 사진 사용"이라고 쓰면 거짓말.
- **XSS**: `considered`(VLM 출력)를 `innerHTML` 로 넣고 있었다 — 사진 속 글자로 프롬프트 주입 →
  `<img onerror>` 가능. `textContent` 로.
- 채점 대기 중엔 라우트가 quality 파일을 읽지 않음 (삭제 실패 시 옛 점수 반환 방지).
- 가드 불합격 → 합성 성공 시에도 어떤 가드가 걸렸는지 `guard_report` 유지.

## 남은 것

- [ ] eval 로 확인: OCR 가드 오차단률, 글자 사후검증 false fail, `text_heavy` 기준(12줄), composite 비율
- [ ] genai 클라이언트 타임아웃
- [ ] OCR 읽기와 check_photo 병렬화 (생성 1회당 VLM 2회가 직렬)
- [ ] 가드 seed 재시도 예산이 photo 재생성과 공유됨 — 2번째 시도에서 처음 가드 실패하면 재시도 없이 composite (의도 명시 필요)
- [ ] feature/defect 좌표 가드(`_guard_anchors`, `crop_sim` 없음)는 여전히 죽은 경로 — 살릴지 지울지
