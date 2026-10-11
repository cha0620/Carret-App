# 프로세스부터 컨테이너까지 — 기초 노트

목표: **"컨테이너는 격리된 프로세스다"를 내 말로 설명하고, 꼬리 질문 3개까지 버티기.**
읽는 순서대로 쌓이게 썼다. 1~7장은 OS 기초, 8~9장은 그 위에 컨테이너, 10장은 PC 있을 때 할 실험, 11장은 스스로 묻고 답하기.

---

## 0. 같이 볼 자료

| 자료 | 무엇 | 언제 |
|---|---|---|
| **OSTEP** (Operating Systems: Three Easy Pieces) — [ostep.org](https://pages.cs.wisc.edu/~remzi/OSTEP/) 무료, 한국어판 『운영체제: 아주 쉬운 세 가지 이야기』 | OS 교과서 중 가장 읽기 쉬움. 이 노트의 1~7장 원본에 해당 | **Virtualization 파트 (4~6장 프로세스·API·제한적 직접 실행, 7~9장 스케줄링, 13~15장 주소 공간)** 부터 |
| Liz Rice, **"Containers From Scratch"** (GOTO 2018, 유튜브 약 40분) | Go 코드 몇십 줄로 컨테이너를 직접 만든다. namespace · chroot · cgroup 이 실제로 무엇인지 한 번에 보임 | 이 노트 8장을 읽은 뒤 |
| Julia Evans, **"How Containers Work!"** (zine, 유료) | 그림으로 된 짧은 책. 복습용 | 선택 |
| Docker 공식 문서 Get started | 명령어 사용법 | 실습할 때 |

---

## 1. 프로그램과 프로세스

- **프로그램**: 디스크에 있는 실행 파일. 가만히 있는 코드.
- **프로세스**: 그 프로그램이 **실행 중인 것**. 같은 프로그램으로 프로세스를 여러 개 띄울 수 있다 (`python` 을 터미널 두 개에서 실행).

프로세스가 가지고 있는 것:

| 가진 것 | 설명 |
|---|---|
| **주소 공간** | 자기만의 메모리 (코드 · 데이터 · 힙 · 스택). 다른 프로세스의 메모리는 못 본다 |
| **레지스터 상태** | 지금 어디를 실행 중인지 (프로그램 카운터 등) |
| **열린 파일 목록** | 파일 · 소켓 · 파이프. 번호로 다룬다 (파일 디스크립터 0 · 1 · 2 = 표준 입력 · 출력 · 에러) |
| **PID** | 프로세스 번호 |
| **부모 PID, 사용자, 환경 변수 …** | |

커널은 이 정보를 프로세스마다 하나의 구조체(**PCB**, 리눅스에서는 `task_struct`)에 담아 관리한다.

**상태**: 실행(Running) · 준비(Ready, CPU 차례를 기다림) · 대기(Blocked, 디스크나 네트워크 응답을 기다림).
Carret 이 Gemini 응답을 기다리는 동안 그 스레드는 **대기** 상태다 — CPU 를 안 쓴다.

---

## 2. 커널과 시스템 콜

- CPU 에는 **커널 모드**(뭐든 할 수 있음)와 **사용자 모드**(제한됨)가 있다. 우리 프로그램은 사용자 모드에서 돈다.
- 파일 열기 · 네트워크 보내기 · 새 프로세스 만들기처럼 하드웨어나 다른 프로세스에 영향을 주는 일은
  **시스템 콜**로 커널에 부탁한다 (`open`, `read`, `write`, `fork`, `execve`, `kill` …).
- 시스템 콜을 하면 CPU 가 커널 모드로 바뀌고, 커널이 일을 한 뒤 사용자 모드로 돌아온다.

> 왜 중요한가: **컨테이너 안의 프로그램이 하는 시스템 콜도 결국 호스트의 같은 커널이 처리한다.** 이게 컨테이너와 VM 의 근본 차이다 (8장).

---

## 3. 프로세스 만들기 — fork · exec · wait

리눅스에서 새 프로세스는 이렇게 생긴다:

1. **`fork()`**: 지금 프로세스를 **복제**한다. 부모에게는 자식 PID 를, 자식에게는 `0` 을 돌려준다.
   (실제로 메모리를 다 복사하지 않고, 쓸 때 복사한다 — **copy-on-write**)
2. **`exec()`**: 자식이 자기 내용을 **다른 프로그램으로 갈아 끼운다**. PID 는 그대로.
3. **`wait()`**: 부모가 자식이 끝나기를 기다리고 **종료 코드를 받아 간다**.

터미널에서 `ls` 를 치면 셸이 fork → 자식이 exec(`ls`) → 셸이 wait 한다.

### 좀비와 고아
- **좀비**: 자식은 끝났는데 부모가 아직 `wait()` 하지 않은 상태. 메모리는 없지만 프로세스 표에 한 줄(PID, 종료 코드)이 남아 있다.
  쌓이면 PID 가 바닥날 수 있다.
- **고아**: 부모가 먼저 죽은 자식. 커널이 **PID 1 (init)** 에게 입양시키고, init 이 대신 `wait()` 해서 치운다.

> **PID 1 은 특별하다**: 시스템 전체의 고아를 거둬 가는 역할. 컨테이너에서 이게 문제가 된다 (9장).

---

## 4. 시그널

프로세스에게 보내는 짧은 알림. `kill -<시그널> <PID>` 로 보낸다.

| 시그널 | 번호 | 뜻 | 막을 수 있나 |
|---|---|---|---|
| `SIGINT` | 2 | Ctrl+C | 핸들러로 잡을 수 있음 |
| `SIGTERM` | 15 | "정리하고 끝나 줘" (기본 `kill`) | 잡을 수 있음 → **정리 후 종료(graceful shutdown)** |
| `SIGKILL` | 9 | 즉시 죽임 | **못 막음** |
| `SIGCHLD` | 17 | "자식이 끝났어" (부모에게) | |

- 종료 코드 관례: 시그널로 죽으면 **128 + 시그널 번호**. `137` = 128 + 9 → SIGKILL 로 죽었다 (컨테이너 OOM 에서 자주 봄, 6장).
- 서버(uvicorn)는 SIGTERM 을 받으면 새 요청을 멈추고 처리 중인 요청을 끝낸 뒤 종료한다.

---

## 5. 스레드

- **스레드** = 한 프로세스 안의 실행 흐름. 같은 프로세스의 스레드끼리는 **주소 공간(메모리)을 공유**한다. 스택과 레지스터만 각자.
- 그래서:
  - 만들기 · 전환이 프로세스보다 싸다 (주소 공간을 바꿀 필요가 없다)
  - 대신 같은 변수를 동시에 건드리면 **경쟁 상태(race condition)** → 락이 필요

| | 프로세스 | 스레드 |
|---|---|---|
| 메모리 | 따로 | 공유 |
| 하나가 죽으면 | 다른 프로세스는 멀쩡 | 같은 프로세스 전체가 위험 |
| 통신 | 파이프 · 소켓 · 공유 메모리 (번거로움) | 그냥 변수 (대신 락) |
| 만들기 · 전환 비용 | 큼 | 작음 |

### Carret 에서
- `routes/inpaint.py`: `ThreadPoolExecutor` 로 A · B 두 모델을 **동시에** 부른다. 둘 다 네트워크를 기다리는 일(I/O)이라 스레드로 충분하다.
- 같은 파일: `_running` 집합 + `threading.Lock` — 같은 사진에 요청이 두 번 동시에 들어와 유료 호출이 두 배로 나가는 걸 막는다. **경쟁 상태를 락으로 막은 실제 예.**
- 꼬리 질문 대비: **"서버를 두 대로 늘리면?"** → 이 락은 한 프로세스 안에서만 통한다. 프로세스 · 서버가 여러 개면 DB 나 Redis 같은 공용 저장소로 잠가야 한다.

### 파이썬 GIL (한 줄만)
CPython 은 한 번에 한 스레드만 파이썬 코드를 실행한다(GIL). 그래서 **I/O 를 기다리는 일은 스레드로 빨라지지만, CPU 계산은 안 빨라진다** → CPU 일은 멀티 프로세스(`ProcessPoolExecutor`)로.
Carret 의 DINOv2 계산(CPU)과 VLM 호출 대기(I/O)를 이 기준으로 설명해 보면 좋은 연습이 된다.

---

## 6. 메모리와 자원 — cgroup 을 이해하기 위한 만큼만

- **가상 메모리**: 프로세스마다 "자기만 쓰는 메모리"처럼 보이는 주소를 주고, 커널이 실제 물리 메모리에 연결해 준다(페이지 단위).
  덕분에 프로세스끼리 메모리를 못 건드린다.
- 메모리가 모자라면 리눅스는 **OOM killer** 가 프로세스를 골라 SIGKILL 로 죽인다.
- **CPU 스케줄링**: 준비 상태 프로세스들에게 CPU 시간을 나눠 준다. 리눅스 기본 스케줄러는 "공평하게" 나누려 한다.

> **cgroup** (control group): 프로세스 묶음에 **"CPU 는 이만큼, 메모리는 이만큼까지"** 상한을 거는 커널 기능.
> 메모리 상한을 넘으면 그 묶음 안에서 OOM killer 가 돈다 → 컨테이너가 종료 코드 **137** 로 죽는다.

---

## 7. 파일 시스템 · 마운트 · chroot

- 리눅스는 디렉터리 나무가 하나(`/`)이고, 디스크나 다른 파일 시스템을 그 나무의 한 가지에 **마운트**해서 붙인다.
- **`chroot`**: 프로세스에게 "여기가 네 `/` 야"라고 루트 디렉터리를 바꿔 준다. 그 프로세스는 바깥 파일이 안 보인다.
  아주 오래된 기능이고, 컨테이너의 "자기만의 파일 시스템"의 조상이다.

---

## 8. 그래서 컨테이너란

> **컨테이너 = 호스트 커널을 같이 쓰는 평범한 프로세스인데, ① 보이는 범위를 좁히고(namespace) ② 쓸 자원을 제한하고(cgroup) ③ 자기만의 파일 시스템을 준 것(이미지 레이어).**

### ① namespace — "무엇이 보이나"를 나눈다

| namespace | 따로 보이는 것 | 효과 |
|---|---|---|
| **pid** | 프로세스 번호 | 안에서는 내가 PID 1, 다른 컨테이너 · 호스트 프로세스가 안 보임 |
| **mnt** | 마운트 (파일 시스템 나무) | 자기 `/` 를 가짐 (chroot 의 발전형) |
| **net** | 네트워크 장치 · IP · 포트 | 컨테이너마다 포트 8000 을 각자 쓸 수 있음 |
| **uts** | 호스트 이름 | |
| **ipc** | 공유 메모리 등 프로세스 간 통신 | |
| **user** | 사용자 ID | 안에서는 root, 밖에서는 일반 사용자 |
| cgroup, time | | |

### ② cgroup — "얼마나 쓰나"를 막는다
`docker run --memory 512m --cpus 1` → 커널의 cgroup 설정이 된다 (6장).

### ③ 이미지 레이어 — "어떤 파일이 있나"
- 이미지는 **읽기 전용 레이어**를 여러 장 쌓은 것. Dockerfile 의 명령 하나가 대략 레이어 하나.
- 컨테이너를 띄우면 맨 위에 **쓰기 레이어** 하나를 얹는다(overlayfs). 파일을 고치면 그때 위로 복사해서 고친다(copy-on-write, 3장의 fork 와 같은 생각).
- 그래서 **컨테이너를 지우면 쓰기 레이어도 사라진다** → 남겨야 할 데이터는 **볼륨**(호스트 디렉터리 마운트)에.

### VM 과의 차이

| | VM | 컨테이너 |
|---|---|---|
| 커널 | 각자 따로 (게스트 OS 를 통째로 띄움) | **호스트 커널 공유** |
| 시작 | 수십 초 | 1초 안팎 (프로세스 하나 띄우는 것) |
| 크기 | GB 단위 | MB ~ 수 GB |
| 격리 | 강함 (하드웨어 가상화) | 약함 (커널 버그 하나면 위험) |
| 다른 OS | 리눅스 위에 윈도우 가능 | 리눅스 커널 위에서는 리눅스 프로그램만 |

---

## 9. Carret 의 `Dockerfile` 로 확인하기

```dockerfile
FROM python:3.12-slim                 # 바닥 레이어들 (데비안 + 파이썬)
COPY requirements.txt .               # ① 의존성 목록 먼저
RUN pip install ... torch (CPU)       #    → 이 레이어는 requirements.txt 가 안 바뀌면 캐시 재사용
RUN pip install -r requirements.txt
COPY backend/ ./backend/              # ② 코드는 나중에 — 코드만 바뀌면 위 레이어는 다시 안 만듦
CMD ["uvicorn", "main:app", ...]      # ③ 배열(exec) 형식
```

| 줄 | OS 개념 | 설명 |
|---|---|---|
| 의존성 먼저, 코드 나중 | 레이어 캐시 | 자주 바뀌는 것을 아래 레이어에 두면 매번 전부 다시 빌드한다 |
| torch CPU 전용 | 이미지 크기 | GPU 가 없으니 CUDA 런타임(수 GB)을 안 받는다 |
| **`CMD [...]` 배열 형식** | **PID 1 · 시그널** | uvicorn 이 **바로 PID 1** 이 되어 `docker stop` 의 SIGTERM 을 직접 받는다. 문자열 형식(`CMD uvicorn main:app`)이면 `/bin/sh -c` 가 PID 1 이 되어 시그널이 앱까지 안 갈 수 있다 → 10초 기다렸다가 SIGKILL |
| (없음) SQLite 파일 | 볼륨 | 지금은 컨테이너 쓰기 레이어에 생긴다 → 컨테이너를 지우면 DB 도 사라짐. 볼륨을 붙여야 함 |
| (없음) `frontend/` | | 지금 이미지에 화면 파일이 복사되지 않는다 — 배포 전에 고칠 것 |

### PID 1 이 왜 특별한가 (컨테이너판)
1. **시그널**: 커널은 PID 1 에게는 **핸들러를 걸지 않은 시그널의 기본 동작(종료)을 적용하지 않는다.** 그래서 SIGTERM 을 처리하지 않는 프로그램이 PID 1 이면 `docker stop` 이 안 먹고 10초 뒤 SIGKILL 로 끝난다.
2. **좀비 청소**: PID 1 은 고아를 입양해 `wait()` 해 줘야 한다(3장). 일반 앱은 이걸 안 하므로 자식 프로세스를 많이 만드는 앱이면 좀비가 쌓인다.
3. 해결: 앱이 직접 처리하거나, `docker run --init`(작은 init 프로그램 `tini` 를 PID 1 로 넣어 줌).

---

## 10. 실험 (PC 있을 때)

### 10-1. fork 와 PID — 파이썬으로
```python
import os, time
pid = os.fork()
if pid == 0:
    print("자식", os.getpid(), "부모", os.getppid())
    os._exit(0)
else:
    print("부모", os.getpid(), "자식", pid)
    time.sleep(30)          # 이 동안 다른 창에서: ps -o pid,stat,cmd --ppid <부모 PID>
    os.wait()               # → 자식이 'Z'(좀비)로 보이다가, wait 뒤 사라짐
```

### 10-2. 시그널 받기
```python
import signal, time
signal.signal(signal.SIGTERM, lambda s, f: (print("정리하고 끝낼게"), exit(0)))
while True: time.sleep(1)   # 다른 창에서 kill <PID> → 메시지 출력, kill -9 <PID> → 아무 말 없이 죽음
```

### 10-3. 컨테이너는 프로세스다
```bash
docker run -d --name t python:3.12-slim sleep 1000
docker exec t ps aux              # 안: sleep 이 PID 1  (ps 가 없으면 apt-get install procps)
ps aux | grep "sleep 1000"        # 밖: 평범한 프로세스, 다른 PID
docker rm -f t
```

### 10-4. namespace 를 손으로 만들기 (리눅스, root)
```bash
sudo unshare --pid --fork --mount-proc bash
ps aux        # bash 가 PID 1, 다른 프로세스가 안 보인다 — 이게 pid namespace
exit
```

### 10-5. PID 1 과 시그널
```bash
docker run -d --name a python:3.12-slim sleep 1000
time docker stop a                # sleep 은 SIGTERM 핸들러가 없음 + PID 1 → 약 10초 걸림
docker run -d --init --name b python:3.12-slim sleep 1000
time docker stop b                # tini 가 PID 1 → 바로 끝남
docker rm a b
```

### 10-6. 메모리 상한과 OOM
```bash
docker run --rm --memory 100m python:3.12-slim \
  python -c "x = bytearray(300 * 1024 * 1024)"
echo $?       # 137 = 128 + 9 (SIGKILL) — cgroup 상한을 넘어 OOM killer 가 죽였다
```

### 10-7. Carret 이미지
```bash
docker build -t carret .
docker run --rm -p 8000:8000 --env-file .env carret
# 확인: 화면(/)이 뜨나? API 만 뜨나? (9장의 frontend 문제)
docker image ls carret     # 크기 확인 — 어느 레이어가 큰지는 docker history carret
```

---

## 11. 스스로 묻고 답하기

소리 내어 **1분 안에** 답해 본다. 막히면 해당 장으로.

<details><summary>Q1. 프로세스와 스레드의 차이는? → 그럼 컨텍스트 스위칭 비용은 왜 다른가?</summary>

프로세스는 주소 공간이 따로, 스레드는 한 프로세스 안에서 메모리를 공유한다(5장).
스레드끼리 전환할 때는 주소 공간을 바꿀 필요가 없어서 싸다. 대신 공유 메모리라 락이 필요하다.
</details>

<details><summary>Q2. fork 와 exec 는 각각 무엇을 하나? 왜 둘로 나눠져 있나?</summary>

fork 는 복제, exec 는 내용 갈아 끼우기(3장). 둘 사이에 자식이 표준 입출력을 바꾸는 등(리다이렉션 `>`, 파이프 `|`) 준비를 할 수 있어서 셸이 이 구조를 쓴다.
</details>

<details><summary>Q3. 좀비 프로세스는 무엇이고 어떻게 치우나?</summary>

끝났는데 부모가 wait 하지 않은 자식. 부모가 wait 하거나, 부모가 죽으면 init(PID 1)이 입양해서 치운다(3장).
</details>

<details><summary>Q4. 컨테이너와 VM 의 차이는? → 컨테이너가 VM 보다 덜 안전한 이유는?</summary>

컨테이너는 호스트 커널을 공유하는 격리된 프로세스, VM 은 커널까지 따로(8장). 커널을 공유하므로 커널 취약점 하나로 격리가 깨질 수 있다.
</details>

<details><summary>Q5. 컨테이너에서 PID 1 은 왜 특별한가?</summary>

① 핸들러 없는 시그널의 기본 동작이 적용되지 않음 → SIGTERM 무시 → docker stop 이 10초 뒤 SIGKILL ② 고아 · 좀비를 거둘 책임. 해결은 exec 형식 CMD, 앱의 시그널 처리, `--init`(9장).
</details>

<details><summary>Q6. 컨테이너 메모리 제한을 넘으면 무슨 일이 일어나나?</summary>

cgroup 상한을 넘으면 커널 OOM killer 가 SIGKILL → 종료 코드 137(6장, 10-6).
</details>

<details><summary>Q7. 컨테이너를 지워도 데이터를 남기려면?</summary>

쓰기 레이어는 컨테이너와 함께 사라지므로 볼륨으로 호스트 디렉터리를 마운트한다. Carret 의 SQLite 파일이 그 예(9장).
</details>

<details><summary>Q8. Dockerfile 에서 requirements.txt 를 코드보다 먼저 COPY 하는 이유는?</summary>

레이어 캐시. 의존성 목록이 안 바뀌면 설치 레이어를 재사용하고 코드 레이어만 다시 만든다(9장).
</details>

<details><summary>Q9. Carret 에서 VLM 호출을 스레드로 병렬화했다. 왜 프로세스가 아니라 스레드인가?</summary>

네트워크를 기다리는 I/O 작업이라 GIL 이 문제되지 않고, 스레드가 더 가볍다. CPU 계산(DINOv2)이라면 프로세스를 고려(5장).
</details>

<details><summary>Q10. inpaint 의 threading.Lock 은 서버를 여러 대로 늘려도 통하나?</summary>

아니다. 락은 한 프로세스 메모리 안에서만 의미가 있다. 여러 프로세스 · 서버면 DB 나 Redis 같은 공용 저장소로 잠가야 한다(5장).
</details>

---

## 12. 진도 체크

○ 설명 가능 · △ 대충 앎 · × 모름 — 볼 때마다 갱신

| 주제 | 상태 |
|---|---|
| 프로그램 vs 프로세스, PCB, 상태 | |
| 커널 모드 · 사용자 모드 · 시스템 콜 | |
| fork · exec · wait, 좀비 · 고아 | |
| 시그널 (TERM · KILL, 128+n) | |
| 프로세스 vs 스레드, 경쟁 상태, 락 | |
| GIL, I/O 바운드 vs CPU 바운드 | |
| 가상 메모리, OOM killer | |
| 마운트 · chroot | |
| namespace 종류 | |
| cgroup | |
| 이미지 레이어 · overlayfs · 볼륨 | |
| 컨테이너 vs VM | |
| PID 1 문제와 해결 | |
| Carret Dockerfile 한 줄씩 설명 | |

다음 노트 후보: `02-network.md` (HTTP · DNS · TLS · 리버스 프록시 — Carret 배포와 겹침), `03-db.md` (인덱스 · 트랜잭션 · SQLite 한계).
