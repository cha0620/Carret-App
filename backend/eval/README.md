# Carret 평가 (eval)

실제 중고 거래 사진으로 파이프라인을 돌려 **분류가 맞는지**, **생성본이 물건을 지키는지**를
사람 판정으로 재고, 자동 지표(judge · DINO · 게이트)가 그 판정을 얼마나 맞히는지 본다.

설계 근거: `study/2026-09-27-prior-art-and-direction.md` §13
(Pinterest 평가자 2명 · 996개, Amazon 평가자 3명 · 이미지/상품 단위 통과율 · 자동 지표 상관 0.4)

## 흐름

```bash
cd backend
python eval/fetch.py                  # 사진 주소(url)가 있는 항목만 → eval/images/ (게시글 주소는 건너뜀)
python eval/run.py --analyze-only     # 분류 정확도만 (싸다)
python eval/run.py                    # 전체 파이프라인 (생성 포함) → runs/<run_id>/
#   runs/<run_id>/review.html 을 브라우저로 열고
#   reviews/<run_id>/TEMPLATE.csv 를 reviews/<run_id>/<이름>.csv 로 복사해 채운다
python eval/report.py                 # 가장 최근 실행 집계 → runs/<run_id>/report.md
python eval/report.py --tags          # 모든 실행의 실패 원인 태그 빈도
```

프롬프트 실험 — 잠금 문구만 바꿔 같은 사진을 돌리고 이전 실행과 견준다 (코드는 그대로):

```bash
python eval/run.py --only edge_clothes2.webp,wind.webp --repeat 2 \
  --lock-file eval/locks/surface.txt --run-id 20261002-lock-surface --note "무엇을 바꿨나"
```

`meta.json` 에 잠금 문구 · 해시(`lock`, `lock_sha`) · `lock_file` 이 남는다. 실험 문구는 `eval/locks/`.

- `images/`, `runs/` 는 git 에 올리지 않는다 — 남의 사진이고 번호판·얼굴이 찍혀 있을 수 있다
- `dataset.json`(정답)과 `reviews/`(사람 채점)는 올린다 — 사진 없이도 숫자를 다시 낼 수 있게
- 실행은 앱 storage·DB 를 건드리지 않는다 (`runs/<run_id>/storage`, `carret.db` 를 따로 쓴다)

## 사진 모으기

규칙 · 층별 목표(test 100 · dev 25) · 검색어 · 저장 방법은 **`COLLECT.md`**, 정리는 `python eval/intake.py` (status · add · export · merge).

중고나라 게시 사진은 업로드 때 약 750px 로 줄어 있다 — 결과를 쓸 때 "입력: 게시 사진(약 750px)" 으로 밝힌다.
번호판·얼굴·전화번호가 찍힌 사진은 결과 페이지에 올릴 때 가린다.

## 정답 라벨 (`dataset.json`)

사진을 실행하기 **전에** 사람이 단다 (분석 결과를 보고 달면 분석 쪽으로 기운다).

| 필드 | 값 | 기준 |
|---|---|---|
| `file` | 파일 이름 | `images/` 안의 이름 |
| `url` | 게시글 주소 | 출처 기록용. 사진 파일은 `images/` 에 직접 (없으면 빈 문자열) |
| `item` | 짧은 영어 명사 | 참고용 |
| `photo_type` | `document` / `inside_view` / `product` | 글자·표지가 곧 물건 / 물건 일부·내부만 / 그 밖 |
| `wear_level` | `none` / `light` / `heavy` | 새것 같음 / 작은 하자 몇 개 / 하자가 보이는 면의 큰 부분 |
| `text_level` | `none` / `simple` / `dense` | 글자 없음 / 큰 글자 몇 개 / 잔글씨 많음 — 시계 다이얼처럼 눈금·잔글씨가 물건의 일부면 dense (번호판·목 라벨은 안 셈) |
| `key_texts` | 문자열 목록 | 결과에서 **반드시 그대로여야 할** 글자 (제목·브랜드·모델명) |
| `note` | 자유 | 채점 때 볼 점 |
| `labeled_by` | 이름 | `claude-draft` 는 초안 — 사람이 확인하면 이름으로 바꾼다 |
| `stratum` | 층 코드 | 모을 때의 의도 (`COLLECT.md`) — 집계는 라벨 기준. 기존 8장은 빈 값(`legacy_stratum` 에 옛 값) |
| `split` | `dev` / `test` | intake 가 층별로 나눈다. test 는 동결 (결과만 본다) |
| `collected_at` | 날짜 | 모은 날 |
| `ambiguous` | true / false | 판단이 애매한 사진 |

