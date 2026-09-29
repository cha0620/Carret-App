# 2026-09-29

1. 테스트 정리 1차: 이력 확인 테스트 걷어내기
2. 테스트 정리 2차: 파라미터 중복 합치기
3. test_pipeline 을 시나리오로 다시 쓰기
4. 데이터셋 설계 — 숫자로 말할 수 있을 만큼
5. 파일럿 실행 · Gemini 한도 · 파이프라인 시간 분석과 줄이기

---

## 1. 테스트 정리 1차: 이력 확인 테스트 걷어내기

### 1-1. 문제

유닛 테스트 1760개 중 859개가 파라미터 케이스이고, 그 사이에 "지운 함수가 없나"·"옛 env 키를 무시하나" 같은
**이력 확인** 테스트가 섞여 있었다 (09-27 §15 남은 일). 이런 테스트는 기능을 지킬 때마다 하나씩 늘어나서
지금 코드가 무엇을 하는지가 아니라 **예전에 무엇이 있었는지**를 기록하게 된다.

분포 (수집 기준, `backend/test/software/unit`):

| 파일 | 전체 | 함수 | 파라미터 케이스 |
|---|---|---|---|
| test_pipeline.py | 631 | 342 | 289 |
| test_detector.py | 270 | 118 | 152 |
| test_compositor.py | 81 | 71 | 10 |
| test_guards.py | 72 | 40 | 32 |

### 1-2. 기준

- **지운다** — 없어진 이름이 없는지(`not hasattr`, `not in model_fields`, 프롬프트에 옛 필드 문자열이 없는지)만 보는 것.
  되살아나도 동작이 깨지는 게 아니면 테스트가 지킬 대상이 아니다. git 이력과 study 가 이미 기록한다.
- **지운다** — 같은 동작을 일반 테스트가 이미 지키는 "옛 이름 무시".
  예: 옛 env 키 무시는 `test_settings_survives_unknown_env_var` (extra="ignore") 하나로 충분하다.
- **남긴다** — 이름에 legacy·old·no_longer 가 붙었어도 **지금 코드가 실제로 타는 경로**.
  예: 옛 DB 마이그레이션, eval·dev 가 부르는 옛 detect 함수, 부분 문자열 불일치 규칙.
- **섞여 있으면** 긍정 확인(지금 있어야 할 것)만 남기고 부정 줄을 뺀다. 이름도 지금 동작으로 바꾼다.

### 1-3. 지운 것 (20케이스)

| 파일 | 테스트 | 케이스 | 이유 |
|---|---|---|---|
| test_guards.py | `test_removed_coordinate_guard_api_is_gone` | 1 | 없어진 이름 확인뿐 |
| test_guards.py | `test_removed_ocr_guard_api_is_gone` | 3 | 〃 |
| test_config.py | `test_ocr_guard_setting_removed` | 1 | 〃 |
| test_config.py | `test_ocr_guard_old_env_ignored` | 1 | 옛 env 무시 — 일반 unknown env 테스트가 지킴 |
| test_config.py | `test_composite_first_min_anchors_removed_and_old_env_ignored` | 1 | 〃 |
| test_pipeline.py | `test_settings_rejects_removed_min_anchors_attribute` | 1 | 위와 중복, pydantic 동작 확인 |
| test_pipeline.py | `test_removed_judge_entrypoints_are_gone` | 1 | 없어진 이름 확인뿐 |
| test_prompts.py | `test_classify_prompt_is_callable_not_constant` | 1 | 옛 상수 부재 확인. 호출은 바로 위 verbatim 테스트가 함 |
| test_prompts.py | `test_analyze_template_has_no_old_classification_fields` | 5 | 옛 필드 문자열 부재. 지금 필드는 `PHOTO_TYPES` 포함 테스트가 지킴 |
| test_detector.py | `test_detect_full_ignores_legacy_item_box_key` | 1 | 옛 키 무시. 키 없음 → None 은 `test_detect_full_missing_item_box_is_none` |
| test_detector.py | `test_analyze_old_fields_ignored` | 4 | 옛 응답 필드 무시. photo_type 없음 → product 는 `test_analyze_photo_type_missing_field_logs` |

