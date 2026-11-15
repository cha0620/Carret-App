"""공개 데모 비용 상한 (11-15).

유료 API(Gemini VLM · fal.ai)를 부르는 요청은 받기 **전에** 예상 비용을 하루 누적에 더해 보고,
서버 전체 상한(`QUOTA_DAILY_USD`)이나 IP 하나 상한(`QUOTA_PER_IP_DAILY_USD`)을 넘으면 429 로 막는다.

- 비용은 실측이 아니라 README "단계별 모델 · 비용" 표의 중앙값 기반 **추정치**다. 실제 청구는 fal · Google 콘솔에서
  본다 — 이 모듈은 "공개 링크가 퍼져도 하루에 이 이상은 안 나간다"는 안전장치다
- 4xx 응답(본문 오류 · 없는 file_id · 실행 중 409 …)은 유료 호출 전에 끝난 것이라 예약을 돌려준다 — 안 그러면 잘못된
  요청만으로 하루 상한을 공짜로 다 태울 수 있다 (11-15 리뷰). 5xx · 연결 끊김은 유료 호출이 이미 나갔을 수 있어 돌려주지 않는다
- DB 오류면 막는다 (fail-closed, 503) — 비용 안전장치가 "확인 못 함"을 통과로 치면 안 된다
- 하루는 KST 자정 기준 (사용자가 보는 날짜와 맞춘다)
- `QUOTA_ENABLED=false`(기본)면 아무것도 하지 않는다 — 로컬 개발 · 테스트 · eval 은 영향 없음
"""
import ipaddress
import logging
import re
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone

from fastapi import Request
from starlette.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from app.core import db
from app.core.config import settings

logger = logging.getLogger("carret.quota")
KST = timezone(timedelta(hours=9))

# (메서드, 경로 정규식, 예상 USD). 위에서부터 처음 맞는 것. 여기 없는 요청(조회 · 피드백 · 정적 파일)은 0 — 세지 않는다.
# 근거: README 단계별 비용 표 (생성 경로 합계 약 $0.03, 업로드 ~ 변환 전 VLM 약 $0.012 — 10-09)
COSTS: list[tuple[str, re.Pattern, float]] = [
    ("POST", re.compile(r"/api/transform"), 0.04),            # 생성 + 재생성 평균 1.38회 + verify — 여유를 둔 값
    ("POST", re.compile(r"/api/inpaint"), 0.03),              # A/B 두 모델 생성 (/api/inpaint/pick 은 아래에서 0)
    ("POST", re.compile(r"/api/analyze"), 0.012),             # analyze (high)
    ("POST", re.compile(r"/api/items(/[^/]+/files)?"), 0.004),  # 각도 분류 · 사진 검토 (low)
    ("POST", re.compile(r"/api/items/[^/]+/review"), 0.004),
    ("POST", re.compile(r"/api/items/[^/]+/arrange"), 0.002),
    ("POST", re.compile(r"/api/components"), 0.002),          # lite
]
FREE = [("POST", re.compile(r"/api/inpaint/pick"))]


def cost_of(method: str, path: str) -> float:
    for m, rx in FREE:
        if method == m and rx.fullmatch(path):
            return 0.0
    for m, rx, usd in COSTS:
        if method == m and rx.fullmatch(path):
            return usd
    return 0.0


def client_ip(request: Request) -> str:
    """trust_proxy_headers 면 Cloudflare 가 덮어쓰는 cf-connecting-ip 만 믿는다. x-forwarded-for 의 맨 왼쪽 값은
    클라이언트가 정할 수 있어 쓰지 않는다 (11-15 리뷰). IPv6 는 /64 로 묶는다 — 주소만 바꿔 상한을 피하지 못하게."""
    raw = request.headers.get("cf-connecting-ip", "") if settings.trust_proxy_headers else ""
    raw = raw.strip() or (request.client.host if request.client else "")
    try:
        ip = ipaddress.ip_address(raw)
    except ValueError:
        return "unknown"
    if ip.version == 6:
        return str(ipaddress.ip_network(f"{ip}/64", strict=False))
    return str(ip)


