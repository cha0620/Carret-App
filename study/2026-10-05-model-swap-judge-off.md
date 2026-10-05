# 2026-10-05

1. 모델 교체 eval — objects · judge · auto_feedback 을 lite 로?
2. 지금 VLM 을 부르는 곳
3. judge 를 운영에서 뺌
4. 옛 하자 스위트(test/eval) 삭제 · 문서 정리
5. 다음 할 일
6. storage 정리 — S3 + SQLite 만
7. 생성 뒤 검사 한 호출 · 확신도 · 말풍선 삭제
8. 로컬 storage 비우기 · 근거 사진 칸 분리
9. 머지 정리 · eval 데이터 11개 · 여러 물건 같이/따로

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
- [x] 생성 뒤 호출 합치기 — verify + added_text 한 호출 (설정으로 켜고 끔), 다음에 check_photo 까지 (왕복 2~3초) → §7
- [x] 판단형 프롬프트에 확신도 — verify · added_text · check_photo, inspect 에 남겨 사람 채점과 비교 → §7
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

- [x] 로컬 storage 비우기 — S3 에 없는 파일을 올릴지 사용자 확인 뒤 → §8
- [x] dev results 페이지 단위 조회 (§10)
- [x] eval-report 스킬을 S3 / `GET /dev/results` 기준으로 (§10)

## 7. 생성 뒤 검사 한 호출 · 확신도 · 말풍선 삭제

§5 의 남은 일 1 · 2번.

### 7-1. verify + added_text 한 호출 (실험, 기본 꺼짐)

- 지금: 생성 뒤 verify(생성본 한 장, 마크 보존)와 added_text(원본 · 생성본, 없던 글자)를 병렬로 부른다
- 한 것: `VERIFY_COMBINED=true` 면 원본 · 생성본을 한 번에 보내 `checks` 와 `added` 를 같이 받는다
  (`detector.verify_combined`, `pipeline._verify_combined`, 프롬프트 `verify_combined.md`, 생각 상한 3072)
- 실패 의미는 따로 부를 때와 같게: 마크 판정이 깨지면 `verify_failed` → 배경 교체, `added` 만 깨지면 경고 로그만 남기고 막지 않음,
  `ADDED_TEXT_GATE=false` 면 added 무시. 마크가 없으면 예전처럼 added_text 만
- 기본을 바꾸는 건 §5 의 새 사진 15장 eval 로 판정이 같은지 본 뒤. check_photo 까지 합치는 건 그다음

### 7-2. 판단형 프롬프트에 확신도

- verify · added_text · check_photo 응답 형식에 `confidence`(0~1). 파서(`_confidence`)는 10-04 에 이미 있었다
- 게이트는 안 본다 — inspect 의 checks · added_text · photo_check 에 남겨 사람 채점과 비교만
- Langfuse 에 4개 다시 등록: verify_v2 v2, check_photo v2, added_text v1, verify_combined v1.
  `added_text` 가 v1 — 그동안 Langfuse 에 없어서 코드 fallback 으로 돌았다

### 7-3. 마크 말풍선 삭제

- 계기: 합친 호출은 두 장을 보내서 VLM 이 원본 기준 박스를 줄 위험이 새로 생겼다 (reviewer).
  사용자 결정 — 말풍선은 이제 없앤다
- 지운 것: 프론트 `renderBubbles` · `#overlay` · CSS, dev 의 초록 박스 · "말풍선 위치 틀림" 버튼, 응답 `bubbles` · `Bubble` 스키마 ·
  `detector.bubbles`, verify 프롬프트의 `box_2d`
- 남긴 것: DB `bubbles` 열(새 행 NULL, 다시 기록해도 옛 값 유지), 피드백 태그 `bubble_wrong`(옛 피드백)
- "edge tag 남기자" 는 eval 데이터셋의 `edge_tags` 얘기 — 말풍선과 무관해서 그대로

| 누가 | 잡은 것 | 처리 |
|---|---|---|
| reviewer | 합친 호출에서 added 만 깨지면 "확인 못 함" 이 로그에 안 남음 | 경고 로그 |
| reviewer | 재시도 로그 이름이 둘 다 `verify` | `verify_combined` |
| reviewer | 없던 글자 로그 중복 | `_log_added` |
| reviewer | 두 장을 보내 말풍선 좌표가 원본 기준일 위험 | 말풍선 삭제로 사라짐 |
| reviewer | added 만 깨져도 재시도 안 함 · 원본 MIME 고정 · 앵커 글자 프롬프트 주입 | 남김 (실험 플래그, 기존과 같은 위험) |
| reviewer | `verify_combined.md` 가 기존 조각을 복사 — 한쪽만 고치면 어긋남 | 남김 — 기본값으로 정하면 조각으로 묶기 |
| reviewer | 확신도가 0~100 · 문자열이면 버려짐 | 남김 — 실제로 얼마나 비는지 eval 에서 |
| reviewer | 다시 기록할 때 옛 `bubbles` 를 NULL 로 덮음 | UPSERT 에서 뺌 |
| tester | 합친 호출 9개 추가 (파싱 · 실패 · added None · 게이트 끔 · 시드 목록) | 695 통과 |
| tester | 말풍선 전제 테스트 10개 수정, `test_browser.py` 삭제, 옛 bubbles 유지 1개 추가 | 696 통과 |