테스트 안에서 **줄만 뺀 것** (테스트는 남음):

| 위치 | 뺀 줄 |
|---|---|
| test_guards.py `test_item_dino_threshold_constant` | `PRODUCT_DINO_THRESHOLD` 부재 |
| test_detector.py `test_photo_types_constant` | `SCENES`·`_product_flag` 부재 |
| test_embedder.py `test_patch_constants` | `PATCH_FG_MIN` 부재 |
| test_vlm.py `test_callers_import_shared_get_client_not_genai` | `genai` 속성 부재 (`genai.Client(` 소스 검사는 남김) |
| test_pipeline.py 설정 기본값 테스트 | `composite_first_min_anchors`·`ocr_guard` 부재 |

긍정 부분만 남기고 **이름을 바꾼 것**:

| 전 | 후 | 남긴 확인 |
|---|---|---|
| `test_old_product_names_are_gone` | `test_state_has_item_similarity` | State 에 `item_similarity` |
| `test_old_front_nodes_are_gone` | `test_front_graph_has_analyze_and_keep_original` | `ANALYZE_ATTEMPTS == 2`, 그래프에 analyze·keep_original |
| `test_substring_is_no_longer_rescued` | `test_substring_is_not_rescued` | 6케이스 그대로 (지금 규칙) |

### 1-4. 남긴 것 (이름만 보면 지울 것 같지만)

| 테스트 | 남긴 이유 |
|---|---|
| test_store_results_route.py `legacy_*` 3개, test_db_migrate.py 옛 unique 키·남은 테이블 | 예전 DB 파일을 실제로 마이그레이션하는 경로 |
| test_detector.py `test_legacy_detect_functions_still_exist_for_eval` | `detect_defects`·`classify` 를 `app/util/evaluator.py`·`routes/dev.py`·`scripts/` 가 아직 부른다 |
| test_detector.py `test_from_box_2d_keeps_legacy_xy_keys` | 파서가 지금도 받는 입력 형식 |
| test_pipeline.py `test_plan_dev_graph_ignores_photo_type_and_wear` | dev 그래프(결과 제공)의 지금 동작 |
| test_config.py `test_settings_survives_unknown_env_var` | 옛 env 키가 `.env` 에 남아도 부팅되는지 — 위에서 지운 개별 테스트들을 이 하나가 대신한다 |

### 1-5. 결과

- 1760 → **1740 passed** (유닛 전체, 28.9초). 8파일, +3 / −88줄.
- 파라미터 케이스 정리(중복 의심: `test_composite_first_reason` 36 · `..._photo_type` 16,
  나쁜 박스 입력이 `detect_full`·`analyze` 두 곳)와 test_pipeline.py 쪼개기는 다음 단계.

### 1-6. 남은 일

- [x] 파라미터 중복 정리 — `test_composite_first_reason` vs `test_composite_first_reason_photo_type`, 나쁜 박스 입력 두 곳 → §2
- [x] test_pipeline.py(4.6천 줄, 631케이스) 를 routes / composite / run_transform 으로 쪼개기 → §3
- [ ] 옛 detect 함수(`classify`·`detect_defects`) 를 eval·dev 에서 analyze 로 옮기면 그 테스트도 같이 정리

## 2. 테스트 정리 2차: 파라미터 중복 합치기

### 2-1. 기준

파라미터 표는 **구현의 갈래 하나당 케이스 하나**면 된다. 같은 갈래를 다른 값으로 여러 번 부르거나,
같은 함수를 두 표가 따로 부르면 덜어낸다.

- `.get()` 으로 읽는 필드는 `None` 과 "키 없음"이 같은 갈래 — 하나만 둔다.
- 사유가 아닌 값은 사유와 헷갈릴 만한 것만 (예: `wear_level: light`, 대소문자 다른 `Document`).
- 우선순위는 **이웃한 두 사유가 같이 있을 때 앞이 이기는지**만. 뒤 사유 + 무관한 값 조합은 "사유 하나씩" 케이스가 이미 지킨다.

### 2-2. `_composite_first_reason` — 두 표를 하나로 (52 → 26 + 1)

