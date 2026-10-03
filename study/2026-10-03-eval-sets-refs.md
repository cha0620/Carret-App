# 2026-10-03

1. 실험과 보고서를 어떻게 굴릴까 — 가설 먼저, 한 번에 하나, 세 층 보고서
2. eval 데이터 관리 — git 밖 사진, 데이터셋 버전, 이름 붙인 묶음
3. 종류 나누기 — 처음 답은 질문을 잘못 읽음 (category ≠ edge)
4. edge 는 층이 아니라 태그 · 대표 세트와 실패 모음을 나눈다
5. 실패 모음 초안 7장
6. 예시 사진으로 구도를 정하되 생성에는 넣지 않는다
7. `eval/refs.py` — 구도 예시 셋과 빠진 각도 (커밋 8ea3cf9)
8. 남은 일

---

## 1. 실험과 보고서를 어떻게 굴릴까 — 가설 먼저, 한 번에 하나, 세 층 보고서

모델 · 프롬프트를 바꿔 가며 실험하기 전에 절차부터 정했다. 도구는 거의 다 있었고
(`run.py --lock-file · --note`, `meta.json`, `grade.py`, `report.py`), 빠진 건 **두 실행 비교 페이지**와 **실험 목록**.

실험 한 번:

1. 돌리기 전에 **가설과 합격선**을 적는다 (예: "보존 통과율 70 → 80% 이상, 장당 비용 2배 이하") — 결과를 보고 기준을 정하면 결과에 맞춰 해석하게 된다
2. **한 번에 하나만** 바꾸고 같은 사진 묶음으로. 생성은 매번 달라서 `--repeat 2`
   - 프롬프트: `--lock-file locks/<이름>.txt` · 모델: 환경변수(`FAL_MODEL`, `VLM_MODEL`, `VLM_MODELS`) — `meta.json` 에 자동으로 남는다
   - 실행 이름 `YYYYMMDD-<바꾼 축>-<변형>`, `--note` 에 가설 한 줄
3. `grade.py` 로 사람 채점 — 경로 · 지표를 숨기고 순서를 섞어서 어느 쪽이 새 모델인지 모르게
4. 기준 vs 실험 비교 페이지 (숫자표 · 프롬프트 diff · 사진 나란히) — 지금 `compare.html` 은 손으로 만든 것
5. 실험 목록 `EXPERIMENTS.md` 에 한 줄 (바꾼 것 · 가설 · 숫자 · 채택/기각 · 링크), 해석은 study 에

| 층 | 무엇 | 언제 |
|---|---|---|
| 실행별 | `report.md` + `review.html` | 실행마다 자동 |
| 실험별 | 비교 페이지 | 채점 뒤 |
| 전체 | `EXPERIMENTS.md` + study | 판정 때 |

결과 보기: `eval/runs/` 를 `python -m http.server 8766` 으로 띄우면 `review.html` 에서 원본 · 결과 · "쓴 프롬프트"를 같이 본다.
`20261002-lock-surface` 는 `carret.db` 와 빈 `files/` 만 남은 멈춘 실행이었다.

## 2. eval 데이터 관리 — git 밖 사진, 데이터셋 버전, 이름 붙인 묶음

| 종류 | 위치 | git | 크기 |
|---|---|---|---|
| 정답 라벨 | `dataset.json` · `splits.jsonl` | ✅ | 작음 |
| 원본 사진 | `images/` (30장) | ❌ 남의 사진 | 4.6MB |
| 실행 결과 | `runs/` | ❌ | 137MB |
| 사람 채점 | `reviews/` | ✅ | 104KB |

구멍 세 개 (제안만, 아직 안 함):

- 사진 · 실행 결과가 **Codespace 에만** 있다 → `sha256` 은 dataset.json 에, 파일은 private S3 · 드라이브에 (`sync.py push/pull`)
- `meta.json` 에 **데이터셋 버전이 없다** → `dataset_sha` 를 남기고, 비교 페이지가 다르면 경고
- 같은 10장을 매번 긴 `--only` 로 넘긴다 → `subsets/<이름>.txt` + `run.py --subset`

## 3. 종류 나누기 — 처음 답은 질문을 잘못 읽음 (category ≠ edge)

"종류별로 나눠야 하나"를 **물건 종류**로 읽고 `category` 필드 초안을 넣었다 (폴더를 나누지 말고 필드 하나로 —
다른 종류 회귀 확인, 종류 판단도 평가 대상, 종류당 3~9장이라 나누면 숫자가 안 나옴).
사용자가 원한 건 **edge(경계 사례)의 종류**였다 → §4.

`category` 는 남겼고, 값은 앱의 종류 이름(`coverage.CATEGORIES`)에 맞췄다: `clothes` → `clothing`.
media 9 · clothing 7 · vehicle 6 · watch 4 · shoes 3. watch · media 는 앱 종류에 없어서 앱 기준으로는 `other`.

## 4. edge 는 층이 아니라 태그 · 대표 세트와 실패 모음을 나눈다