- [x] 합친 호출 vs 병렬 두 호출 — 새 게시글 11장, 생성본 7장 (§11)
- [ ] 확신도가 실제로 얼마나 들어오는지, 사람 채점과 맞는지
- [ ] eval `test_journey_real` 이 이제 `checks` 1개 이상을 본다 — 다음 eval 때 확인

## 8. 로컬 storage 비우기 · 근거 사진 칸 분리

### 8-1. 로컬 storage 비우기

- 로컬 original 53 · result 49 · quality 91 개가 S3 에 하나도 없었다 (DB 결과 50개 중 44개가 이 원본)
- 사용자 결정: S3 에 올린 뒤 로컬 삭제. 정규화 없이 같은 이름 · 같은 내용으로 올리고, S3 목록과 대조해 빠진 게 0 인 걸 확인한 뒤 지웠다
- 남긴 것: `storage/inbox`(dev 가 던져 두는 로컬 폴더), `storage/result/_review/`(09-27 사람 검토용 하위 폴더 — 처리 미정)

### 8-2. 상품 칸 · 근거 칸 나눠 받기

- 문제: 10-04 부터 한 번에 올린 사진을 AI(objects)가 상품 / 근거로 나눴다. 판매자는 어느 게 보증서인지 이미 안다
- 사용자 결정: **칸이 종류를 정하고 AI 는 묶음 · 각도 · 근거 종류만**. 마크 클로즈업은 **근거 사진으로만** (생성 · 검사엔 안 씀)
- 한 것:
  - `/items` · `/items/{id}/files` 가 `proof_files`(사진만)를 받고 사진마다 `slot` 저장. 새 물건은 상품 사진이 있어야 한다
  - `listing._enforce_slots`: 한 칸뿐인 물건은 그 칸 종류로, 칸이 섞이면 다른 칸 사진을 새 물건으로 뗀다. 파는 상품이 하나면 근거를 거기에 연결.
    `slot` 없는 옛 사진은 AI 판단 그대로
  - 사용자가 고친 묶음(`apply`)도 새 사진의 칸이 내 물건 종류와 다르면 칸 종류의 새 물건으로
  - 근거 종류 `mark`(정품 마크 · 시리얼), objects 프롬프트에 칸 표시 · 사진 속 지시 무시
  - 화면: 상품 칸 아래 "🧾 보증서 · 정품 마크 · 영수증" 칸. 물건이 없으면 모아 뒀다가 상품 사진과 같이, 있으면 바로 더함. 취소 버튼

| 누가 | 잡은 것 | 처리 |
|---|---|---|
| reviewer | 고친 묶음(apply)에서 칸이 안 지켜짐 | 칸이 다르면 새 물건 |
| reviewer | 물건 수 상한에서 떼려던 사진이 물건 없음이 됨 | 원래 물건에 남김 |
| reviewer | 대기 근거가 URL 업로드 뒤 엉뚱한 물건으로 감 · 취소 없음 · 근거만으로 새 물건 | URL 때 비움 · 취소 버튼 · 서버 400 |
| reviewer | `slot` 기본값 "product" 가 옛 사진에 박힐 수 있음 | None |
| reviewer | 근거로 바뀐 물건이 상품 이름을 물려받음 | "근거 사진" |
| reviewer | "PROOF box" 가 포장 상자 box 와 겹침 · 사진 속 글자 주입 | "PROOF section" · 지시 무시 문장 |
| reviewer | 근거도 장수 상한에 들어감 | 메시지에 "(근거 사진 포함)" |
| reviewer | 물건 수 상한 12(detector) · 20(listing) 이 다름 · 업로드마다 전체 재분류 비용 | 남김 (기존) |
| tester | 가짜 group_objects 인자 · 라우트 3 · normalize 3 · apply 칸 충돌 · 상한 · 근거만 추가 | 705 통과 |

- [ ] objects 프롬프트 Langfuse 재등록 — 배포 뒤 (옛 서버는 칸 표시를 모른다)
- [ ] `storage/result/_review/` 처리 (사용자 확인)
- [x] 묶음 테스트(`SHOES_AND_CARD`) — 옛 경로 회귀 테스트로 남김 (§10)

## 9. 머지 정리 · eval 데이터 11개 · 여러 물건 같이/따로

### 9-1. 쌓인 PR 머지