구현(`app/services/pipeline.py:157`)은 detect_failed → inside_view → document → text_dense → wear_heavy
다섯 줄 `if` 사슬이고 `settings` 를 읽지 않는다. 그런데 표가 둘이었다.

- `test_composite_first_reason` (36) — `min_texts` 파라미터를 받아 `settings` 를 바꾸는데, 함수가 안 읽는 값이라 12 · 0 · 1 을 돌려도 같은 갈래.
- `test_composite_first_reason_photo_type` (16) — photo_type 을 붙이면서 따로 만든 표. 우선순위 케이스 4개가 앞 표와 그대로 겹쳤다.

하나로 합친 표(26)는 빈 state · 사유 하나씩(5) · 사유가 아닌 값(6) · 대소문자(2) · 경로를 안 바꾸는 필드(3) ·
document 는 글자 수준 무관(2) · 우선순위(6)로 나뉜다. "plan 은 글자 수 기준을 안 본다"는 `min_texts=0` 한 케이스짜리
`test_composite_first_reason_ignores_min_texts` 로 뺐다.

뺀 케이스: `None` 값 6개(키 없음과 같음), `item_texts` 개수 변형 4개, `min_texts` 변형,
옛 필드 `scene`·`text_is_product` 2개(§1 기준 — 이력 확인), 뒤 사유 + 무관 값 조합(`simple`+`heavy` 등).

### 2-3. 나쁜 박스 입력 — 두 곳에서 한 곳으로 (25 → 15)

`detect_full`(13)과 `analyze`(12)가 같은 입력 목록을 따로 들고 있었는데, 둘 다
`_from_box_2d` → `_has_box` 같은 파서를 탄다 (`app/services/ai/detector.py:141`, `:176`).

- 목록은 **analyze(지금 파이프라인이 타는 경로)** 쪽에 모았다. detect_full 에만 있던 `[None, 0, 500, 500]` 을 옮겨 13.
- detect_full 은 "박스가 깨져도 하자는 살린다"를 보는 테스트라 모양이 틀린 것 · 범위 밖 2케이스만 남겼다.

### 2-4. 결과

- 1740 → **1704 passed** (유닛 전체, 24.9초). 오늘 합계 1760 → 1704 (−56).

### 2-5. 남은 일

- [x] test_pipeline.py(4.5천 줄) 쪼개기 — §1-6 에서 이어짐 → §3 (쪼개는 대신 다시 씀)
- [ ] 남은 큰 표(`test_route_after_plan_keep_original` 20 · `test_analyze_photo_type_unknown_falls_back_to_product_and_logs` 18) 도 같은 기준으로 보기

## 3. test_pipeline 을 시나리오로 다시 쓰기

### 3-1. 문제 — 1700개가 필요한가

아니다. 앱 코드 4.1천 줄에 유닛 테스트 1.36만 줄(3.3배), `pipeline.py` 926줄에 테스트 4.5천 줄 · 603케이스.
실행(25초)은 문제가 아니고 **고칠 때 비용**이 문제였다 — 이번 달 앱 커밋 26개에 테스트 커밋 24개,
09-27 tester 한 번에 25분. 이유:

- 노드 함수마다 · 라우팅 함수마다 표가 따로 있어서, 경로 하나를 바꾸면 여러 파일이 같이 깨진다
- `monkeypatch.setattr` 321회 — 노드를 다 바꿔 끼우면 "이렇게 연결돼 있다"만 확인한다
- 기능마다 tester 가 테스트를 더하기만 했다 (지우는 단계가 없었다)

### 3-2. 판단 — 쪼개지 말고 경로 단위로 다시 쓴다

처음 계획(§1-6)은 routes / composite / run_transform 으로 **쪼개기**였는데, 쪼개면 줄 수는 그대로다.
파이프라인은 아직 자주 바뀌니 **노드 단위가 아니라 경로 단위**로 지키는 게 맞다고 봤다.

