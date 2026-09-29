"""서버 시작 뒤 백그라운드에서 로컬 모델을 미리 올린다.

첫 요청이 모델 로딩을 기다리지 않게 — 09-29 Langfuse: DINO 첫 호출 19~23초(구도 확인 단계에
붙는다), rembg 첫 로딩은 첫 배경 교체 55초. 서버 시작은 막지 않고(데몬 스레드), 실패는 삼킨다
(요청 때 _load 가 다시 시도). rembg 는 오리기 백엔드가 local 일 때만 — fal(기본)이면 fal 실패 때의
대체로만 쓰여서 메모리를 미리 잡을 이유가 없다.
"""
import logging
import threading
import time

from app.core.config import settings

logger = logging.getLogger("carret.warmup")


def warm_models() -> list[str]:
    """올린 모델 이름 목록을 돌려준다."""
    from app.services.ai import compositor, embedder
    targets = [("dino", embedder._load)]
    if settings.cutout_backend != "fal":
        targets.append(("rembg", compositor._load))
    done = []
    for name, load in targets:
        t0 = time.time()
        try:
            load()
            done.append(name)
            logger.info(f"[warmup] {name} {time.time() - t0:.1f}s")
        except Exception as e:
            logger.warning(f"[warmup] {name} 실패(요청 때 다시 시도): {e}")
    return done


def start_warmup() -> threading.Thread | None:
    if not settings.warmup_models or settings.pipeline_mode != "real":
        return None
    th = threading.Thread(target=warm_models, name="warmup", daemon=True)
    th.start()
    return th