`edge` 층은 "분류가 애매하거나 오리기 어려운 것" 한 칸에 성격이 다른 것이 섞여 있었고,
29장을 보니 경계 성격은 none · doc 같은 **다른 층과 겹친다** (손에 든 신발 = none + 가려짐).
→ 하나만 고르는 층 대신 여러 개 다는 `edge_tags` 로.

| 묶음 | 세부 | 예 |
|---|---|---|
| 판단이 애매 | 의도된 손상 vs 하자 · 글자가 곧 물건인지 · 물건 vs 소품 | edge_clothes2 · wind 옷걸이 |
| 오리기 · 검출 | 투명 · 반사 · 여러 개 · 가려짐 · 잘림 | book2 · edge_manga1 · none_shoes1 |
| 생성에서 망가짐 | 물건 위 워터마크 · 아주 작은 글자 · 그림 패턴 | knit1 · 시계 · none_clothes4 |
| 개인정보 | 번호판 | car1 · none_car4 |

**실패한 사진을 edge 로 넣을까, 미리 정해서 모을까** → 둘 다, 섞지 않고:

| | 대표 세트 | 실패 모음 (회귀 세트) |
|---|---|---|
| 모으기 | 결과 보기 전 규칙대로 | 실패를 보고 원인 진단 뒤 |
| split | dev + test (test 동결) | **dev 만** |
| 답하는 질문 | 실제 사진 중 몇 % 되나 | 이 문제 고쳤나 · 다시 안 깨지나 |

- 실패를 test 에 넣으면 숫자가 부푼다 — 생성은 매번 달라서 한 번 실패한 사진은 그냥 다시 돌려도 통과하곤 한다
- 그래서 실패 모음 기준은 **여러 번 중 반복 실패**
- 태그 목록은 대략만 정하고 실패에서 자라게, 실패 모음에서 자주 나온 태그를 대표 세트 수집 목표에 반영
- 채택은 둘 다 본다 — 실패 모음에서 좋아져도 대표 세트에서 떨어지면 기각

## 5. 실패 모음 초안 7장

`reviews/*/*.csv` 전부(실행 5개)를 사진별로 모아 반복 실패만 골랐다. `dataset.json` 에
`set: "failure"` · `edge_tags`(사진 성격) · `failure_tags`(증상, 채점 태그 그대로) · `failure_evidence` · `failure_added`.

| 사진 | 실패 | edge_tags | failure_tags |
|---|---|---|---|
| edge_clothes2 | 8/9 | intended_damage · tag | detail_lost · tag_lost · text_altered |
| wind | 5/8 | glossy_material · prop | material_changed · color_changed |
| none_shoes2 | 4/4 | subtle_wear | wear_removed · object_added |
| none_watch3 | 4/6 | hand_held · small_text | part_changed · color_changed · text_added · wear_removed |
| tshirt | 4/6 | watermark · small_text | text_added · text_altered |
| none_shoes1 | 4/6 | hand_held · occluded | occlusion_invented · object_added |
| none_clothes3 | 3/4 | prop · embroidery · tag | detail_lost · tag_lost · text_added |

- 뺀 것: knit1 2/8 (가끔 큰 자막 — 지켜볼 것), none_car4 1/4, none_car2 1/3, none_clothes1 1/6
- 채점 대부분이 claude 1차 — 사람 확인 전 초안
- 나머지 22장은 규칙 없이 모은 파일럿이라 `set` 을 달지 않았다 (대표 세트는 COLLECT 규칙대로 새로 모아야 생긴다)

## 6. 예시 사진으로 구도를 정하되 생성에는 넣지 않는다

원하는 건 "상품으로 나오는 구도"인데 말보다 예시 사진이 정확하다. 걱정: 예시를 참조로 넣으면 그 물건을 닮아간다.
여러 장 받는 편집 모델은 참조에서 구도만이 아니라 색 · 소재 · 로고도 섞는 경향이 있고,
지금 실패 모음에서 깨지는 게 바로 소재(wind) · 글자(tshirt) · 디테일(edge_clothes2)이다.

순서:

1. 예시는 **기준 정의**로만 — 채움 비율 · 중심 · 여백 · 기울기 · 그림자 숫자를 뽑고, 원본을 오려서 코드로 맞춘다 (새 픽셀 0)
2. 각도 자체를 바꿔야 할 때만 생성, 참조는 실제 상품 대신 회색 실루엣 같은 틀이나 VLM 이 쓴 구조화 문장
3. 예시는 judge 의 "이 구도에 가까운가" 기준으로
4. 실제 사진을 참조로 넣어 볼 거면 먼저 **섞임 측정** — 다르게 생긴 참조 두 장으로 같은 원본을 돌려 결과가 참조 쪽으로 끌려가는지 (결과-참조 DINO)

지금 `generator.py` 는 `image_urls` 에 원본 한 장만 넣는다. flux-2 flash edit 의 여러 장 참조는 확인 안 함.

## 7. `eval/refs.py` — 구도 예시 셋과 빠진 각도 (커밋 8ea3cf9)