| 파일 | 무엇을 | 방식 |
|---|---|---|
| `test_pipeline_scenarios.py` | 경로 — 사진 종류 셋 × 생성·검증 / 배경 교체 / 원본 그대로 + 실패·재시도 | 실제 그래프를 `run_transform` 으로 끝까지. 바깥(VLM·생성·누끼·DINO·채점)만 `World` 가짜 |
| `test_pipeline_rules.py` | 순수 규칙 — 생성 전 판단 표, verify 에 걸 글자 고르기, 게이트 재생성 문구 | 함수 직접 |
| `test_pipeline_runtime.py` | 경로와 무관한 약속 — 백그라운드 관측 신호 취소·마감, 채점 경합, 트레이스·flush, 옛 성적표 | 가짜 Future · 가짜 그래프 |

시나리오는 결과 dict · inspect JSON · 저장된 결과 이미지 색(원본/생성/합성) · DB 경로 · 외부 호출 순서를 본다.
노드 이름이나 중간 state 는 보지 않는다 — 노드를 합치거나 나눠도 경로가 같으면 안 깨진다.

`test_pipeline_retry.py`(12)도 지웠다 — 구도 반려 재생성·한도는 시나리오 `test_invalid_photo_*` 가 그대로 지킨다.
`test_pipeline_persistence.py`(DB 행 필드)는 남겼다.

### 3-3. 지킨 게 줄지 않았나 — 커버리지 + 돌연변이

줄 커버리지만으로는 "실행은 됐지만 확인은 안 한" 걸 못 잡아서, `pipeline.py` 에 버그를 28개 심어 보고 비교했다
(스크래치 스크립트, 앱 코드는 원래대로 되돌림).

| | 테스트 | 줄 | 분기 커버리지 | 돌연변이 잡음 |
|---|---|---|---|---|
| 전 (`test_pipeline.py` + `_retry.py`) | 615 | 4.7천 | 99% | 27 / 28 |
| 후 (세 파일) | 135 | 1.2천 | 99% | **26 / 28** (reviewer 반영 후 138개, §3-4) |

처음엔 24 / 28 이었다. 놓친 넷 중 둘은 진짜 빈 곳이라 채웠다:

- 늦은 누끼 비교를 **취소**하지 않아도 통과했다 → `test_validate_result_item_timeout_cancels_and_keeps_verdict`
- 배경 교체가 생성본의 가드 기록을 **안 비워도** 통과했다 (생성본 가드가 원래 비어 있었음) →
  게이트 2회 실패 시나리오에서 유사도를 낮춰 생성본 기록을 만든 뒤 합성본에서 비는지 확인

남은 둘은 결과가 같은 돌연변이라 테스트할 대상이 아니다:

- `read_text` 의 "이미 오리기 실패했으면 다시 배경 교체로 안 보냄" — `composite()` 도 같은 확인을 해서 결과가 같다
- `judge_and_save` 의 채점 직후 결과 확인 — 저장 직후 한 번 더 확인해서 결과가 같다

커버리지에서 빠진 한 줄(`read_text` 의 `text_lock` 꺼짐 분기, `pipeline.py:110`)은 그래프로는 닿지 않는다 —
`read_text` 로 가는 길이 전부 `text_lock` 을 먼저 본다. 죽은 코드 후보.

틀렸던 것 하나: 최악 경로를 "구도 반려 × 한도가 게이트 재생성 뒤에 한 번 더"로 보고 10회 생성을 기대했는데,
`gen_attempts` 는 게이트 재생성 뒤에도 이어서 세서 5 + 1 = 6회다. 코드가 맞고 내 기대가 틀렸다.

### 3-4. reviewer 가 잡은 것

돌연변이 26 / 28 이어도 빈 곳이 있었다 — 내가 심은 버그는 "분기를 바꾸는" 종류뿐이었고,
reviewer 는 **가짜가 받은 입력을 안 본다**는 걸 짚었다.