- #33 → #34 → #35 → #36 을 차례로 `--delete-branch` 로 머지하다 꼬였다 — #33 을 머지하며 브랜치를 지우자 #34 가 base 를 잃고 닫혔고,
  #35 는 main 이 아니라 #34 브랜치로 들어갔다. #36 도 같은 이유로 닫힘
- 교훈: 쌓인 PR 은 아래 것을 머지한 뒤 위 PR 의 base 를 main 으로 바꾸고 머지한다 (브랜치를 지우기 전에)
- 수습: #36 브랜치(위 내용 전부 포함)를 main 으로 #37 — 로컬 병합 충돌 없음, CI 통과 후 머지

### 9-2. eval 데이터 — 중고나라 게시글 11개

- `phone_img/` 11개 묶음(64장) → `intake add --pilot` (전부 dev). 사진마다 `slot` · `proof_type` · `orig_name`
- 상품/근거 기준은 처음에 박스 · 구성품도 근거로 넣었다가 사용자가 고쳤다 — "구성품이 메인이면 상품". COLLECT.md §4-1
- 박스 안에 구성품이 든 세트(보드게임 · 굿즈)는 category `pack` (새로 추가)
- 첫 사진 라벨은 Claude 초안 → 사용자가 그대로 정답으로 확정 (`--by cha0620`). 사람 정답 없는 항목 0
- 원본과 해시 대조(64/64 같음) 뒤 `phone_img/` 의 11개 폴더 삭제 — 앞으로도 받아 가면 옮기고 지운다

### 9-3. 한 사진에 팔 물건이 여러 개면 같이 / 따로

- 사용자 요청: ps5 게임 5장처럼 여러 개면 한 장으로 할지 따로 만들지 묻기. "따로" = 물건마다 한 장
- 서버: `TransformRequest.separate` (sell 하나만), 결과 키에 `-o<번호>` — 같은 사진의 다른 물건 결과가 덮어쓰지 않게
- 화면: 변환 전에 "한 장에 같이 / 물건마다 따로 / 취소", 따로면 차례로 변환해 썸네일 줄. 피드백은 무드 기준이라 따로 결과엔 숨김

| 누가 | 잡은 것 | 처리 |
|---|---|---|
| reviewer | 묻는 중 사진을 바꾸면 옛 질문이 남고 버튼이 먹통 · 분석이 늦게 끝나면 변환 버튼이 다시 켜짐 · 체크 변경 무시 | 묻는 동안 setBusy 로 잠금, 사진 바꾸면 질문 취소 |
| reviewer | 따로 변환 중 사진이 바뀌면 새 사진 위에 결과 · 실패 메시지가 덮임 | 사진 확인 후 그림, 실패한 물건 이름을 마지막에 |
| reviewer | 없는 필드 `label` · 결과 표시 중복 | `what` · `showResult` |
| reviewer | 따로 결과의 DB 행이 `-o<n>` 키로 따로 쌓임 · N배 비용 · 중간 취소 없음 | 남김 (상한은 사용자 결정) |
| tester | separate 키 접미사 · 422 두 가지 · pack 카테고리 | 710 통과 |

- [ ] 따로 만들기 물건 수 상한 · 중간 취소 (사용자 결정)
- [ ] 쌓인 PR 은 위 PR base 를 main 으로 바꾼 뒤 머지

## 10. dev 결과 페이지 단위 조회 · eval-report 를 `/dev/results` 로

- 문제: 결과가 쌓이면 `GET /dev/results` 가 항목마다 DB · json 을 다 읽어 느림. S3 면 eval-report 스크립트가 읽을 데가 없음
- 한 것:
  - 서버: `offset` · `limit`(1~500, 없으면 전부 — 옛 호출 그대로) · 응답에 `total` · `offset`. 정렬은 전체, 읽기는 이 페이지만
  - 화면: 50개씩 이어 받기. 첫 페이지에서 바로 그리고 끝에서 한 번 더, 중간엔 요약에 "받는 중 n/total"
  - `inspect_stats.py --url http://…` — dev 서버를 100개씩 읽는다 (storage 백엔드 무관). 실서버 58건 중 inspect 55건 확인
  - `SHOES_AND_CARD` 는 옮기지 않음 — 칸 없는 옛 사진 경로의 회귀 테스트, 근거 칸 경로는 따로 테스트가 있다 (주석)

