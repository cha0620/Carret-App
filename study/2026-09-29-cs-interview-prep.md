# 2026-09-29

1. 방향 — 취준 포트폴리오와 CS 기본기
2. DB — SQLite WAL · busy timeout · 마이그레이션 경합
3. 동시성 — 락 분리 · double-checked locking · LRU 캐시
4. async 와 스레드 — run_in_threadpool · 스레드풀 · Future 취소 · contextvars
5. 네트워크·보안 — SSRF 방어와 그 빈틈
6. 외부 API — 타임아웃 · deadline · 재시도 · 연결 재사용
7. 컨테이너 — Dockerfile 레이어 캐시
8. 연습 방법과 할 일

## 1. 방향 — 취준 포트폴리오와 CS 기본기

**판단**: AI 엔지니어를 주력으로 한다. Carret 은 문제 정의 → 가드 설계 → 평가 → 뒤집은 판단 기록이
있어서 주력 프로젝트로 충분하다. 빈 곳은 두 가지다.

- **숫자**: 보존 통과율, 건당 비용·지연. 데이터셋 평가를 끝내야 README 맨 위에 올릴 수 있다
- **인프라**: 두 번째 프로젝트(비슷한 사진 찾기)는 기능을 작게 두고 Terraform · k8s · Prometheus ·
  부하 테스트를 연습하는 곳으로 쓴다. AI 는 거의 쓰지 않는다

**CS 기본기 증명**: 서류보다 면접에서 확인받는다. 가장 좋은 재료는 **내가 쓴 코드 안의 CS**다.
외운 개념이 아니라 "Carret 에서는 이렇게 했고 안 하면 이렇게 깨진다"로 답하는 연습을 한다.
아래 §2~§7 이 그 목록이다. 각 항목은 **질문 → 답 요점 → 꼬리 질문 → 직접 해볼 것** 순서다.

## 2. DB — SQLite WAL · busy timeout · 마이그레이션 경합

코드: `backend/app/core/db.py` `get_conn()` — `sqlite3.connect(..., timeout=10)` + `PRAGMA journal_mode=WAL`

**Q. WAL 을 왜 켰나?**
run-inbox 가 여러 파일을 스레드풀로 돌리며 동시에 쓰다 보니 `database is locked` 가 났다.
기본 모드(rollback journal)는 커밋할 때 DB 전체를 배타적으로 잠가서 읽기까지 막는다.
WAL 은 변경을 `-wal` 파일에 덧붙이고, 읽는 쪽은 자기 시작 시점의 스냅숏을 본다.
그래서 **쓰는 중에도 읽기가 막히지 않는다**. 쓰기는 여전히 한 번에 하나다.

**Q. `timeout=10` 은 무엇을 기다리나?**
락이 풀리기를 기다리는 busy timeout 이다. 넘기면 `database is locked` 예외가 난다.

꼬리 질문:

- WAL 에서도 쓰기 두 개가 겹치면? → 하나는 기다린다. 동시 쓰기는 늘지 않는다
- checkpoint 는 무엇이고 언제 일어나나? `-wal` 파일이 계속 커지면?
- WAL 을 쓰면 안 되는 환경은? (네트워크 파일시스템 — 공유 메모리 `-shm` 을 쓰기 때문)
- `journal_mode=WAL` 은 연결마다 거는 설정인가, DB 파일에 남는 설정인가?
- 트랜잭션 격리 수준 네 가지, SQLite 는 어디에 해당하나? dirty read · phantom read 는?
- 서비스가 커지면 SQLite 를 PostgreSQL 로 바꿔야 하는 시점은?

**Q. `_add_column()` 은 왜 `duplicate column` 오류를 무시하나?**
`PRAGMA table_info` 로 "컬럼 없음"을 본 뒤 `ALTER TABLE` 하기 전에 다른 워커가 먼저 추가할 수 있다.
확인과 실행 사이의 틈 — **TOCTOU (time-of-check to time-of-use)** 경합이다.
확인을 없앨 수 없으니 실행이 실패해도 결과가 같으면 괜찮게(멱등하게) 만들었다.

