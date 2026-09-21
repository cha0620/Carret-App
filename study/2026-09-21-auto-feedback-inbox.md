# 2026-09-21 — 자동 피드백 에이전트 + 인박스 수집 파이프라인

실사용자 피드백이 아직 부족한 동안, VLM이 "구매자/판매자라면 이 결과 사진을
보고 몇 점을 줄지"를 대신 채워 넣는 자동 피드백 에이전트를 실제로 돌려볼 수
있는 파이프라인을 만들었다. 중간에 접근 방식을 두 번 갈아엎었고, reviewer가
잡은 이슈를 즉시 반영한 라운드도 있었다 — 그 과정 전체를 기록.

## 1. 왜 두 번 갈아엎었나 (요구사항이 구체화된 과정)

- **1차 시도**: `dataset/img` + `dataset/after`(이미 anchor 평가용으로 있던,
  파일명으로 원본/결과를 짝짓는 폴더 구조)를 재사용해서, 사용자가 직접
  before/after 쌍을 넣으면 그걸로 피드백만 생성하는 엔드포인트를 만듦.
  → 사용자가 "아니지" 하고 기각: **원본만 넣으면 결과는 내가(에이전트가)
  실제 생성 모델을 돌려서 만들어야 한다**는 게 진짜 요구였음. 사전에
  "지금 하려는 게 맞는지" 한 번 더 확인했으면 왕복을 줄일 수 있었던 지점.
- **2차 시도**: `storage/inbox/` 라는 새 폴더 하나만 만들고, 거기 있는
  "원본 파일"들을 실제 파이프라인(`pipeline.run_transform` — fal.ai 생성 +
  VLM judge 포함, 진짜 비용이 드는 실호출)에 태운 뒤 자동 피드백까지 붙이는
  `POST /dev/run-inbox`로 구현. 이게 최종 형태.
- **3차 요구**: "중고나라 크롤링해오면 안 되냐" → 로그인 필요한 네이버 카페
  대량 크롤링은 ToS/법적 리스크가 커서 만류. 대신 "다운로드가 안 되는 이미지가
  많으니 URL만 받아서 처리"하도록 확장, 그리고 실제로는 Kaggle의
  **Stanford Online Products Dataset**에서 대체 샘플을 몇 장 골라옴(3절).

## 2. 최종 구조

```
storage/inbox/          ← 사람이 원본을 직접 던져두는 곳
storage/inbox/done/     ← 성공 처리된 원본이 {file_id}_{원래파일명} 으로 이동
```

`POST /dev/run-inbox` (body: `{"preset": "studio_white", "urls": [...]}`,
둘 다 optional) 가 `inbox/`의 파일들 **+** `urls`로 받은 이미지 링크를
같은 방식으로 처리한다:

```
file_id 발급 → 원본 저장 → pipeline.run_transform() (진짜 생성 모델)
            → auto_feedback.generate_feedback() (VLM, seller 시점 별점+코멘트)
            → store.save_feedback(..., source="agent")
```

이 시퀀스를 `app/services/ingest.py::ingest_and_feedback()` 하나로 뽑아서
로컬 파일 경로/URL 경로 둘 다 같은 함수를 부르게 했다 — dev.py 자신의
원칙("이 모듈은 로직을 소유하지 않는다, 입력만 고른다")을 지키기 위한
리팩터링. URL 다운로드+검증도 `app/util/img_fetch.py::fetch_image()`로
빼서 기존 `POST /api/images/upload-url`과 공유(로직 중복 금지).

## 3. `remotezip`으로 3GB짜리 데이터셋에서 파일 몇 개만 뽑기

Kaggle의 Stanford Online Products Dataset은 원본이 Stanford 서버에
`Stanford_Online_Products.zip` (3GB) 하나로 올라가 있다. 이미지 7장만
필요한데 3GB를 다 받을 이유가 없다 — zip 포맷은 파일 목록(central directory)이
**끝부분**에 있고, 각 엔트리는 독립적으로 압축돼 있어서 **HTTP Range 요청**만
지원하면(`Accept-Ranges: bytes`) 중앙 디렉토리 + 원하는 엔트리만 부분
다운로드로 읽을 수 있다. `pip install remotezip`이 이걸 그대로 구현해준다:

```python
from remotezip import RemoteZip
with RemoteZip(url) as z:
    names = z.namelist()          # 중앙 디렉토리만 읽음 (전체 다운로드 아님)
    data = z.read(one_name)       # 그 엔트리 범위만 Range 요청
```

실제로 120,084개 파일 목록을 받는 데도, 이미지 7장(각 수십KB)을 받는 데도
3GB 전체를 건드리지 않았다. 큰 원격 아카이브에서 일부만 필요할 때 일반적으로
쓸 수 있는 패턴.

받은 이미지 중 일부(소파 콜라주 이미지, 주전자 클로즈업)는 육안으로 봤을 때
"단일 물체 + 실사 배경"이라는 용도에 안 맞아서 같은 카테고리의 다른 후보로
교체했다 — 자동으로 고르기보다 실제로 열어보고 골라야 하는 이유.

## 4. reviewer가 두 라운드에 걸쳐 잡은 이슈들

