# 2026-10-05

1. 모델 교체 eval — objects · judge · auto_feedback 을 lite 로?
2. 지금 VLM 을 부르는 곳
3. judge 를 운영에서 뺌
4. 옛 하자 스위트(test/eval) 삭제 · 문서 정리
5. 다음 할 일
6. storage 정리 — S3 + SQLite 만

## 1. 모델 교체 eval — objects · judge · auto_feedback 을 lite 로?

10-04 §(모델 점검)의 남은 일. 같은 입력 · 같은 시점에 `gemini-3.8-flash` 와 `gemini-3.5-flash-lite` 를 번갈아 불렀다.

- judge · auto_feedback: 생성은 매번 달라서 **새로 생성하지 않고** 이미 있는 eval 실행(`eval/results/runs/*`)의 원본 · 결과 73쌍을 그대로 썼다. 사람 채점(`results/reviews/*/claude.csv` 등)이 붙은 건 17쌍 (실패 12)
- objects: 묶음 12개를 eval 사진으로 만들었다 — 서로 다른 물건 섞기 + 같은 사진의 좌우반전 · 잘라낸 판을 "같은 물건 다른 장" 으로. 실제 여러 장 업로드보다 쉽다
- 스크립트는 scratchpad 에만 (레포에 안 넣음). 비용 약 $1. Langfuse 에 `objects` 프롬프트가 없어 404 → 코드의 기본 프롬프트로 돌았다

| 호출 | 3.8-flash | lite | n |
|---|---|---|---|
| objects 개수 맞음 / 묶기 완전 일치 | 12/12 · 12/12 | 12/12 · 12/12 | 묶음 12 |
| judge fidelity 평균 | 3.27 | 4.15 | 73 |
| judge — 사람이 실패로 본 결과의 fidelity | 2.5 | 3.42 | 17 |
| judge — fidelity ≤2 로 사람 판정 맞힘 | 11/17 | 10/17 | 17 |
| auto_feedback 별점 평균 (같은 별점 37/73, 상관 0.48) | 3.47 | 4.62 | 73 |
| 응답 시간 중앙값 | 2.3~5.5초 | 1.4~3.2초 | |

- **objects → lite** (`app/core/vlm.py` `DEFAULT_MODELS`). 실제 업로드 사진이 쌓이면 다시 본다
- **judge · auto_feedback 유지**. lite 는 judge 를 약 0.9점 후하게 매기고, auto_feedback 은 거의 칭찬만 한다
  (3.8 이 "없던 가슴 로고가 생겨 못 쓴다" 고 한 결과에 lite 는 "쇼핑몰 사진처럼 잘 나왔네요")

쓴 프롬프트 (코드의 기존 문구, 한국어 번역):

- judge: "이미지1: 원본, 이미지2: 결과. 1단계: 물건에서 달라진 점을 나열. 2단계: 점수. JSON만."
- auto_feedback: "이미지1: 전(원본), 이미지2: 후(결과). 당신은 방금 이 결과 사진을 받은 판매자다. 1~5점으로 평가하고 짧은 한 줄 평을 남겨라. JSON만."
- objects: "사진들을 물건별로 묶고 사진마다 각도 · 가림 · 흐림을 답하라" (사진 장수를 넣는 기존 프롬프트)

## 2. 지금 VLM 을 부르는 곳

| 단계 | 호출 | 결정에 쓰나 | 모델 |
|---|---|---|---|
| 업로드 | objects (`detector.group_objects`) | O | lite (§1) |
| 분석 | analyze · item_text | O | 3.8 |
| 생성 뒤 | verify ∥ added_text (병렬) | O (게이트) | 3.8 |
| 생성 뒤 | check_photo | 백그라운드 확인 · 재생성 | lite |
| 응답 뒤 | judge | X | 3.8 → §3 에서 운영 제외 |
| dev · eval 만 | auto_feedback · views | X | 3.8 |

- 처음에 "auto_feedback 도 운영에서 빼자" 고 했는데 틀렸다 — 처음부터 dev(`ingest_and_feedback`)에서만 불린다
- 하자 탐지(`detect_defects`)는 10-04 에 없앴다. 하자는 analyze 의 `wear_level` 로만 본다