꼬리 질문: 운영 DB 에서는 마이그레이션을 어떻게 관리하나? (Alembic, 버전 테이블)

직접 해볼 것: 스레드 8개로 동시에 INSERT 하는 스크립트를 WAL 켜고/끄고 돌려 락 에러 수와 처리량을 비교

## 3. 동시성 — 락 분리 · double-checked locking · LRU 캐시

코드: `backend/app/services/ai/compositor.py` — `_lock`(알파 캐시), `_load_lock`(rembg 모델 로드), `_load()`, `original_alpha()`

**Q. 락을 왜 두 개로 나눴나?**
모델 로드는 수 초 걸린다. 락 하나로 묶으면 로드하는 동안 캐시 조회까지 전부 멈춘다.
보호하는 자원이 다르면 락도 나눈다 — **락의 범위(granularity)를 좁힌다**.

꼬리 질문:

- 락을 여러 개 쓰면 생길 수 있는 문제는? → 데드락. 네 가지 조건과 막는 방법 (락 순서 고정)
- 뮤텍스와 세마포어 차이, `threading.Lock` 과 `RLock` 차이

**Q. `_load()` 에서 `None` 검사를 왜 두 번 하나?**
double-checked locking. 첫 검사는 이미 로드됐으면 락 없이 바로 돌려주려고, 두 번째 검사는
락을 기다리는 사이 다른 스레드가 먼저 로드했을 수 있어서다.

꼬리 질문: Java 에서는 이 패턴에 `volatile` 이 필요하다. 왜 필요하고, 파이썬에서는 왜 괜찮나? (메모리 가시성, GIL)

**Q. 알파 캐시는 어떻게 동작하나?**
`OrderedDict` 로 만든 LRU 캐시. 조회되면 `move_to_end`, 가득 차면 가장 오래된 것을 뺀다.
항목 하나가 원본 해상도 알파(12MP ≈ 12MB)라 최대 8개로 작게 뒀다.
키에 이미지 해시·백엔드 설정·함수 자체를 넣어 설정이 바뀌면 옛 값을 쓰지 않는다.

꼬리 질문:

- 캐시 조회는 락 안, **오리기 계산은 락 밖**이다. 같은 사진이 동시에 두 번 들어오면? → 둘 다 계산한다 (cache stampede).
  락 안에서 계산하면 막을 수 있지만 모든 요청이 5초씩 줄을 선다. 어느 쪽을 골랐고 왜 괜찮나?
- LRU 말고 LFU · FIFO 는? 각각 언제 유리한가
- LRU 를 O(1) 로 직접 구현하면? (해시맵 + 이중 연결 리스트)
- fal 실패 후 로컬로 대체한 알파는 왜 캐시하지 않나? (품질 낮은 값이 남아 배경 교체까지 오염)

직접 해볼 것: `OrderedDict` 없이 해시맵 + 이중 연결 리스트로 LRU 를 짜고 테스트

## 4. async 와 스레드 — run_in_threadpool · 스레드풀 · Future 취소 · contextvars

코드: `backend/app/util/img_fetch.py` `fetch_image()`, `backend/app/services/pipeline.py` `_POOL`·`_CUTOUT_POOL`·`_OCR_POOL`·`_in_background()`·`_item_check()`

**Q. DNS 조회를 왜 `run_in_threadpool` 로 감쌌나?**
`fetch_image` 는 `async def` 라 이벤트 루프 위에서 돈다. `socket.getaddrinfo` 는 블로킹이라
그대로 부르면 조회하는 동안 **서버의 다른 요청이 전부 멈춘다**. 스레드로 넘기고 `await` 한다.

꼬리 질문:

- FastAPI 에서 `def` 엔드포인트와 `async def` 엔드포인트는 어디서 실행되나?
- `async def` 안에서 `time.sleep(5)` 를 부르면?
- 이벤트 루프는 어떻게 동작하나? 코루틴과 스레드 차이
- 프로세스와 스레드 차이, GIL 이 있는데 스레드풀이 왜 빨라지나? (I/O 대기 중엔 GIL 을 놓는다. CPU 작업은?)
- rembg · DINO 같은 CPU 작업이 많아지면 스레드 대신 무엇을? (프로세스 풀, 별도 워커)