### 1라운드 (`run-inbox` 최초 버전)
| 이슈 | 심각도 | 조치 |
|---|---|---|
| `path.rename`이 try 밖에 있어서 실패 시 배치 전체가 500으로 죽음 | High | rename을 별도 try로 감싸서 실패해도 배치는 계속, `move_error`만 기록 |
| 동시 호출 시 같은 사진이 두 번 처리돼 실비용 중복 청구 가능 | High | `threading.Lock`으로 동시 호출 거부(409) |
| 실패해도 file_id를 몰라서 고아 파일 추적 불가 | Medium | 에러 row에 `file_id` 포함 |
| `done/`에서 같은 파일명이 덮어써짐 | Medium | `{file_id}_{원래파일명}`으로 저장 |
| dev.py가 로직을 직접 들고 있어 원칙 위반 | Medium | `ingest.py`로 위임 |

### 2라운드 (URL 지원 추가 후)
| 이슈 | 심각도 | 조치 |
|---|---|---|
| `fetch_image`가 임의 URL에 서버가 대신 요청 — SSRF (사설망/루프백/클라우드 메타데이터 접근 가능), 심지어 인증 없는 공개 엔드포인트(`/api/images/upload-url`)에서도 재사용됨 | **High** | ✅ 오늘 수정 — 아래 5절 |
| URL 지원 위해 `async def`로 바꿨는데 안에서 부르는 건 여전히 블로킹 동기 호출 → 배치 도는 동안 이벤트 루프 전체(다른 사용자 요청 포함) 멈춤 | **Medium** | ✅ 오늘 수정 — 아래 5절 |
| URL로 받은 이미지가 bmp/gif/tiff 등이어도 확장자 검증 없이 저장 → 나중에 파이프라인이 못 찾아 고아 파일 | Medium | ⬜ 미반영 |
| `path.rename` 자체가 실패(다른 마운트 등)하면 원본이 inbox에 남아 다음 실행 때 재처리 → 비용 중복 | Medium | ⬜ 미반영 |
| `urls` 리스트 길이 제한 없음 | Low | ⬜ 미반영 |

## 5. SSRF 방지 + 이벤트 루프 블로킹 수정 (오늘 즉시 반영)

**SSRF**: 호스트명이 아니라 `socket.getaddrinfo()`로 실제 resolve한 IP를
검사해서 사설(`10.x`)/루프백(`127.0.0.1`)/링크로컬(`169.254.169.254` 클라우드
메타데이터 포함)/예약/멀티캐스트 대역이면 거부. **리다이렉트는 자동으로 안
따라가고**(`follow_redirects=False`) 매 홉마다 같은 검사를 다시 거친다 —
안 그러면 공인 URL 하나로 검사를 통과시켜 놓고 302로 내부 주소로 튕기는
우회가 가능하기 때문. DNS 조회는 블로킹 I/O라 `run_in_threadpool`로 감쌌다.

```python
def _is_public_host(host: str) -> bool:
    for *_, sockaddr in socket.getaddrinfo(host, None):
        ip = ipaddress.ip_address(sockaddr[0])
        if ip.is_private or ip.is_loopback or ip.is_link_local \
           or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            return False
    return True
```

**이벤트 루프 블로킹**: `dev_run_inbox`가 `async def`인데 안에서
`ingest.ingest_and_feedback()`(진짜 fal.ai/VLM 네트워크 호출, 몇 초~수십 초)를
그냥 직접 부르면 그 시간 동안 프로세스의 **유일한** asyncio 이벤트 루프가
막혀서 동시에 들어온 다른 요청(`/api/transform` 등 실사용자 트래픽 포함)도
전부 대기하게 된다. `starlette.concurrency.run_in_threadpool`로 감싸서
워커 스레드에서 돌리도록 수정:

```python
row = await run_in_threadpool(
    ingest.ingest_and_feedback, file_id, data, ext, req.preset)
```

**교훈**: FastAPI에서 `async def` 엔드포인트를 쓴다고 저절로 논블로킹이
되는 게 아니다 — 그 안에서 부르는 함수가 진짜 `await` 가능한(비동기) 것인지
아니면 그냥 오래 걸리는 동기 함수인지를 구분해야 하고, 후자라면
`run_in_threadpool` 없이는 `def`(자동 스레드풀)로 두는 것보다 오히려 더
나쁘다. (참고로 `dev_transform_with_result`는 기존부터 같은 패턴의 문제를
안고 있었음 — 이번엔 손대지 않음, 범위 밖.)

## 6. 남은 TODO
- [ ] `fetch_image`가 받아들이는 이미지 포맷을 로컬 업로드와 동일하게
      `{jpg, jpeg, png, webp}`로 제한 (지금은 PIL이 디코드만 되면 다 통과)
- [ ] `path.rename` 실패 시 안전한 재시도/정리 경로 (`shutil.move` 등 검토)
- [ ] `DevRunInboxReq.urls` 길이 상한
- [ ] (구조적, 별도 논의) 앱 전체에 인증 없음 + CORS `*` — `/api/images/upload-url`을
      포함한 모든 공개 엔드포인트가 해당. 이번 SSRF 수정은 완화책이지,
      "누구나 두드릴 수 있다"는 근본 문제 자체를 없애지는 않음.
- [ ] 자동 피드백을 주기적으로(스케줄) 돌릴지 — 사용자가 "성능 개선 성격이라
      주기적으로 하면 됨"이라고 언급, 아직 cron/스케줄러 설계는 안 함.
