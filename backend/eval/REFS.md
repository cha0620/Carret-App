# 구도 예시 셋 모으기

정석 구도("상품으로 나오는 사진의 모양")를 말 대신 예시 사진으로 정한다.
예시는 **생성 모델에 넣지 않는다** — 참조 사진의 색 · 소재 · 글자가 결과에 섞여 들어올 수 있어서다.
예시에서는 구도 숫자(화면을 얼마나 채우나 · 여백 · 수평 · 그림자)만 뽑고, 채점 때 "이 구도에 가까운가"의 기준으로 쓴다.

지금 앱에 정석 구도가 있는 종류는 **shoes 뿐** (`app/services/compositions.py`). 다른 종류는 `new:` 로 모아서 구도를 정한다.

## 1. 모으기

- 구도마다 **3장 이상** (`MIN_EXAMPLES`). 같은 쇼핑몰 3장보다 다른 쇼핑몰 3장
- 물건이 아니라 **구도**가 닮은 사진을 고른다 — 같은 모델 · 같은 색일 필요 없다
- 사진은 `backend/eval/refs/` 에 (git 밖), 이름은 `<구도 키>_<번호>.jpg` (예: `shoes_front34_1.jpg`, `clothing_flatlay_1.jpg`)

## 2. 라벨 (`refs.json`)

```json
[
  {"file": "shoes_front34_1.jpg", "category": "shoes", "composition": "shoes_front34", "view": "front_34",
   "source": "https://...", "note": "흰 배경, 그림자 약하게"}
]
```

| 칸 | 값 |
|---|---|
| `file` | refs/ 안의 파일 이름만 (폴더 없이) |
| `category` | `coverage.CATEGORIES` — shoes · clothing · bag · electronics · vehicle · other |
| `composition` | `compositions.py` 의 구도 키. 아직 없는 구도면 `new:<이름>` (영문 소문자 · 숫자 · `_`, 예: `new:clothing_flatlay`) |
| `view` | 그 예시를 찍은 각도 — `coverage.VIEWS` (front · front_34 · side · back · rear_34 · top · bottom · inside · label · detail). 있는 구도면 그 구도의 각도 중 하나여야 한다 |
| `applies_to` | 이 구도를 쓰는 물건 (예: "상의 전부") — 선택 |
| `source` · `note` | 선택 (검사하지 않는다) |

데이터셋 사진이 어떤 구도를 목표로 하는지는 `dataset.json` 의 `target_composition` (같은 구도 키).
세부 종류마다 따로 두지 않고 묶는다 — 상의는 전부 `new:clothing_top_front`, 신발은 전부 `shoes_front34` (10-03).

## 3. 확인

```bash
python eval/refs.py status     # 라벨 오류가 있으면 종료 코드 1
```

- **라벨 오류** (오류가 있는 예시는 장수에 세지 않는다)
  - 칸: 모르는 종류 · 각도 · 구도, composition 빈칸, `new:` 이름 형식 · 이미 있는 구도 이름
  - 맞춤: 구도와 종류가 안 맞음, 구도와 각도가 안 맞음 (예: shoes_side 인데 view 가 front_34)
  - 파일: 이름에 폴더, refs/ 에 없음, 사진 확장자 아님, 같은 파일 두 번, 같은 사진 다른 이름, refs.json 에 없는 사진
- **할 일**
  - 정석 구도가 없는 종류 · 예시가 3장 안 되는 구도 · 선 그림(svg) 없는 구도
  - `new:` 구도 → `compositions.py` 에 추가 (키 · 설명 · 각도 · 프롬프트 문장 · 선 그림)
  - **빠진 각도 (두 방향, compositions.py 에 있는 구도만)**
    - 구도가 쓰는 각도를 업로드 때 꼭 받지 않음 → 그 구도를 고를 때만 "이 각도로 찍어 주세요"가 뜬다.
      꼭 받아야 하는 면이면 `coverage.REQUIRED` 에 넣는다.
      필수 면의 대체 각도 하나만 겹치는 건 "꼭 받는다"로 치지 않는다 (사용자가 다른 각도로 그 면을 채울 수 있다)
    - 업로드 때 꼭 받는 면의 각도(대체 각도 포함)인데 그걸로 만드는 구도가 없음 → 그 사진은 정리만 된다