| 지적 | 반영 |
|---|---|
| 가짜들이 어떤 이미지를 받았는지 안 본다 — verify 가 원본을 봐도, 합성이 생성본을 오려도 통과 | 가짜가 받은 이미지를 원본/생성 n번째/합성으로 기록(`World.seen`)하고 경로마다 확인. DINO 가짜도 (원본, 생성본) · (원본, 합성본) · 누끼 쌍마다 다른 값 |
| 생성할 때마다 같은 이미지 — "마지막 결과를 남긴다"를 못 본다 | 시도마다 다른 색. 재생성 뒤 남은 결과·verify 가 본 이미지가 마지막 시도인지 확인 |
| verify 가 항상 물은 만큼 답한다 — `expected=len(targets)` 가 안 지켜진다 | 항목을 빠뜨리는 응답(`"short"`) 시나리오 추가 |
| 그래프 전 옛 성적표 삭제가 사실상 안 지켜진다 | 그래프가 터진 뒤에도 옛 성적표가 없는지 확인 |
| 항상 참인 확인 넷 (`split("IMPORTANT")` 등) · 모듈 수준 가짜 Future 재사용 · 가짜 시그니처가 느슨함 | 고침 |
| DB 행은 경로 4칸만 봤다 | item · gate_passed · bubbles 도 확인 |
| 반려 사유가 빈 재생성 문구, `composite_first_min_texts=0` | 시나리오 추가 |

reviewer 지적 유형으로 버그 9개를 더 심어 확인 — 전부 잡음 (**35 / 37**, 남은 둘은 §3-3 의 결과가 같은 돌연변이).
반영하지 않은 것: 재귀 한도(80)가 최악 경로(~25)보다 넉넉해서 한도를 낮춰도 테스트가 통과한다는 지적 —
한도는 "넉넉하게"가 의도라 값 자체를 고정하지 않았다. 배경색(`bg_color`) 전달 확인도 남음.

### 3-5. 결과

- 유닛 1704 → **1227 passed**. 오늘 합계 1760 → 1227 (−533).
- 파이프라인 테스트 615케이스 · 4.7천 줄 → 138케이스 · 1.3천 줄.

### 3-6. 남은 일

- [ ] `read_text` 의 `text_lock` 꺼짐 분기(`pipeline.py:110`) — 닿지 않는 코드라 지울지
- [ ] 같은 방식(경로 단위 + 돌연변이 비교)을 test_detector(255) · test_compositor(81) 에도
- [ ] tester 에이전트 지침에 "경로로 지킬 수 있으면 노드 테스트를 더하지 않는다" 넣기

## 4. 데이터셋 설계 — 숫자로 말할 수 있을 만큼

### 4-1. 문제 — README 목표(30~50장)로는 숫자가 안 선다

통과율 80% 일 때 Wilson 95% 구간:

| 표본 | 95% 구간 | 오차 |
|---|---|---|
| 8장 (지금) | 41~93% | ±26%p |
| 30 | 63~90% | ±14%p |
| 50 | 67~89% | ±11%p |
| **100** | 71~87% | **±8%p** |

50장을 경로별로 나누면 경로 하나가 15~25장 → ±15~20%p. "생성 경로 보존율 80%" 가 실제론 60% 일 수도 있다.
→ **test 100장(동결) + dev 25장(조정용)**.

### 4-2. 낼 숫자 다섯 — 데이터 보기 전에 정한다

| # | 숫자 | 정밀도 (예상) |
|---|---|---|
| ① | 보존 통과율 — 전체 · 경로별, 구간과 함께 | 전체 ±8%p, 생성 경로(약 45~50장 × 2회) ±10%p |
| ② | 생성 방식 비교 — 지금(물건까지 다시 그림) vs 배경만 생성 + 원본 누끼 | 같은 사진 짝 비교(McNemar) |
| ③ | 분류 정확도 + 안정성 (같은 사진 3번 → 다 같은 비율) | ±5~8%p |
| ④ | judge · DINO 의 AUC (자동 지표가 사람 판정을 맞히나) | 실패 15건 이상이면 ±0.16 |
| ⑤ | 평가자 일치도 kappa | — |

②의 한계를 먼저 적어 둔다 — 짝 비교에 필요한 사진 수(검정력 80%)는 차이 크기에 달렸다:
55% → 85% 는 33장, 70% → 85% 는 85장, 75% → 85% 는 155장. 100장으로는 **큰 차이만** 확실히 잡는다
(Pinterest 표의 FLUX 53% vs Canvas 84% 정도면 잡힌다).

