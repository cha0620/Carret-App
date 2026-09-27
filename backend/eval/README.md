# Carret 평가 (eval)

실제 중고 거래 사진으로 파이프라인을 돌려 **분류가 맞는지**, **생성본이 물건을 지키는지**를
사람 판정으로 재고, 자동 지표(judge · DINO · 게이트)가 그 판정을 얼마나 맞히는지 본다.

설계 근거: `study/2026-09-27-prior-art-and-direction.md` §13
(Pinterest 평가자 2명 · 996개, Amazon 평가자 3명 · 이미지/상품 단위 통과율 · 자동 지표 상관 0.4)

## 흐름

```bash
cd backend
python eval/fetch.py                  # dataset.json 의 url → eval/images/
python eval/run.py --analyze-only     # 분류 정확도만 (싸다)
python eval/run.py                    # 전체 파이프라인 (생성 포함) → runs/<run_id>/
#   runs/<run_id>/review.html 을 브라우저로 열고
#   reviews/<run_id>/TEMPLATE.csv 를 reviews/<run_id>/<이름>.csv 로 복사해 채운다
python eval/report.py                 # 가장 최근 실행 집계 → runs/<run_id>/report.md
```

- `images/`, `runs/` 는 git 에 올리지 않는다 — 남의 사진이고 번호판·얼굴이 찍혀 있을 수 있다
- `dataset.json`(정답)과 `reviews/`(사람 채점)는 올린다 — 사진 없이도 숫자를 다시 낼 수 있게
- 실행은 앱 storage·DB 를 건드리지 않는다 (`runs/<run_id>/storage`, `carret.db` 를 따로 쓴다)

## 사진 모으기

사진 종류가 고르게 들어가도록 모은다 (목표 30~50장):

| 사진 종류 | 예 | 목표 |
|---|---|---|
| document | 책 · 음반 · 카드 · 보증서 · 설명서 | 6~10 |
| inside_view | 엔진룸 · 케이스 뜯은 노트북 · 한 곳 클로즈업 | 4~6 |
| product · 하자 none | 새것 같은 상품 | 8~10 |
| product · 하자 light | 얼룩 하나 · 긁힘 몇 개 | 8~10 |
| product · 하자 heavy | 녹 · 도장 벗겨짐 · 흠집 많음 | 4~6 |
| product · 글자 많음 | 화장품 · 전자제품 박스 · 로고 큰 옷 | 4~6 |

중고나라 게시 사진은 업로드 때 약 750px 로 줄어 있다 — 결과를 쓸 때 "입력: 게시 사진(약 750px)" 으로 밝힌다.
번호판·얼굴·전화번호가 찍힌 사진은 결과 페이지에 올릴 때 가린다.

## 정답 라벨 (`dataset.json`)

사진을 실행하기 **전에** 사람이 단다 (분석 결과를 보고 달면 분석 쪽으로 기운다).

| 필드 | 값 | 기준 |
|---|---|---|
| `file` | 파일 이름 | `images/` 안의 이름 |
| `url` | 원본 주소 | 없으면 빈 문자열 (직접 넣은 사진) |
| `item` | 짧은 영어 명사 | 참고용 |
| `photo_type` | `document` / `inside_view` / `product` | 글자·표지가 곧 물건 / 물건 일부·내부만 / 그 밖 |
| `wear_level` | `none` / `light` / `heavy` | 새것 같음 / 작은 하자 몇 개 / 하자가 보이는 면의 큰 부분 |
| `text_level` | `none` / `simple` / `dense` | 글자 없음 / 큰 글자 몇 개 / 잔글씨 많음 (번호판·목 라벨은 안 셈) |
| `key_texts` | 문자열 목록 | 결과에서 **반드시 그대로여야 할** 글자 (제목·브랜드·모델명) |
| `note` | 자유 | 채점 때 볼 점 |
| `labeled_by` | 이름 | `claude-draft` 는 초안 — 사람이 확인하면 이름으로 바꾼다 |

## 사람 채점 (`reviews/<run_id>/<이름>.csv`)

`review.html` 에서 원본과 결과를 나란히 보고, 한 줄에 한 실행씩 채운다. 표시는 `1`(해당), 빈칸(아님).

| 열 | 표시하는 경우 |
|---|---|
| `reviewed` | 이 줄을 봤다 (`y`) — 비어 있으면 집계에서 빠진다 |
| `shape_color_changed` | 물건의 형태 · 색 · 무늬 · 부품이 달라짐 (없던 부품이 생기거나 사라짐 포함) |
| `text_changed` | `key_texts` 나 로고 글자가 바뀌거나 뭉개짐 (번호판·라벨 같은 부수 글자는 제외) |
| `wear_changed` | 하자가 지워지거나 · 정돈되거나 · 새로 생김 |
| `background_issue` | 배경이 부자연스럽거나 · 물건과 안 어울리거나 · 원래 배경 조각이 남음 |
| `framing_issue` | 물건이 잘리거나 · 너무 작거나 · 자막·워터마크가 새로 생김 |
| `note` | 자유 |

- **보존 통과** = 물건 쪽 표시(`shape_color_changed` · `text_changed` · `wear_changed`)가 하나도 없음.
  구매자가 원본 대신 이 사진을 보고 사도 속지 않는가
- **결함 없음** = 표시가 하나도 없음
- 평가자가 여럿이면 다수결, 동점은 실패. 두 사람이 서로의 채점을 보지 않고 단다

## 보고서 (`report.py`)

1. 분석 정확도 — photo_type · wear_level · text_level (+ 혼동표)
2. 경로 분포 — 정답 종류별 생성 / 배경 교체 / 원본 그대로, 생성하지 않은 이유
3. 사람 판정 — 경로별 · 종류별 보존 통과율, 결함 없음 비율, 물건 기준(`--repeat` 2 이상), 실패 유형
4. 평가자 일치도 — Cohen's kappa
5. 자동 지표 vs 사람 — 생성본에서 judge · DINO · 게이트의 AUC · 상관 (자동 지표를 믿어도 되는가)