| 누가 | 잡은 것 | 처리 |
|---|---|---|
| reviewer | 페이지 사이 새 결과로 목록이 밀려 중복 · 집계 부풀림 | 화면 · 스크립트에서 `(file_id, preset)` 로 거름 |
| reviewer | 페이지마다 다시 그려 쓰던 피드백이 날아감 | 첫 페이지 · 끝에서만 그림 |
| reviewer | 중간 실패를 조용히 삼킴 | "⚠ n/total 만 받음" 표시 |
| reviewer | 다시 불러오기 때 `rsShown` 이 남음 | 되돌림 |
| reviewer | 스크립트 오류 처리 · `total` 없으면 조용히 끝 · `file://` | 한 줄 오류 · `body["total"]` · http(s) 만 |
| reviewer | 페이지마다 storage 전체 LIST (S3 비용 O(N²/limit)) · 지워진 결과는 누락 가능 | 남김 — 커서 · 목록 캐시는 결과가 수천 건일 때 |
| reviewer | limit 기본 무제한 | 남김 — 옛 호출 호환, dev 라우트 |
| tester | offset 만 · limit 501 → 422 · 페이지 순서 · 잘못된 값 | 709 통과 |

- [ ] `/dev/results` 커서(before=mtime) 또는 목록 캐시 — 결과가 수천 건이 되면

## 11. 합친 호출 vs 병렬 두 호출 — 새 게시글 사진으로

- 사진: 중고나라 11개 게시글의 라벨 있는 첫 사진 11장 (15장 계획이었지만 라벨 있는 건 11장). 실행 `eval/results/runs/verify-ab-1005`
- 방법: 생성은 매번 달라 두 번 돌리면 생성 차이가 섞인다 → **한 번 생성하고 같은 생성본(`try*.jpg`)에 두 방식을 다 돌림**,
  순서는 번갈아. 마크 목록이 있는 생성본 7장 (나머지는 목록이 비어 verify 를 안 탐)
  - A = 지금 기본: `verify_and_locate(strict)` + `added_text` 병렬
  - B = `VERIFY_COMBINED`: 원본 · 생성본을 같이 보내 한 호출

| 지표 | A 병렬 | B 합침 | n |
|---|---|---|---|
| 왕복 평균 | 3.4초 | 3.8초 | 7 |
| 최종 통과(마크 보존 + 새 글자 없음) | 0/7 | 0/7 | 7 — 7/7 일치 |
| 마크 보존 판정 | 보존 3 | 보존 1 | 2건 갈림 (bcle#2 · bcle2#2 — B 만 "망가짐") |
| 호출 실패 | 0 | 0 | |

- 결론: **표본 부족 (n=7)**. 그래도 합쳐서 빨라지진 않았다 — 병렬 두 호출이 이미 한 왕복이라 합쳐 봐야 출력이 길어질 뿐.
  `VERIFY_COMBINED` 기본 꺼짐 그대로
- B 가 마크에 더 엄격 — 원본을 같이 보니 비교가 된다. A 의 verify 는 생성본만 본다. 어느 쪽이 맞는지는 사람 눈으로 (`files/none_bcle*_try2.jpg`)
- ~~`added_text` 가 원본 브랜드 글자를 새 글자로 올림~~ → **눈으로 보니 오탐이 아니었다**: 생성 모델이 사진 아래에
  "MERIDA" · "ULTEGRA" · "GIANT" 캡션을 지어 넣었고(bcle#1 · bcle2 둘 다), iPhone 은 상자를 없애고 "iPhone" 글자를 새로 그렸다.
  막힌 7장 중 6장은 막는 게 맞았다. bcle#2 만 프레임 GIANT 가 멀쩡해 보여 B 의 "망가짐"은 과한 판정으로 보인다
- 같은 실행의 judge(fidelity/realism/trust, 1~5): 대부분 4/3/4, 실제 결과는 generate 3 · composite 8

- [x] bcle · bcle2 try2 를 눈으로 — bcle2#2 는 ULTEGRA 캡션이 있어 막는 게 맞음, bcle#2 는 B 가 과함

### 11-1. 자체 평가 (Claude 가 원본 · 결과 · 시도를 나란히 보고)

| 항목 | 결과 |
|---|---|
| 최종 결과가 쓸 만한가 | 10/11 — bcle 배경 교체본에서 앞바퀴 왼쪽이 잘림 (원본엔 바퀴 전체) |
| 가짜가 나간 결과 | 0/11 — 지어낸 글자 · 바뀐 물건은 전부 게이트가 막음 |
| 막힌 생성본 중 막는 게 맞았던 것 | 6/7 |
| 생성 성공 (generate) | 3/11 — 나머지는 배경 교체 (글자 많음 4 · 게이트 3 · 잘림 1) |

- 안전(가짜 안 나감)은 됐다. 약한 곳은 **생성 성공률** — 막힌 이유 대부분이 "캡션 · 브랜드 글자를 지어 넣음"
- [ ] 생성 프롬프트에 "사진에 글자 · 브랜드 캡션을 새로 넣지 말 것" — 같은 11장으로 전후 비교 (약 $0.5)
- [ ] 배경 교체에서 물건이 잘리는 경우 (bcle 앞바퀴) — 누끼 박스 확인
