"""원본 읽기 · 분석(analyze) · 글자 읽기 · 게시글 구성품 목록 캐시."""
import logging
import json
from app.core.config import settings
from app.prompts.presets import get_preset
from app.services.ai import detector
from app.services.persistence import storage

from app.services.pipeline.state import ANALYZE_ATTEMPTS, State, _with_retry
from app.services.pipeline.selection import apply_selection

logger = logging.getLogger("carret.pipeline")


# ── 노드 ─────────────────────────────────────────
def load(s: State) -> dict:
    original = storage.load_original(s["file_id"])
    if original is None:
        raise FileNotFoundError(s["file_id"])
    return {
        "original": original,
        "preset": get_preset(s["preset_key"], note=s.get("style_note") or ""),
    }


def read_text(s: State) -> dict:
    """원본 물건 위 글자 읽기 — generate 프롬프트에 넣어 글자 뭉개짐을 줄인다.
    analyze 가 text_level=simple 이라고 본 물건만 온다 (none 은 읽을 게 없고, dense 는 이미
    배경 교체로 갔다). 관측 단계와 같은 실패 정책: 실패하면 글자 없이 그대로 생성한다.
    물건 위치(item_box)는 analyze 가 준 걸 우선 — 없을 때만 여기 값을 쓴다."""
    box = s.get("item_box")
    if not settings.text_lock:
        return {"item_texts": [], "item_box": box}
    if isinstance(s.get("item_texts"), list):
        # analyze(v2)가 이미 읽었다 — VLM 을 또 부르지 않는다 (10-01: 글자 읽기 호출이 VLM 비용의 21%,
        # 따로 부르면 응답이 깨져도 조용히 "글자 없음"이 됐다 — analyze 는 깨지면 재시도 · detect_failed)
        out = {"texts": s["item_texts"], "item_box": None}
    else:
        try:   # 옛 analyze 결과(texts 없음, 저장해 둔 것)만 여기로
            out = detector.read_item_text(s["original"], s.get("item", "object"))
        except Exception as e:
            logger.warning(f"[read_text] 실패(무시): {e}")
            return {"item_texts": [], "item_box": box}
    upd = {"item_texts": out["texts"], "item_box": box or out.get("item_box")}
    # analyze 가 simple 이라고 했어도 막상 읽어 보니 잔글씨가 많으면 — 생성 전 배경 교체 (안전망).
    # dev 그래프(provided_result)는 배경 교체로 가지 않는다 (inspect 에 엉뚱한 사유가 남지 않게).
    # 생성 전 배경 교체가 이미 실패하고 온 길(composite_error)이면 다시 보내지 않는다 (무한 왕복 방지).
    n = settings.composite_first_min_texts
    if (n and len(out["texts"]) >= n and s.get("provided_result") is None
            and not s.get("composite_error")):
        logger.info(f"[read_text] 글자 {len(out['texts'])}줄 → 생성 전 배경 교체 모드로: text_heavy")
        upd["composite_reason"] = "text_heavy"
    return upd


def _analysis_name(file_id: str) -> str:
    return f"{file_id}_analysis.json"


def _overlay_components(file_id: str, a: dict | None) -> dict | None:
    """분석의 objects 는 저장된 게시글 구성품 목록이 있으면 늘 그걸로 (10-09 리뷰: 한 곳만 진실).
    두 호출이 동시에 저장해도 읽을 때 맞추니 순서 경합이 없다."""
    if not a or a.get("detect_failed"):
        return a
    comps = load_components(file_id)
    if isinstance(comps, dict) and isinstance(comps.get("objects"), list):
        return {**a, "objects": comps["objects"], "contents_hidden": comps.get("contents_hidden", False)}
    return a


def load_analysis(file_id: str) -> dict | None:
    """저장해 둔 analyze 결과 — 업로드 직후 미리 분석(/api/analyze)했거나 같은 사진을 다른 무드로
    다시 변환할 때 VLM 을 또 부르지 않게. 원본은 file_id 마다 바뀌지 않는다. 깨졌으면 None."""
    try:
        data = storage.load("quality", _analysis_name(file_id))
        a = json.loads(data) if data else None
    except Exception:
        return None
    return _overlay_components(file_id, a)