결정 규칙도 먼저 적는다 (숫자 보고 기준을 옮기지 않게):

- judge AUC ≥ 0.75 → judge 를 안전망으로 쓴다, 아니면 관측만
- 새 생성 방식이 짝 비교에서 p < 0.05 로 앞서면 기본 경로로

### 4-3. 층 — 경로가 고르게 나오게

| 코드 | 층 | test | dev | 예상 경로 |
|---|---|---|---|---|
| none | 물건 · 하자 없음 | 25 | 5 | 생성 |
| light | 물건 · 작은 하자 | 25 | 5 | 생성 (하자 정돈 여부) |
| heavy | 물건 · 큰 하자 | 10 | 3 | 배경 교체 |
| dense | 잔글씨 많음 | 10 | 3 | 배경 교체 |
| doc | 글자가 곧 물건 | 15 | 4 | 표지 펴기 |
| inside | 일부 · 내부 | 8 | 2 | 원본 그대로 |
| edge | 경계 사례 | 7 | 3 | 포토카드 · 앨범 구성품 · 보드게임(document 과민/과소), 반사 · 투명 |

### 4-4. 모으기 — 사람이 직접, 규칙대로

자동 수집은 하지 않는다: 중고나라(네이버 카페)는 로그인·가입이 필요한 글이 많고 약관이 자동 수집을 막는다.
브라우저 자동화로 로그인 계정을 쓰는 것도 사실상 스크래핑이다. 130장은 손으로 1.5시간이면 된다.

손으로 고르면 좋은 사진만 고르기 쉬워서 규칙을 먼저 정했다 (`backend/eval/COLLECT.md`):
검색어는 표에서만 · 최신순 · 조건에 맞는 첫 게시글의 첫 사진 · 검색어당 최대 3장 · 제외 기준은 미리
(사람이 주인공 · 스크린샷 · 콜라주 · 물건이 안 보임). 흔들림 · 어두움 · 워터마크는 제외하지 않는다 — 실제 사진이다.
발표할 땐 "층별 할당 표본, 게시 사진 약 750px" 라고 밝힌다.

### 4-5. 도구

| 파일 | 하는 일 |
|---|---|
| `backend/eval/COLLECT.md` | 규칙 · 층별 목표 · 검색어 · 저장 방법 · 라벨 방법 |
| `backend/eval/intake.py` | `status`(층별 현황) · `add`(inbox → images/ + dataset.json, 중복 사진 검사, dev/test 나눔) · `export` / `merge`(라벨 CSV) |
| `backend/eval/run.py --split test` | 동결된 test 만. 라벨 안 된 사진은 건너뛴다 |

- dev/test 는 층마다 목표 비율(dev / (test + dev))을 모인 수에 비례해 따라가게, 층별 시드로 섞어서 단다.
  한 번 단 split 은 바꾸지 않는다 (test 동결)
- `add` 는 하나라도 틀리면(이름 규칙 · 같은 이름 · 같은 사진) 아무것도 옮기지 않는다. `merge` 도 값 하나라도 틀리면 반영 안 함
- 라벨 CSV 는 BOM 을 붙인 UTF-8 — 엑셀이 한글을 깨지 않게. key_texts 는 `|` 로 구분
- `dataset.json` 은 한 항목 한 줄 — 라벨 고친 diff 가 그 사진 줄만 바뀌게. 기존 8장은 dev 로 옮겼다 (이미 본 사진)

tester 가 테스트 13개(intake 11 · run 2). 지적 둘 반영: run.py 가 라벨 "비어 있지 않음"만 보던 걸 intake 처럼
"허용 값인지"로, `"split": null` 을 dev 로 (둘 다 지금 쓰는 곳은 없지만 기준을 맞춤). eval 도구 90 passed.

reviewer 가 잡은 것 (모으기 전에 고침):

