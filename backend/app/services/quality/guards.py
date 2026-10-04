"""출력 가드 - 생성 결과를 결정론적·저비용 신호(임베딩 유사도, 로컬 OCR)로 재는 층.
judge()(fidelity/realism/trust)는 VLM 의 "판단"이라 프롬프트/모델 버전에 따라 흔들리는
반면, 여기는 값으로 남는 신호다.

지금은 전부 soft(관측용) — 결과를 막지 않는다. VLM 으로 결과 글자를 다시 읽어 비교하던
hard 가드(ocr_match)는 반려의 대부분이 읽기 흔들림이라 09-27 에 없앴다. 글자 보존은
TEXT_LOCK(생성 전 부탁) + verify(주요 글자 "보이나")가 맡는다.

관측용이라 계산 실패는 None 으로 삼킨다 — 이미 비용 든 생성을 신호 하나 때문에 되돌리지 않는다.
"""
import logging
from dataclasses import dataclass

from app.core.tracing import score
from app.services.ai import embedder
from app.services.quality import metric

logger = logging.getLogger("carret.guards")

OCR_MATCH_THRESHOLD = 0.95   # 로컬 OCR(ocr_local) recall 기준 — 관측용
DINO_BAND = (0.75, 0.995)
# 누끼 딴 물건끼리의 DINO 유사도 하한. 경험값 없음 — soft 로 값만 모으고 eval 로 정한다.
ITEM_DINO_THRESHOLD = 0.80
# 누끼 쌍의 DINO 패치 유사도(물건 안쪽 하위 1%) 하한. 합성 테스트(주전자 누끼 1장, 2026-09-26):
# 조명 변화·이동 0.975~0.988, 흠집 한 줄 추가 0.92 — 실제 생성본 분포는 모른다. soft 로 값만 모은다.
ITEM_PATCH_THRESHOLD = 0.95


@dataclass
class GuardResult:
    name: str
    passed: bool
    value: float
    threshold: float
    severity: str  # "hard" | "soft"


def dino_band_guard(orig: bytes, result: bytes) -> GuardResult | None:
    """dino_band — 이미지 전체 DINO 유사도 (soft). 계산 실패(모델 로드/메모리)는 None —
    이미 비용 든 변환을 관측 신호 하나 때문에 500 으로 날리지 않는다."""
    lo, hi = DINO_BAND
    try:
        dino = embedder.cosine_similarity(orig, result)
    except Exception as e:
        logger.warning(f"[guards] dino_band 계산 실패(soft, 무시): {e}")
        return None
    g = GuardResult(name="dino_band", passed=lo <= dino <= hi,
                    value=dino, threshold=lo, severity="soft")
    score(g.name, g.value, data_type="NUMERIC")   # 키 없으면 noop (tracing.py 컨벤션)
    return g


def item_guard(pair: tuple[bytes, bytes] | None) -> GuardResult | None:
    """item_dino — 원본·결과에서 물건만 오려(compositor.isolate) 비교한 DINO 유사도. soft(관측용).

    물건이 통째로 바뀌거나 형태·색·무늬가 달라진 걸 잡는다. 작은 하자(흠집 하나)는 임베딩을
    거의 못 움직여 여기서 못 잡는다 — 그건 verify(Wear Gate) 몫.
    pair 가 없거나(오리기 실패) 계산이 실패하면 None — 이미 비용 든 생성을 이것 때문에 막지 않는다."""
    if pair is None:
        return None
    try:
        sim = embedder.cosine_similarity(*pair, name="item_dino_similarity")
    except Exception as e:
        logger.warning(f"[guards] item_dino 계산 실패(soft, 무시): {e}")
        return None
    g = GuardResult(name="item_dino", passed=sim >= ITEM_DINO_THRESHOLD,
                    value=sim, threshold=ITEM_DINO_THRESHOLD, severity="soft")
    score(g.name, g.value, data_type="NUMERIC")
    return g


def item_patch_guard(pair: tuple[bytes, bytes] | None) -> GuardResult | None:
    """item_patch — 같은 누끼 쌍을 DINO 패치 단위로 비교한 국소 유사도 (embedder.patch_similarity).
    soft(관측용). item_dino(CLS 하나)가 못 보는 "한 군데만 바뀜"(흠집·글자 지워짐)을 보려는 값.
    실패하면 None — item_guard 와 같은 이유."""
    if pair is None:
        return None
    try:
        sim = embedder.patch_similarity(*pair)
    except Exception as e:
        logger.warning(f"[guards] item_patch 계산 실패(soft, 무시): {e}")
        return None
    g = GuardResult(name="item_patch", passed=sim >= ITEM_PATCH_THRESHOLD,
                    value=sim, threshold=ITEM_PATCH_THRESHOLD, severity="soft")
    score(g.name, g.value, data_type="NUMERIC")
    return g


def local_ocr_guard(before: list[str], after: list[str]) -> GuardResult | None:
    """ocr_local — 로컬 OCR(EasyOCR)로 읽은 원본·결과 글자의 줄 단위 recall. soft(관측용).
    원본에서 읽은 게 없으면 None."""
    if not before:
        return None
    recall = metric.text_match(before, after)["recall"]
    g = GuardResult(name="ocr_local", passed=recall >= OCR_MATCH_THRESHOLD,
                    value=recall, threshold=OCR_MATCH_THRESHOLD, severity="soft")
    score(g.name, g.value, data_type="NUMERIC")
    return g