## 3. judge 를 운영에서 뺌

- 문제: judge 는 결정에 안 쓰는 관측값인데 변환마다 고해상도 두 장짜리 3.8 호출이 나가고, 화면에 "충실성 n/5" 를 띄운다
- 판단: 사람 판정과 11/17 만 맞는다 → 게이트를 통과해 나간 결과에 "충실성 2/5" 가 붙으면 헷갈리고, 후한 점수는 근거 없는 안심을 준다.
  "중요한 게 바뀌었나" 는 verify · added_text 가 결정용으로 이미 본다. judge 만 하는 건 현실감 점수와 설명문
- 완전 삭제는 아님: eval 에서 생성 모델 · 잠금 문구를 비교할 때 현실감 숫자가 필요하다. 사람 채점 30~50쌍이 모이면 다시 재고 정한다
- DINO 와의 차이: DINO 는 임베딩 거리라 작은 로고 · 글자 하나에 둔하고, judge 는 의미 차이를 보지만 모델 · 프롬프트에 따라 흔들린다

한 것:

- `pipeline.run_transform`: `defer_judge` → `defer_signals` (누끼 비교만 미룸) + `score_quality` (기본 False, eval · dev 만 True)
- 그래프 뒤 두 번째 성적표 삭제는 채점할 때만 — 운영은 S3 삭제 왕복을 하나 아낀다
- transform 라우트의 judge 백그라운드 · `quality` 응답 · `GET /api/quality` 삭제. `QualityReport` · `judge_pending` 스키마 삭제
- 프론트 성적표 상자 · 폴링 삭제. dev 페이지의 judge 칩은 dev 경로가 채우므로 그대로
- `eval/run.py` · `services/ingest.py` 는 `score_quality=True`

| 누가 | 잡은 것 | 처리 |
|---|---|---|
| reviewer | `judge_and_save` · `item_signals_and_save` docstring 이 옛 구조 | 고침 |
| reviewer | 운영에서도 성적표 삭제가 매번 두 번 (S3 왕복) | 두 번째는 `score_quality` 때만 |
| reviewer | README "judge 축별 Score" 를 운영 기능처럼 소개 | "(eval·dev 실행 때)" |
| reviewer | 스킬 문서 — 운영 결과면 judge 칸이 비는 게 정상임을 안 적음 | 적음 |
| reviewer | `GET /api/quality` 삭제로 file_id 만 알면 읽던 공개 엔드포인트가 줄었다 | (보안 이득) |
| tester | 옛 동작 테스트 9개 삭제, 남는 의미(응답 뒤 누끼 비교만 · judge 안 부름 · original 은 채점 안 함)로 수정 · 1개 추가 | 680 통과 |
| tester | `test_journey_real.py`(eval 마커, 실제 API)가 한 번 analyze JSON 끊김으로 실패 → 재실행 통과 | analyze 는 3.8 이라 objects 교체와 무관. 흔들림으로 기록만 |

## 4. 옛 하자 스위트(test/eval) 삭제 · 문서 정리

- `backend/test/eval/` 가 10-04 에 없앤 `detector.classify` · `detect_defects` 를 불러 `make eval` 이 깨진 상태였다 → 삭제
- `make eval` = `cd backend && python eval/run.py --analyze-only` (지금 eval 사진으로 분류 정확도)
- `make test` 의 `-m "not eval and not e2e"` 는 그대로 — `test_journey_real.py` 가 아직 eval 마커(실제 VLM)를 쓴다. 한 번 지웠다가 되돌렸다
- `detect_failed` 는 지금도 쓰는 플래그(analyze 실패)라 남김

## 5. 다음 할 일

- [x] storage 정리 → §6 — `real_defects` · `text_check` 를 `eval/data/` 로, dev 라우트의 로컬 폴더 목록(`storage.BASE` glob)을 storage 추상화로, 로컬 storage 비우기 (DB 는 SQLite 그대로)
  - `.env` 는 이미 `STORAGE_BACKEND=s3`, `/storage/{kind}/{name}` 은 이미 storage 를 거쳐 서빙 — "S3 면 사진이 안 보인다" 고 처음에 말한 건 틀렸다