사용자가 예시 셋을 모으기로 했고, "그 구도에서 비는 각도가 있으면 추가로 받아야 하니 그것도 통제"를 요청.
앱에는 이미 구도를 고를 때 그 각도 사진이 없으면 `hint` 로 찍어 달라는 흐름이 있다(`compositions.options`, 프론트 `render.js`).
빠진 건 **예시 셋 · 구도 정의 · 업로드 필수 면(`coverage.REQUIRED`)이 서로 맞는지** 보는 도구.

- `eval/refs/` (git 밖) + `eval/refs.json` (git), 규칙은 `eval/REFS.md`
- 라벨: `category` · `composition`(있는 키 또는 `new:<이름>`) · `view` · `source`
- `python eval/refs.py status`: 구도별 예시 수(오류 없는 것만, 3장 목표), 라벨 오류(있으면 종료 1), 할 일
- **빠진 각도 두 방향** (compositions.py 에 있는 구도만):
  - 구도 각도를 업로드 때 꼭 받지 않음 → 고를 때만 찍어 달라고 한다.
    필수 면이 받는 각도(대체 포함)가 **전부** 구도 각도 안에 있어야 "꼭 받는다" — 대체 각도 하나만 겹치면 사용자는 다른 각도로 그 면을 채울 수 있다
  - 업로드 때 받는 각도인데 그걸로 만드는 구도가 없음 → 그 사진은 정리만 된다

지금 신발에서 잡힌 것:

- `shoes_front34` — 필수 면 front_34 는 `front` 로도 채워지는데 정면 구도가 없다 → 정면만 올린 사람은 대표컷을 못 고른다
- `shoes_top` — `top` 을 업로드 때 안 묻는다

리뷰 · 테스트에서 잡은 것:

| 누가 | 지적 | 처리 |
|---|---|---|
| reviewer | category 가 없거나 값이 dict/숫자면 오류 목록 전에 TypeError 로 죽음 | 항목별 `entry_errors`, 타입 검사, 깨진 JSON 은 한 줄 안내 |
| reviewer | 대체 각도 하나만 겹쳐도 "업로드 때 받는다"로 판정 (두 방향 다) | `asked_for`: 필수 면의 각도 집합 ⊆ 구도 각도, 방향 2 는 `ok - made` 를 다 적음 |
| reviewer | `new:` 구도가 방향 2 경고를 지움 | made 에서 `new:` 제외 |
| reviewer | 예시 없는 종류는 빠진 각도를 아예 안 봄 | `coverage.CATEGORIES` 전부 |
| reviewer | 라벨 틀린 예시도 장수에 셈 → "예시 부족"이 사라짐 | `valid()` 만 셈 |
| reviewer | refs/ 에만 있는 사진 · 같은 사진 다른 이름 · `../` 이름 | 셋 다 오류로 |
| reviewer | 한 원인에 오류 여러 줄, REFS.md 와 코드 불일치 | 원인 하나에 한 줄, 문서 맞춤 |
| tester | 기본 인자가 정의 때 고정돼 상수 monkeypatch 가 안 먹음 | 기본값 None, 함수 안에서 읽음 |
| tester | `^[a-z0-9_]+$` 를 `.match` → `"new:pair\n"` 통과 | `fullmatch` |

- 테스트 `test_eval_refs.py` 123개, 전체 단위 1896 passed
- 커밋 `8ea3cf9` — refs.py 가 import 하는 미커밋 `compositions.py` · `coverage.py` 를 같이 올렸다 (커밋만 꺼내서 eval 테스트 224 passed 확인).
  `conftest.py` 는 `refs_mod` 만 (이전 작업의 `grade_mod` 는 미커밋으로 남김)

## 8. 남은 일

측정:

- [ ] 실패 모음 7장 사람 확인 (지금은 claude 1차 채점)
- [ ] knit1 — 큰 자막 생기는 실패가 또 나오면 실패 모음에
- [ ] 참조 사진 섞임 측정 (참조 두 장 × 같은 원본, 결과-참조 DINO) — 실제 사진을 참조로 넣기 전에

구조 · 도구:

- [ ] 구도 예시 셋 모으기 (사용자) — 신발은 정면 사진도 몇 장
- [ ] 예시에서 구도 숫자 뽑기 + 누끼 · 정렬로 맞추기 → none_shoes1 · 2 에 생성 결과와 나란히
- [ ] 신발 정면: 업로드 필수를 front_34 로 좁힐지, 정면 구도를 만들지 · `top` 을 필수로 넣을지
- [ ] `run.py --set failure` · report 태그별 집계 · `intake.py` 라벨 열에 category · edge_tags
- [ ] `compare.py` (기준 vs 실험 비교 페이지) · `EXPERIMENTS.md`
- [ ] `meta.json` 에 `dataset_sha` · `subsets/` + `--subset` · 사진 · 결과 백업 (S3 · 드라이브 중 정하기)
- [ ] COLLECT.md: edge 층을 "분류가 애매한 것"으로 좁히고 태그별 최소 수
