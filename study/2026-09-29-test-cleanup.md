# 2026-09-29

1. 테스트 정리 1차: 이력 확인 테스트 걷어내기
2. 테스트 정리 2차: 파라미터 중복 합치기
3. test_pipeline 을 시나리오로 다시 쓰기

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