- [ ] 생성 뒤 호출 합치기 — verify + added_text 한 호출 (설정으로 켜고 끔), 다음에 check_photo 까지 (왕복 2~3초)
- [ ] 판단형 프롬프트에 확신도 — verify · added_text · check_photo, inspect 에 남겨 사람 채점과 비교
- [ ] 위 두 실험을 중고나라 새 사진 15장으로 (사용자가 모음, 라벨은 돌리기 전에) — 같은 채점으로 judge 재평가도
- [x] 모델 교체 eval → §1

## 6. storage 정리 — S3 + SQLite 만

- 목표: 로컬 `backend/storage` 를 비우고 S3(이미지 · JSON) + SQLite(`data/carret.db`, 그대로) 만 쓴다
- 처음 판단이 틀렸다: "S3 모드면 사진이 안 보인다" 고 했는데 `main.py` 의 `/storage/{kind}/{name}` 이 이미 storage 를 거쳐 서빙하고, `.env` 도 이미 `STORAGE_BACKEND=s3` 였다
- 진짜 원인: 한 장 업로드(`routes/images.py`)가 설정과 상관없이 **로컬 디스크에 직접** 썼다 — 로컬 `storage/original` 이 계속 쌓인 이유. 읽기가 로컬로 폴백해서 티가 안 났다

한 것:

- 업로드 두 곳(`/upload`, `/upload-url`)을 `storage.save` 로 — 이름은 `.jpg` 로 통일 (normalize 가 JPEG 로 다시 쓴다, items.py 와 같게), 깨진 이미지 · 압축 폭탄은 400
- `storage.list_files(kind)` (로컬 · S3 paginator, `Delimiter="/"`, 폴더 마커 제외) → dev 원본 · 갤러리 · 결과 목록이 S3 에서도 보인다. 인박스는 사용자가 파일을 던지는 로컬 폴더라 그대로
- `/storage/...` 는 이미지(original · result)와 파이프라인이 만드는 이름만 — 그동안 성적표 · 분석 · 묶음 JSON 도 이 주소로 열렸다
- S3 오류 중 404 가 아닌 것(권한 · 요청 제한)은 로그 — 로컬을 비우면 "파일 없음" 으로만 보여 장애가 감춰진다
- `text_check` · `real_defects` → `backend/eval/data/` (real_defects 는 남의 사진이라 git 밖). 안 쓰던 `dataset` 분기 · `original_of` 삭제

| 누가 | 잡은 것 | 처리 |
|---|---|---|
| reviewer | 업로드 이름이 `.png` 인데 내용은 JPEG — 응답 형식 · dev 목록(.jpg 만) · dev.js 주소가 어긋남 | `.jpg` 로 통일 |
| reviewer | normalize 예외가 500 으로 샘 | 400 |
| reviewer | S3 폴더 마커가 빈 이름으로 섞임 | 거름 |
| reviewer | ClientError 를 전부 "없음" 으로 | 404 외엔 로그 |
| reviewer | `/storage` 이름에 문자셋 제한 없음 (S3 GET 남용) | 정규식 |
| reviewer | dev results 가 결과마다 S3 GET 2번 + DB — 쌓이면 느려짐 | 남김 (dev 전용) |
| reviewer | `main.py` 가 S3 모드에서도 빈 `./storage` 를 다시 만든다, eval-report 스킬이 로컬 quality 를 읽는다 | 남김 — 아래 할 일 |
| tester | S3 list · 병합 · S3 모드 업로드가 로컬에 안 쓰임 · dev results 목록 · quality 404 · 깨진 이미지 400 · 이름 제한 — 7개 추가 | 686 통과 |

- [ ] 로컬 storage 비우기 — S3 에 없는 파일을 올릴지 사용자 확인 뒤
- [ ] dev results 페이지 단위 조회 (결과가 쌓이면 느려짐)
- [ ] eval-report 스킬을 S3 / `GET /dev/results` 기준으로