| 지적 | 반영 |
|---|---|
| 라벨을 반쯤 달고 사진을 더 모아 `add` 하면 `labels.csv` 를 빈 칸으로 덮어쓴다 | 이미 채운 칸을 살려서 다시 쓴다 |
| 사진을 옮긴 뒤 저장 — 중간에 멈추면 dataset 에 없는 사진이 남고, 쓰다 멈추면 dataset.json 이 깨진다 | 복사 → 임시 파일에 저장 후 바꿔 끼우기 → inbox 원본 삭제. 저장 전에 멈추면 복사본을 지운다. `status` 가 어긋난 파일을 보여 준다 |
| 조금씩 add 하면 dev 가 "몇 번째로 모은 사진"(= 검색어 순서)으로 정해진다 | 층마다 dev·test 자리를 미리 섞어 두고 k 번째 사진이 k 번째 자리 |
| 기존 8장이 dev 목표를 먹는다 (규칙 없이 고른 사진) | 층 목표에서 빼고 dev 에 따로 (`legacy_stratum` 에 옛 값) |
| test 동결을 강제하지 않는다 | split 을 `splits.jsonl` 에 남기고 `status` 가 대 본다. test 사진의 사람 라벨을 바꾸려면 `--relabel` |
| 엑셀: cp949 로 다시 저장 · 빈 줄 · 날짜/지수로 바뀐 글자 | cp949 도 읽는다 · 빈 줄 건너뜀 · 날짜·숫자처럼 보이는 key_texts 경고 · COLLECT 에 불러오기 방법 |
| urls.txt: 이름에 공백 · 탭 · BOM · 모양이 틀린 줄이 조용히 넘어감 | 마지막 칸이 주소, BOM · 탭 받음, 틀린 줄 · 중복 · inbox 에 없는 이름은 경고 |
| heic 등은 조용히 빠진다 · 층 목표를 넘기면 전부 test 로 | 둘 다 `add` 가 거부 |
| `fetch.py` 가 게시글 주소의 HTML 을 사진 이름으로 저장한다 | 응답이 image/* 일 때만 저장 |
| 초안 라벨(`claude-draft`)을 사람이 확인할 길이 없다 | 라벨 CSV 에 같이 나온다 |
| run.py 가 어떤 split 으로 돌렸는지 안 남긴다 | meta.json 에 split · only |

미룬 것: `ambiguous` 따로 집계(report), 크기만 바꿔 다시 저장한 같은 사진 검사, "판매자 한 장" 확인.
eval 도구 테스트 98 passed (intake 18), 유닛 1247 passed.

### 4-6. 남은 일

- [ ] 사진 125장 모으기 (`COLLECT.md`) + 라벨 (실행 전에). 20장은 다른 사람도 라벨
- [ ] 두 번째 평가자 정하기 — 없으면 혼자 채점 + 며칠 뒤 40장 재채점(자기 일치도, kappa 보다 약한 근거라고 밝힌다)
- [ ] report.py: 모든 비율에 Wilson 구간 · analyze 안정성 · 두 실행 짝 비교(McNemar) · 물건 단위 통과율 · split 별 · ambiguous 따로
- [ ] review.html: 순서 섞기 · 경로 · 지표 숨기기
- [ ] 실행: analyze × 3 (전체, 약 $2) → 전체 × 2 (test, 약 $8) → 채점 → 새 생성 방식 × 2 (약 $8)

## 5. 파일럿 실행 · Gemini 한도 · 파이프라인 시간 분석과 줄이기

### 5-1. 파일럿 (29장 = 기존 8 + 파일럿 21)

- 파일럿은 `intake add --pilot` — 층 목표 밖, 전부 dev. test 100 자리는 그대로
- analyze × 3: photo_type 87/87 · wear_level 52/87 · text_level 60/87 → **경로 일치 76/87**
  - 하자는 none → light 로 과민 (11장) — light 는 경로를 안 바꿔서 경로 오류는 heavy 1번뿐
  - 경로 오류 11건이 전부 "잔글씨 dense" — 시계 다이얼 · 지게차 스티커. 라벨 기준을 정했다:
    **시계 다이얼처럼 눈금·잔글씨가 물건의 일부면 dense** (README). 고친 뒤 80/87 — 이번엔 시계를
    simple 로 봐서 생성으로 보내는 쪽이 남음 (다이얼을 다시 그림) → analyze 프롬프트에 넣을 일
- 전체 × 2: 11번째 실행부터 Gemini **월 사용 한도(429 spending cap)** — 뒤 48개는 analyze 실패 →
  detect_failed → 배경 교체. 결과 폐기. 한도 초과가 운영에서 조용히 "전부 배경 교체"가 되는 것도 발견
- Claude 로 바꿀 수 있나 검토: 호출 층은 4곳이라 가능, 구조화 출력으로 JSON 은 나아짐. 걸리는 건
  box_2d 좌표 정확도(Gemini 가 학습한 형식)와 비용(Sonnet 5 기준 수 배). 지금 파이프라인 기준선을
  먼저 재고, 바꾸는 건 이 데이터셋으로 analyze 부터 숫자 비교하기로

### 5-2. Langfuse 로 본 시간 · 비용 (09-27 이후 정상 transform 33건)

| 경로 | n | 시간 중앙 / p90 | VLM 비용 중앙 |
|---|---|---|---|
| 생성 | 13 | 59초 / 90초 | $0.012 (+ FLUX $0.010 × 1.38회 → 약 $0.026) |
| 배경 교체(문서) | 5 | 22초 / 39초 | $0.007 |
| 원본 그대로 | 4 | 4초 | $0.004 |

생성 경로를 시간순으로 펼치니 **구도 확인이 끝난 뒤 누끼 비교를 기다리는 3~43초**가 제일 컸다 —
rembg 오리기(CPU)라 Langfuse 에 안 잡히는 "기록 안 된 시간 28%"의 정체. 그런데 누끼 비교는 관측용(soft)
이라 결과를 안 바꾼다. 그다음이 DINO 첫 로딩 19~23초, fal 생성 대기열 6~51초, 재생성(13건 중 3건).

VLM 비용 비중: judge 26% · analyze 22% · 글자 읽기 21% · verify 17% · 구도 확인 6%.

**틀렸던 추정**: 구도 확인과 verify 를 동시에 돌리면 5~10초 줄 거라 했는데, 실제 구도 확인은 2~3초라 효과가 작다.

**같이 발견**: 09-27 이후 transform 트레이스 325개 중 **244개가 테스트용 file_id** — `/transform` 을 부르는
웹 단위 테스트가 `.env` 의 키로 진짜 Langfuse 에 보내고 있었다.

### 5-3. 한 것

| 커밋 | 내용 | 효과 |
|---|---|---|
| 테스트 트레이싱 차단 | software 공통 conftest 에서 `tracing._disabled` — 고친 뒤 테스트 실행 이후 새 트레이스 0 | 대시보드 · 사용량 |
| 누끼 비교를 그래프 밖으로 | `item_signals_and_save` — judge 처럼 라우트는 응답 뒤, eval·dev 는 그래프 직후. 재변환이 끼면 버림. inspect 에 item_box | 생성 경로 중앙 약 10초, 최악 약 40초 |
| 모델 미리 올리기 | 서버 시작 뒤 데몬 스레드로 DINO (오리기 백엔드가 local 이면 rembg 도). 로컬 측정 12.3초 | 첫 요청 약 12~23초 |

누끼 비교는 이제 저장본(정규화 후)을 오린다 — 예전 값과 소폭 다를 수 있다. 결과·경로는 안 바뀐다.
유닛 1266 passed. 실제 시간 효과는 Gemini 한도를 올린 뒤 파일럿 재실행에서 잰다.

### 5-4. 남은 일

- [ ] Gemini 한도 올리고 파일럿 재실행 → 채점 → 보고서 (시간 효과도 여기서)
- [ ] analyze 프롬프트: 시계 다이얼 눈금·잔글씨는 dense
- [ ] run.py 가 429 · 한도 초과를 보면 바로 멈추게. 운영은 detect_failed 비율 알림
- [ ] 누끼 · 합성 구간도 Langfuse 에 기록 (남은 "기록 안 된 시간")
- [ ] analyze + 글자 읽기 합치기 (약 3초 + VLM 1회) — 정확도 다시 재야 함
- [ ] VLM 을 Claude 로 바꾸는 비교 (analyze 부터, 이 데이터셋으로)
