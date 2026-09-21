"""URL에서 이미지 다운로드 + 검증 — images.py(/upload-url)와 dev.py(/run-inbox) 공유.

로직 중복 금지: 둘 다 "http(s)인지 → 사설망 아닌지 → 다운로드 → 크기 → 진짜
이미지인지" 를 똑같이 검사해야 하므로 한 곳에만 둔다.

SSRF 방지: 호스트명이 아니라 실제로 resolve된 IP가 사설/루프백/링크로컬/예약/
멀티캐스트 대역이면 거부한다 (클라우드 메타데이터 169.254.169.254 포함).
리다이렉트는 자동으로 따라가지 않고 매 홉마다 같은 검사를 다시 거친다 —
리다이렉트 한 번으로 검사를 우회(공인 URL → 내부 주소)하는 걸 막기 위함."""
import io
import ipaddress
import socket
from urllib.parse import urljoin, urlparse

import httpx
from fastapi import HTTPException
from PIL import Image
from starlette.concurrency import run_in_threadpool

from app.core.config import settings

_MAX_REDIRECTS = 5


def _is_public_host(host: str) -> bool:
    """host가 가리키는 모든 IP가 공인 대역인지 (DNS 리바인딩 방지 위해 매번 새로 resolve)."""
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    for *_, sockaddr in infos:
        ip = ipaddress.ip_address(sockaddr[0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            return False
    return True


def _assert_public_url(url: str) -> None:
    if not url.startswith(("http://", "https://")):
        raise HTTPException(400, "http(s) URL만 가능")
    host = urlparse(url).hostname
    if not host or not _is_public_host(host):
        raise HTTPException(400, "허용되지 않는 주소")


async def fetch_image(url: str) -> tuple[bytes, str]:
    """(bytes, ext) 반환. ext 는 점 없이 소문자(예: "jpeg", "png")."""
    current = url
    async with httpx.AsyncClient(timeout=30, follow_redirects=False) as c:
        for _ in range(_MAX_REDIRECTS):
            await run_in_threadpool(_assert_public_url, current)   # DNS 조회는 블로킹 I/O
            r = await c.get(current)
            if not r.is_redirect:
                break
            current = urljoin(current, r.headers.get("location", ""))
        else:
            raise HTTPException(400, "리다이렉트가 너무 많습니다")

    if r.status_code != 200:
        raise HTTPException(400, f"다운로드 실패: {r.status_code}")
    if len(r.content) > settings.max_bytes:
        raise HTTPException(413, "이미지가 너무 큽니다")

    try:
        img = Image.open(io.BytesIO(r.content))
        ext = (img.format or "JPEG").lower()
    except Exception:
        raise HTTPException(400, "이미지가 아닙니다")
    return r.content, ext