## 사람 채점 (`reviews/<run_id>/<이름>.csv`)

`python eval/grade.py <run_id> --name <이름>` → <http://localhost:8765> 에서 체크하면 그 CSV 에 바로 저장된다
(사진당 카드 한 장 — 원본 옆에 repeat 결과 A · B 를 두고 결과마다 체크. 경로·분석값·자동 지표는 숨기고, 순서는 평가자 이름으로 섞는다. 다시 열면 이어서 채운다).
손으로 채울 때는 `review.html` 에서 원본과 결과를 나란히 보고, 한 줄에 한 실행씩 채운다. 표시는 `1`(해당), 빈칸(아님).

| 열 | 표시하는 경우 |
|---|---|
| `reviewed` | 이 줄을 봤다 (`y`) — 비어 있으면 집계에서 빠진다 |
| `shape_color_changed` | 물건의 형태 · 색 · 무늬 · 부품이 달라짐 (없던 부품이 생기거나 사라짐 포함) |
| `text_changed` | `key_texts` 나 로고 글자가 바뀌거나 뭉개짐 (번호판·라벨 같은 부수 글자는 제외) |
| `wear_changed` | 하자가 지워지거나 · 정돈되거나 · 새로 생김 |
| `added_content` | 원본에 없던 물건 · 글자 · 부품이 생김 (신발 뒤에 옷, 몸판에 브랜드 글자, 가려졌던 곳의 로고 등) |
| `background_issue` | 배경이 부자연스럽거나 · 물건과 안 어울리거나 · 원래 배경 조각이 남음 |
| `framing_issue` | 물건이 잘리거나 · 너무 작거나 · 자막·워터마크가 새로 생김 |
| `failure_tags` | 실패 원인 태그, `;` 로 이어 씀 (표시보다 잘게 — 같은 원인끼리 세려고): `detail_lost` 디자인 디테일(구멍·워싱·자수 결) · `material_changed` 소재·광택 · `color_changed` · `part_changed` 부품·디자인 · `text_altered` 있던 글자 바뀜 · `text_added` 없던 글자·자막 · `tag_lost` 택·라벨·포장 · `occlusion_invented` 가려졌던 곳 지어냄 · `object_added` 다른 물건 · `wear_removed` 하자 지워짐. 모든 실행 합계는 `python eval/report.py --tags` |
| `photo_quality` | 상품 사진 품질 1~5 — 원본과 비교하지 말고 **판매 페이지에 그대로 올릴 만한가** (5 쇼핑몰 공식 사진 수준 · 4 바로 올려도 됨 · 3 쓸 만하지만 어색한 곳이 보임 · 2 AI 티·어색함이 커서 망설여짐 · 1 못 씀) |
| `note` | 자유 |

- **보존 통과** = 물건 쪽 표시(`shape_color_changed` · `text_changed` · `wear_changed` · `added_content`)가 하나도 없음.
  구매자가 원본 대신 이 사진을 보고 사도 속지 않는가
- **결함 없음** = 표시가 하나도 없음
- **바로 쓸 수 있음** = 보존 통과 + `photo_quality` 4 이상. 보존(정직)과 품질(팔리는 사진)은 서로 당기므로 둘을 같이 본다
- 평가자가 여럿이면 다수결, 동점은 실패. 두 사람이 서로의 채점을 보지 않고 단다

## 보고서 (`report.py`)

1. 분석 정확도 — photo_type · wear_level · text_level (+ 혼동표)
2. 경로 분포 — 정답 종류별 생성 / 배경 교체 / 원본 그대로, 생성하지 않은 이유
3. 사람 판정 — 경로별 · 종류별 보존 통과율, 결함 없음 비율, 물건 기준(`--repeat` 2 이상), 실패 유형, 상품 사진 품질(평균 · 분포 · 바로 쓸 수 있음)
4. 평가자 일치도 — Cohen's kappa
5. 자동 지표 vs 사람 — 생성본에서 judge · DINO · 게이트의 AUC · 상관 (자동 지표를 믿어도 되는가) · 품질 점수와의 상관

실행 조건은 `runs/<run_id>/meta.json` 에 남는다 — 생성 모델 · 스텝 · VLM · 잠금 문구(+해시) · 커밋 · 커밋 안 된 app 변경 여부(+diff 해시).
코드에 없는 임시 변경(게이트 끔 등)은 `python eval/run.py --note "게이트 후퇴 끔"` 으로 적는다. 보고서 맨 위에 같이 나온다.
