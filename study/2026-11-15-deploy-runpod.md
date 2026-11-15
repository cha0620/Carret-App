# 2026-11-15

1. [README 를 코드에 맞춤](#1-readme-를-코드에-맞춤)
2. [RunPod 배포 준비](#2-runpod-배포-준비)
3. [공개 데모 비용 상한](#3-공개-데모-비용-상한)
4. [리뷰가 잡은 것](#4-리뷰가-잡은-것)
5. [남은 일](#5-남은-일)

## 1. README 를 코드에 맞춤

- verify 단계 설명이 아직 "좌표를 받는다"였다 — 10-05 에 말풍선을 없애며 `schema_verify.md` 에서 `box_2d` 가 빠졌다
- 강점 2번이 09-27 에 없앤 OCR 글자 가드를 결정론적 신호로 소개하고 있었다
- 테스트 수 765 → 790. PR #42 (머지됨)
- 교훈: 문서의 "현재 동작" 문장은 코드를 바꾼 날 같이 고치지 않으면 한 달 뒤엔 아무도 모른다

## 2. RunPod 배포 준비

**판단**: 서버는 GPU 가 필요 없다 (생성 fal.ai, 판단 Gemini, 서버는 DINOv2-small · OpenCV). → **CPU Pod 하나 + RunPod HTTP 프록시**.
Serverless 는 요청 하나가 아니라 화면 · 여러 API · SQLite 를 들고 있는 앱이라 맞지 않는다.

- `Dockerfile`: **`frontend/` 를 복사하지 않고 있었다** — 이미지로 띄우면 API 만 뜨고 화면이 없었다. DINOv2 가중치를 이미지에 미리 받고
  `HF_HUB_OFFLINE=1` (첫 요청 대기 · 실행 중 HF 의존 제거)
- `.github/workflows/image.yml`: GHCR 이미지, 수동 실행 · `v*` 태그만, `latest` 는 main · 태그에서만
- `deploy/runpod.env.example`, `deploy/RUNPOD.md`: 키는 RunPod Secrets(`{{ RUNPOD_SECRET_이름 }}`), 사진 · DB 는 `/workspace` 볼륨
  (환경 변수를 바꾸면 Pod 가 재시작되고 볼륨 밖은 지워진다)
- env 견본에서 **값 뒤 주석을 뺐다** — `DEV_TOOLS=false  # …` 를 그대로 붙여 넣으면 값이 통째로 들어가 bool 파싱이 실패한다
- 여기서는 docker 데몬이 없어 이미지 빌드는 못 돌렸다 → 첫 빌드는 GitHub Actions 에서 확인

## 3. 공개 데모 비용 상한

**문제**: 링크가 퍼지면 변환 1건 약 $0.03 이 그대로 쌓인다. 앱에는 재생성 상한 · 생각 토큰 상한은 있었지만 **요청 수 상한이 없었다**.

**한 것** (`app/core/quota.py`, `QUOTA_ENABLED` 기본 끔):

- 유료 POST 마다 추정 비용 표(`COSTS`)를 두고, 받기 **전에** SQLite `usage` 에 예약 (`BEGIN IMMEDIATE` — 동시 요청 둘이 같이 통과하지 않게)
- 서버 하루(KST) `QUOTA_DAILY_USD=3`, IP 하루 `QUOTA_PER_IP_DAILY_USD=0.5` — 넘으면 429 + 한국어 안내 (화면 `failMsg`)
- 처음엔 IP 상한을 0.3("변환 약 10번")으로 잡았는데 틀렸다: 한 흐름(업로드 · 검토 · 분석 · 구성품 · 변환)이 약 $0.06 이라 약 5번.
  학교 · 회사 · 모바일 망은 IP 를 같이 쓰니 0.5 로
- `GET /healthz`
- 테스트 23개 (`test_quota.py`), 유닛 전체 813 통과

## 4. 리뷰가 잡은 것

| 지적 | 조치 | 안 한 이유 |
|---|---|---|
| 하위 폴더 `backend/.env` 가 이미지에 들어감 (`.dockerignore` 는 루트만) | `**/.env` 등으로 | |
| `--forwarded-allow-ips "*"` 면 uvicorn 이 client.host 를 XFF 맨 왼쪽(위조 가능)으로 바꿈 | `--proxy-headers` 뺌 | |
| XFF 맨 왼쪽을 IP 로 씀 | `cf-connecting-ip` 만, `ipaddress` 검증, IPv6 /64 | |
| 예약 후 4xx 도 차감 → 잘못된 요청으로 상한을 공짜로 소진 | 4xx 면 환불 (5xx · 끊김은 유료 호출이 나갔을 수 있어 그대로) | |
| 동기 SQLite 가 이벤트 루프를 막음 | `run_in_threadpool` | |
| DB 오류 시 평문 500 | 503 JSON, fail-closed 명시 | |
| healthz 가 오늘 지출 · IP 수를 공개 | `{"status":"ok"}` 만 | |
| 커넥션을 닫지 않음 | `closing()` | |
| `url.path` 와 라우팅 경로가 갈릴 수 있음 | `scope["path"]` | |
| `latest` 를 기능 브랜치가 덮어씀 | main · 태그만, 버전 태그 추가 | |
| 100초 프록시 제한 + 재시도로 비용 이중 지출 | RUNPOD.md 에 적음 | 근본 해결은 작업 ID + 폴링 — 마무리 단계라 새 구조는 안 넣는다 |
| arrange · items 비용이 물건 · 사진 수에 비례하지 않음 | | 추정치 표의 한계로 둠 — fal · Google 결제 한도를 두 번째 벽으로 |
| 업로드 · upload-url 은 무료라 상한 밖 (볼륨 채우기) | RUNPOD.md 에 적음 | 데모 규모에선 디스크를 가끔 보는 걸로 |
| 금액을 REAL 로 누적 | | 상한 경계 오차가 센트 미만 |
| 429 에 CORS 헤더 없음 | | 같은 출처 화면만 쓴다 |
| usage 에 IP 무기한 보관 · 정답 구도 그림이 이미지에 없음 | RUNPOD.md 에 적음 | |

## 5. 남은 일

- [ ] GitHub Actions 에서 첫 이미지 빌드 확인
- [ ] RunPod 계정 · Secrets · Pod 띄우기 → `/healthz`, `/dev/results` 404 확인
- [ ] RunPod 프록시가 클라이언트가 보낸 `cf-connecting-ip` 를 덮어쓰는지 실측
- [ ] fal · Google 콘솔에 결제 한도(알림)
- [ ] 변환 실제 소요 시간 분포 — 100초를 넘는 비율
