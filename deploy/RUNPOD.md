# RunPod 배포 (공개 데모)

Carret 서버는 GPU 가 필요 없다 — 생성은 fal.ai, 판단은 Gemini 가 하고, 서버는 DINOv2(small) · OpenCV 만 CPU 로 돌린다.
그래서 **CPU Pod 하나**에 컨테이너 이미지를 띄우고 RunPod HTTP 프록시로 연다.

```text
사용자 → Cloudflare → RunPod 로드밸런서 → CPU Pod (uvicorn :8000) ─→ fal.ai · Gemini
                                              └ /workspace 볼륨: 사진 · SQLite
```

## 0. 처음 한 번

1. **이미지 만들기**: GitHub → Actions → `image` → *Run workflow* (또는 `v*` 태그 푸시).
   `ghcr.io/cha0620/carret-app:latest` 가 생긴다
2. **이미지 공개 여부**: GitHub → Packages → `carret-app` → 공개로 두면 RunPod 가 그냥 받는다.
   비공개로 두려면 RunPod *Settings → Container Registry Authentication* 에 GitHub 사용자 이름과
   `read:packages` 권한 토큰을 넣고 템플릿에서 고른다
3. **키 넣기**: RunPod *Secrets* 에 `fal_key`, `vlm_key` 를 만든다 (값은 다시 볼 수 없다)

## 1. Pod 띄우기

*Pods → Deploy → CPU* 에서 **Edit Template**:

| 칸 | 값 |
|---|---|
| Container Image | `ghcr.io/cha0620/carret-app:latest` |
| Expose HTTP Ports | `8000` |
| Volume Disk / Mount Path | 5GB 안팎 / `/workspace` |
| Container Disk | 10GB 이상 (이미지에 torch CPU · DINOv2 포함) |
| Environment Variables | `deploy/runpod.env.example` 를 한 줄씩 |

CPU 는 가장 작은 것부터 (메모리 4GB 이상 권장 — DINOv2 와 이미지 처리).

## 2. 확인

```bash
curl https://<POD_ID>-8000.proxy.runpod.net/healthz
# {"status":"ok"}
```

브라우저로 `https://<POD_ID>-8000.proxy.runpod.net/` 를 열면 앱 화면이 뜬다.
`/dev/results` 가 404 인지도 본다 (`DEV_TOOLS=false`).

## 3. 비용 안전장치

| 무엇 | 어디서 |
|---|---|
| 유료 요청 하루 상한 — 서버 전체 `QUOTA_DAILY_USD`, IP 하나 `QUOTA_PER_IP_DAILY_USD` (KST 자정 초기화) | `app/core/quota.py` |
| 재생성 상한 `MAX_GENERATE_ATTEMPTS` (1~5) | `config.py` |
| VLM 생각 토큰 상한 | `app/core/vlm.py` |
| 오늘 추정 사용량 (공개 안 함) | Pod 터미널: `cd /app/backend && python -c "from app.core import quota; print(quota.usage_today())"` |

- 상한 비용은 **추정치**다 (README 단계별 비용 표 기반, 변환 1건 $0.04 로 넉넉히). 실제 청구는 fal · Google 콘솔에서 본다
- fal · Google 쪽에도 **결제 한도(알림)** 를 따로 걸어 둔다 — 서버 상한이 뚫려도 두 번째 벽
- 상한에 걸리면 429 와 "오늘 데모 사용량이 다 찼어요…" 안내가 화면에 뜬다
- 4xx 로 끝난 요청(본문 오류 · 없는 사진 등)은 유료 호출 전이라 예약을 돌려준다. 5xx · 끊김은 돌려주지 않는다
- 사용량 DB 가 안 열리면 유료 요청을 막는다 (503) — "확인 못 함"을 통과로 치지 않는다
- IP 는 Cloudflare 의 `cf-connecting-ip` 만 믿는다 (IPv6 는 /64 로 묶음). Pod 에 TCP 포트를 따로 열면 Cloudflare 를
  안 거치는 길이 생겨 IP 상한을 피할 수 있다 — HTTP 포트 8000 만 연다 (전체 하루 상한은 그래도 걸린다)

## 4. 알아 둘 것

- **100초 제한**: RunPod 프록시는 100초가 넘는 요청을 끊는다. 변환은 보통 35~60초지만 fal 대기열이 길고
  재생성까지 가면 넘을 수 있다 — 그때는 화면에 실패로 뜬다 (비용은 이미 쓴 상태). 자주 나면 `MAX_GENERATE_ATTEMPTS=1`
- **업데이트**: 새 이미지를 만든 뒤 Pod 를 재시작하면 `latest` 를 다시 받는다. 환경 변수를 바꿔도 재시작된다 —
  `/workspace` 밖은 지워지므로 사진 · DB 는 꼭 `/workspace` 에
- **안 쓸 때는 Stop**: Pod 는 켜져 있는 동안 과금된다 (Stop 해도 볼륨 보관료는 나간다)
- **개인정보**: 업로드 사진이 `/workspace/storage`, 하루 사용량(IP 포함)이 `usage` 테이블에 쌓인다.
  데모 안내에 "올린 사진은 서버에 저장됩니다"를 적고, 둘 다 주기적으로 지운다
- **무료 요청은 세지 않는다**: 사진 업로드 · URL 업로드는 유료 API 를 안 불러 상한 밖이다 — 볼륨이 차지 않게
  디스크 사용량을 가끔 본다
- **정답 구도 그림 없음**: `eval/data/refs/` 는 git 밖이라 이미지에 없다 — 데모에서 정답 구도 목록은 비어 보인다
