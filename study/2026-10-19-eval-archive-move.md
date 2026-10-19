# 2026-10-19

1. 작업 환경 옮기기 준비 (codespace → 로컬)
2. eval 결과 보관 — `eval.db` + S3
3. 시도 이미지 두 벌 · 원본 크기 정리
4. 지난 run 정리 — study 근거만 남기기

## 1. 작업 환경 옮기기

`docs/local-setup.ko.md` — 코드는 git, `backend/.env` 와 Claude 메모리는 직접 복사, eval 사진은 S3 에서 받는다.
venv 는 새로 만든다.

## 2. eval 결과 보관 (`backend/eval/archive.py`)

- 숫자는 `eval/eval.db`(sqlite) 하나 — runs · samples · attempts · labels · files
- 사진은 S3 `<s3_prefix>-eval/`, 앱과 같은 버킷 · 키
- `build` → `push` → (새 환경) `pull`. pull 은 `eval/` 밖 경로를 거부하고, 로컬과 다른 파일은 `--force` 없이 덮지 않는다
- 비용: 모델 호출 없음, S3 저장 · 전송만

## 3. 시도 이미지

- 예전 eval 은 `storage/attempts` 를 `files/` 로 **복사** → 같은 사진 두 벌 (해시로 확인, 154MB 전부 중복)
- 이제 이동. `storage.IMAGE_KINDS` 에 `attempts` 추가 → 결과처럼 1600px JPEG
- 10-19 이전 시도 이미지는 원본 크기 — 해상도 섞어 비교하지 않기

## 4. 지난 run 정리

처음 build 는 정리 전 폴더를 그대로 담아 2197개였다 (문서엔 정리했다고 적고 실제론 안 지움).

- 지우기 전 study 를 뒤졌다: 작은 판별 모델 재료(inspect 쌍)가 지울 쪽에 100쌍 넘게 있었다
- 사용자 결정: 전부 남기지 말고 **study 가 사진으로 근거를 드는 대표 사례만**
  - 신발에 재킷(10-01) · knit1 목 라벨 → 가슴 프린트 · H&M 로고 · 책 띠지(10-03) · 글자 잠금 뺀 신발
  - 정답 베꼈는데 게이트 통과(아이폰 · 코트) · 피규어 늘었는데 통과 · 이름 빼면 후드 그대로(10-08)
- 지움 1496개 · 406MB. `results/runs` 470MB → 83MB, `eval.db` 파일 701개

남은 일:

- [ ] 작은 판별 모델 라벨 재료가 25쌍 + 대표 사례로 줄었다 — 새 결과로 채우기 (`honesty-metric/12-small-verifier-plan.md` §3)
- [ ] 로컬에서 `pull` 로 실제로 받아 보기
