# 작업 환경 옮기기 (codespace → 로컬)

## 가져갈 것

| 무엇 | 어떻게 |
|---|---|
| 코드 · study · `.claude/` | `git clone` |
| `backend/.env` | 직접 복사 (키가 들어 있어 메신저·git 금지) |
| eval 사진 · 결과 | `.env` 만 있으면 S3 에서 받는다 (아래) |
| Claude 메모리 | `~/.claude/projects/<경로>/memory/` 복사. 폴더 이름은 프로젝트 경로에서 나온다 (`/Users/me/Carret-App` → `-Users-me-Carret-App`) |

venv 는 복사하지 않고 새로 만든다 (OS 가 다르면 안 돈다):

```bash
python3.12 -m venv venv1 && venv1/bin/pip install -r requirements.txt
```

Docker 는 배포용이라 로컬 개발엔 필요 없다. 배포 환경과 같은지 볼 때만 `docker build` + `--env-file backend/.env`.

## eval 결과 보관 — `backend/eval/archive.py`

숫자는 `eval/eval.db` 하나(runs · samples · attempts · labels · files), 사진은 S3 `<s3_prefix>-eval/` 에 둔다.

```bash
cd backend
python eval/archive.py build   # results/runs · data 를 읽어 eval.db 를 새로 만든다
python eval/archive.py push    # S3 와 해시가 다른 파일 + eval.db 를 올린다
python eval/archive.py pull    # 새 환경에서: eval.db 를 받고 빠진 파일을 제자리에 푼다
```

- pull 은 로컬에 내용이 다른 파일이 있으면 덮지 않고 알린다. 덮으려면 `--force`.
- 받은 목록의 경로가 `eval/` 밖을 가리키면 거부한다.
- eval 을 새로 돌린 뒤엔 `build` → `push` 를 다시 한다.

## 결과 폴더 크기 (10-19 정리)

- 남긴 run: `_view` `_pipe` `refs` `picks-flash-1011`(정직 라벨) · 10-12 `picks-gen` `coat-fixed` `phones-fixed` `airmax-top` `seedream-*` 3개
- 그 전 run 은 study 가 사진으로 근거를 드는 대표 사례만 `files/` + `posts.json`/`results.jsonl` 로 남김:
  `20261001-listing-clothes`(신발에 재킷 · knit1 목 라벨) · `20261003-added-text`(H&M 로고 · 띠지) · `20261003-shoes-hero2` ·
  `posts-textphoto-1008`(정답 베낌 통과) · `posts-textsketch2-1008`(피규어 늘었는데 통과) · `coat-noname-flash-1008` · `coat-clean-nbp-1008`.
  나머지는 지움 (406MB, 1496개). 결론은 각 날짜 study 에 글로 남아 있다
- 시도 이미지는 결과와 같이 1600px · JPEG q88 로 저장한다 (`storage.IMAGE_KINDS`). 10-19 이전 run 의 시도 이미지는 원본 크기라 해상도가 다르다 — 섞어 비교하지 않는다.
- eval 이 시도 이미지를 `files/` 로 옮기면 `storage/attempts` 엔 남기지 않는다 (예전엔 두 벌).