def _components_name(file_id: str) -> str:
    return f"{file_id}_components.json"


def load_components(file_id: str) -> dict | None:
    try:
        data = storage.load("quality", _components_name(file_id))
        return json.loads(data) if data else None
    except Exception:
        return None


def components_for(file_id: str, original: bytes, extras: list[tuple[str, bytes]] | None = None) -> dict:
    """게시글 구성품 목록 (10-09) — 기준 사진 + 같이 넣는 참고 사진을 같이 본 하나의 목록.
    판매자가 고르는 번호(sell)와 세트 프롬프트 · 개수 게이트가 모두 이 목록을 쓴다. 같은 참고 사진 조합이면
    저장해 둔 걸 쓰고, 조합이 바뀌면 다시 본다. 전체 분석이 이미 저장돼 있으면 그 objects 도 이 목록으로 맞춘다.
    반환: {"objects", "contents_hidden", "extras"}."""
    ids = [fid for fid, _ in extras or []]
    cached = load_components(file_id)
    if isinstance(cached, dict) and cached.get("extras") == ids:
        return cached
    got = detector.components([original, *(b for _, b in extras or [])])
    out = {**got, "extras": ids}
    # 분석 파일은 건드리지 않는다 — load_analysis 가 읽을 때 이 목록을 덮는다 (경합 없음)
    try:   # 저장 실패로 변환이 깨지지 않게 (10-09 리뷰)
        storage.save("quality", _components_name(file_id), json.dumps(out, ensure_ascii=False).encode("utf-8"))
    except Exception as e:
        logger.warning(f"[components] 저장 실패(무시): {e}")
    return out


def analyze_original(file_id: str, original: bytes) -> dict:
    """analyze VLM (재시도 포함) → 결과. 성공하면 저장해 둔다. 실패는 detect_failed=True (저장 안 함)."""
    try:
        out = {**_with_retry(lambda: detector.analyze(original), "analyze", ANALYZE_ATTEMPTS),
               "detect_failed": False}
    except Exception:
        return {"item": "object", "considered": [], "anchors": [], "detect_failed": True,
                "text_level": None, "photo_type": None, "wear_level": None, "watermark": None}
    try:
        storage.save("quality", _analysis_name(file_id),
                     json.dumps(out, ensure_ascii=False).encode("utf-8"))
    except Exception as e:
        logger.warning(f"[analyze] 결과 저장 실패(무시): {e}")
    # 화면이 본 게시글 구성품 목록이 있으면 그걸로 — 판매자가 고른 번호가 같은 물건을 가리키게 (10-09)
    return _overlay_components(file_id, out)


def analyze(s: State) -> dict:
    """원본 분석 (VLM 1회) — 물건·아이덴티티 마크·사진 유형·하자 수준·워터마크·글자 수준·위치.
    저장해 둔 결과가 있으면 그걸 쓴다 (load_analysis).

    실패는 "지킬 것 없음"과 다르다 — 빈 앵커로 넘기면 verify 가 검사할 게 없다며
    게이트를 통과시킨다. 재시도까지 실패하면 detect_failed 로 남겨 plan 이 배경 교체로 보낸다
    (생성 결과를 확인 없이 내보내지 않음)."""
    cached = load_analysis(s["file_id"])
    fresh = cached is None or cached.get("detect_failed")
    out = analyze_original(s["file_id"], s["original"]) if fresh else cached
    # 고른 번호는 사용자가 본 분석(저장된 것)의 번호다 — 지금 새로 분석했다면 목록 순서가 다를 수 있어 쓰지 않는다
    sell = None if fresh else s.get("sell")
    if fresh and s.get("sell") is not None:
        logger.info("[analyze] 저장된 분석이 없어 고른 물건 번호를 쓰지 않음 (분석 판단대로)")
    return apply_selection(out, sell, s.get("answer_count"), s.get("mains") if sell is not None else None)