**Q. 스레드풀을 왜 셋으로 나눴나?**
느린 오리기(rembg CPU 약 5초)가 check_photo 자리를 잡아먹지 않게 따로 2개,
EasyOCR 은 스레드 안전하지 않아서 1개. 일의 성격이 다르면 풀을 나눠 서로 굶기지 않는다 (bulkhead).

**Q. 늦은 작업에 `fut.cancel()` 을 부르면 멈추나?**
**아니다.** `Future.cancel()` 은 아직 큐에서 기다리는 작업만 취소한다. 이미 돌고 있으면 `False` 를
돌려주고 끝까지 돈다. 파이썬 스레드는 밖에서 강제로 죽일 수 없다.
그래서 `_item_check` 주석의 "풀을 돌려준다"는 **큐에 있던 작업에만 맞는 말**이다.
→ 면접 전에 알고 있어야 할 코드의 약점.

꼬리 질문: 정말 멈춰야 하는 작업이면? (작업 안에서 취소 플래그 확인, 프로세스로 돌리고 kill)

**Q. `_in_background()` 는 왜 `contextvars.copy_context()` 를 하나?**
contextvars 는 스레드풀 스레드로 자동으로 넘어가지 않는다. 복사해서 넘기지 않으면
Langfuse 트레이스가 끊겨 그 스레드의 VLM 호출이 별도 트레이스로 찍힌다.

꼬리 질문: thread-local 과 contextvars 차이 (코루틴마다 따로 가지려면 contextvars)

## 5. 네트워크·보안 — SSRF 방어와 그 빈틈

코드: `backend/app/util/img_fetch.py` `_is_public_host()`, `_assert_public_url()`, `fetch_image()`

**Q. URL 업로드에서 무엇을 막았나?**
SSRF. 사용자가 준 URL 로 서버가 요청을 보내므로 `http://169.254.169.254/`(클라우드 메타데이터)나
사설망 주소로 내부를 훑을 수 있다. 호스트명이 아니라 **resolve 한 IP 전부**가 사설·루프백·링크로컬·
예약·멀티캐스트가 아닌지 본다. 리다이렉트는 자동으로 따라가지 않고 홉마다 다시 검사한다.

**Q. 이 방어의 빈틈은?** (알고 말하면 오히려 점수가 된다)
`_is_public_host` 에서 한 번 resolve 하고, 실제 연결은 httpx 가 **다시 resolve** 한다.
TTL 이 짧은 DNS 로 첫 조회엔 공인 IP, 두 번째엔 `127.0.0.1` 을 주면 검사를 통과한다 —
**DNS rebinding**, §2 와 같은 TOCTOU 구조다. 막으려면 검사한 IP 로 직접 연결해야 한다
(IP 로 접속하고 Host 헤더·SNI 는 원래 호스트명으로).

꼬리 질문:

- DNS 조회 과정 (재귀 리졸버, 루트 → TLD → 권한 서버), TTL
- 사설 IP 대역 세 개는? `169.254.0.0/16` 은 왜 따로 위험한가
- IPv4-mapped IPv6 주소(`::ffff:127.0.0.1`)도 막히나? → 파이썬 버전별로 직접 확인해 볼 것
- HTTP 리다이렉트 상태 코드 301 · 302 · 307 · 308 차이
- 응답 크기 제한을 다운로드 **뒤에** 검사한다. 10GB 응답이 오면? (스트리밍으로 받으며 끊기)

직접 해볼 것: 로컬에서 rebinding 을 흉내 내는 테스트 (getaddrinfo 를 두 번째 호출부터 다른 값으로 바꿔 치기)

## 6. 외부 API — 타임아웃 · deadline · 재시도 · 연결 재사용

코드: `backend/app/services/pipeline.py` `_remaining()`, `backend/app/core/vlm.py` `get_client()`·`retryable()`

**Q. `_remaining(deadline)` 은 왜 있나?**
대기 상한을 **작업을 넘긴 시점부터** 잰다. 단계마다 타임아웃을 따로 주면 직렬로 더해져
전체 응답 시간이 길어진다. 남은 시간만 기다린다.