def today() -> str:
    return datetime.now(KST).strftime("%Y-%m-%d")


def try_reserve(ip: str, usd: float, day: str | None = None) -> str | None:
    """상한 안이면 누적에 더하고 None, 넘으면 이유(문자열)를 돌려준다. 확인과 더하기는 한 트랜잭션."""
    day = day or today()
    with closing(db.get_conn()) as conn, conn as c:
        c.execute("BEGIN IMMEDIATE")   # 동시 요청 둘이 같이 "아직 여유 있음"을 보고 둘 다 통과하지 않게
        total = c.execute("SELECT COALESCE(SUM(usd), 0) FROM usage WHERE day = ?", (day,)).fetchone()[0]
        mine = c.execute("SELECT COALESCE(usd, 0) FROM usage WHERE day = ? AND ip = ?", (day, ip)).fetchone()
        mine = mine[0] if mine else 0.0
        if total + usd > settings.quota_daily_usd:
            return "daily"
        if mine + usd > settings.quota_per_ip_daily_usd:
            return "ip"
        c.execute(
            "INSERT INTO usage(day, ip, usd, calls) VALUES (?, ?, ?, 1) "
            "ON CONFLICT(day, ip) DO UPDATE SET usd = usd + excluded.usd, calls = calls + 1",
            (day, ip, usd))
    return None


def refund(ip: str, usd: float, day: str) -> None:
    with closing(db.get_conn()) as conn, conn as c:
        c.execute("UPDATE usage SET usd = MAX(usd - ?, 0), calls = MAX(calls - 1, 0) WHERE day = ? AND ip = ?",
                  (usd, day, ip))


def usage_today(day: str | None = None) -> dict:
    day = day or today()
    with closing(db.get_conn()) as conn, conn as c:
        row = c.execute("SELECT COALESCE(SUM(usd), 0), COALESCE(SUM(calls), 0), COUNT(*) FROM usage WHERE day = ?",
                        (day,)).fetchone()
    return {"day": day, "usd": round(row[0], 4), "calls": row[1], "ips": row[2],
            "daily_limit_usd": settings.quota_daily_usd}


MESSAGES = {
    "daily": "오늘 데모 사용량이 다 찼어요. 내일(한국 시간 자정 이후) 다시 시도해 주세요.",
    "ip": "오늘 이 기기에서 쓸 수 있는 횟수를 다 썼어요. 내일 다시 시도해 주세요.",
}


async def quota_middleware(request: Request, call_next):
    if not settings.quota_enabled:
        return await call_next(request)
    path = request.scope["path"]               # 라우팅이 보는 경로와 같은 값으로 (url.path 는 %23 등에서 갈린다)
    usd = cost_of(request.method, path)
    if usd <= 0:
        return await call_next(request)
    ip, day = client_ip(request), today()
    try:
        why = await run_in_threadpool(try_reserve, ip, usd, day)   # SQLite 대기가 이벤트 루프를 막지 않게
    except sqlite3.Error:
        logger.exception("[quota] 사용량 기록 실패 — 막는다")
        return JSONResponse({"detail": "잠시 후 다시 시도해 주세요.", "quota": "error"}, status_code=503)
    if why:
        logger.warning(f"[quota] {why} 상한 — {request.method} {path} ip={ip}")
        return JSONResponse({"detail": MESSAGES[why], "quota": why}, status_code=429)
    resp = await call_next(request)
    if 400 <= resp.status_code < 500:
        try:
            await run_in_threadpool(refund, ip, usd, day)
        except sqlite3.Error:
            logger.exception("[quota] 환불 기록 실패 — 무시 (상한 쪽으로만 어긋난다)")
    return resp
