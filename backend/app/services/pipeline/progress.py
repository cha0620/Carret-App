"""변환 작업 진행 상황 (10-12 사용자: "결과 먼저 보여 주고 검사는 뒤에서").

POST /api/transform/jobs 가 작업을 만들고 스레드에서 돌린다. 작업 id 는 파이프라인 상태(job_id)로 넘어가고,
노드는 생성이 끝날 때마다 publish 로 미리보기를 넘긴다. 화면은 GET 으로 물어 "검사 중" 배지를 달아 먼저 보여 준다.

미리보기는 **메모리에만** 둔다 — 검사 전 이미지가 /storage/result/ 의 예측 가능한 주소에 남지 않게
(generation.py 의 같은 규칙). 작업 id(uuid4 hex)를 아는 화면만 볼 수 있다. 서버를 다시 켜면 사라진다.
검사에 떨어진 생성본(재생성 · 배경 교체로 넘어감)과 끝난 작업의 미리보기는 지운다 — 검사 전 사진이 남지 않게 (10-12 리뷰).
"""
import threading
import time
import uuid
from dataclasses import dataclass, field

TTL_S = 30 * 60          # 끝난 작업은 30분 뒤 지운다
STUCK_S = 15 * 60        # 이만큼 안 끝나면 실패로 닫는다 (외부 API 무한 대기 등)
MAX_JOBS = 200           # 기록 상한 — 끝난 작업부터 지운다
MAX_RUNNING = 4          # 동시에 도는 작업 상한 — 넘으면 429 (반복 요청으로 유료 호출이 끝없이 늘지 않게)

DONE = ("done", "error")


class Busy(Exception):
    """동시에 도는 작업이 상한에 찼다."""


@dataclass
class Job:
    id: str
    file_id: str
    stage: str = "queued"
    attempt: int = 0
    preview: bytes | None = None
    result: dict | None = None
    error: dict | None = None          # {"status": int, "detail": str}
    created: float = field(default_factory=time.time)
    updated: float = field(default_factory=time.time)


_lock = threading.Lock()
_jobs: dict[str, Job] = {}


def _sweep(now: float) -> None:
    for j in list(_jobs.values()):
        if j.stage not in DONE and now - j.created > STUCK_S:
            j.stage, j.preview, j.updated = "error", None, now
            j.error = {"status": 504, "detail": "너무 오래 걸려 멈췄어요 — 다시 시도해 주세요"}
        elif j.stage in DONE and now - j.updated > TTL_S:
            _jobs.pop(j.id, None)
    done = sorted((j for j in _jobs.values() if j.stage in DONE), key=lambda j: j.updated)
    while len(_jobs) >= MAX_JOBS and done:      # 도는 작업은 지우지 않는다
        _jobs.pop(done.pop(0).id, None)


def create(file_id: str) -> tuple[Job, bool]:
    """(작업, 새로 만들었나). 같은 사진으로 이미 도는 작업이 있으면 그걸 돌려준다 (두 번 눌러도 생성은 한 번)."""
    with _lock:
        now = time.time()
        _sweep(now)
        running = [j for j in _jobs.values() if j.stage not in DONE]
        same = next((j for j in running if j.file_id == file_id), None)
        if same:
            return same, False
        if len(running) >= MAX_RUNNING:
            raise Busy()
        j = Job(id=uuid.uuid4().hex, file_id=file_id)
        _jobs[j.id] = j
        return j, True


def get(job_id: str) -> Job | None:
    with _lock:
        return _jobs.get(job_id)


def publish(job_id: str | None, stage: str, *, attempt: int | None = None,
            preview: bytes | None = None, clear_preview: bool = False) -> None:
    """파이프라인 노드가 부른다. job_id 가 없으면(eval · 동기 /api/transform) 아무 일도 안 한다."""
    if not job_id:
        return
    with _lock:
        j = _jobs.get(job_id)
        if j is None or j.stage in DONE:
            return
        j.stage = stage
        if attempt is not None:
            j.attempt = attempt
        if clear_preview:
            j.preview = None
        if preview is not None:
            j.preview = preview
        j.updated = time.time()


def finish(job_id: str, *, result: dict | None = None, error: dict | None = None) -> None:
    with _lock:
        j = _jobs.get(job_id)
        if j is None:
            return
        j.stage = "error" if error else "done"
        j.result, j.error = result, error
        j.preview = None   # 끝나면 진짜 결과 주소만 — 오류여도 검사 전 사진을 남기지 않는다
        j.updated = time.time()