꼬리 질문: `time.time()` 대신 `time.monotonic()` 을 쓰는 이유는? (벽시계는 NTP 로 거꾸로 갈 수 있다)

**Q. `get_client()` 에 `@lru_cache(maxsize=1)` 을 왜 붙였나?**
클라이언트를 프로세스에서 하나만 쓴다. 호출마다 새로 만들면 매번 TCP 연결과 TLS 핸드셰이크를
다시 한다 (변환 1회에 VLM 5~6회).

꼬리 질문:

- TCP 3-way handshake, TLS 핸드셰이크 과정. 연결 재사용(keep-alive, 커넥션 풀)이 줄이는 것은?
- HTTP/1.1 과 HTTP/2 차이
- 인자 없는 함수에 `lru_cache` 를 붙이면 싱글톤이 된다. 두 스레드가 동시에 처음 부르면 한 번만 만들어지나?
  (→ 캐시 구조는 안전하지만 함수가 두 번 불릴 수 있다. §3 의 `_load()` 와 비교)

**Q. 어떤 실패만 재시도하나?**
타임아웃 · 429 · 5xx · 깨진 응답만. 400 처럼 요청이 틀린 경우나 코드 오류는 다시 해도 똑같다.

꼬리 질문:

- 지수 백오프에 jitter 를 더하는 이유 (여러 클라이언트가 동시에 재시도해 몰리는 것 방지)
- 재시도해도 안전하려면? → 멱등성. 이미지 생성 API 를 재시도하면 비용은?
- circuit breaker 는 무엇이고 언제 필요한가

## 7. 컨테이너 — Dockerfile 레이어 캐시

코드: `Dockerfile` — `requirements.txt` 를 먼저 복사해 설치, 코드는 나중에 복사. torch 는 CPU 전용 wheel 을 먼저 설치

**Q. 복사 순서를 왜 이렇게 했나?**
레이어는 위에서부터 캐시된다. 자주 바뀌는 코드를 뒤에 두면 코드만 바뀔 때 의존성 설치를 건너뛴다.

**Q. torch 를 왜 따로 먼저 설치하나?**
기본 PyPI 로 받으면 CUDA 런타임(수 GB)까지 딸려 온다. GPU 가 없는 서버라 CPU wheel 이면 된다.
먼저 설치해 두면 뒤의 `-r requirements.txt` 가 "이미 만족"으로 건너뛴다.

꼬리 질문:

- 컨테이너와 VM 차이. 컨테이너 격리는 무엇으로 하나? (namespace, cgroup)
- 이미지를 더 줄이려면? (multi-stage build, `.dockerignore`)
- 컨테이너 안에서 root 로 도는 것의 위험
- uvicorn 워커를 여러 개 띄우면 §3 의 모듈 전역 캐시·모델은 어떻게 되나? (프로세스마다 따로 → 메모리 배수)

## 8. 연습 방법과 할 일

연습 방법:

- 하루 3~4개, **소리 내어** 답하고 녹음해서 들어 보기. 요점만 외우지 말고 "Carret 에서는"으로 시작하기
- 막히는 꼬리 질문은 교재(운영체제 · 네트워크 · DB)의 해당 장을 읽고 이 노트에 한 줄 추가

할 일:

- [ ] §2 WAL 켜고/끄고 동시 쓰기 비교 실험 → 블로그 글
- [ ] §3 LRU 캐시 직접 구현 (해시맵 + 이중 연결 리스트)
- [ ] §4 `_item_check` 주석 "풀을 돌려준다" 를 실제 동작(큐에 있던 작업만 취소)에 맞게 고칠지 판단
- [ ] §5 DNS rebinding 빈틈 — 검사한 IP 로 직접 연결하도록 고칠지 판단, 고치면 테스트 추가
- [ ] §5 응답 크기 제한을 스트리밍 중에 검사
- [ ] §5 IPv4-mapped IPv6 가 막히는지 배포 파이썬 버전에서 확인
- [ ] 운영체제 · 네트워크 · DB 질문 목록 하나 정해서 매일 몇 개씩
