"""생성 뒤 검사 — 사진 성립(check_photo) · 관측 신호(DINO · OCR) · 마크 보존 · 개수 · 재생성 표시."""
import logging
import re
import time
from dataclasses import asdict
from app.core.config import settings
from app.core.tracing import score
from app.prompts.presets import TEXT_LOCK_MAX_CHARS, prompt_safe, unreadable, text_where
from app.services.ai import compositor, detector, embedder
from app.services.persistence import storage
from app.services.quality import guards

from app.services.pipeline.state import (
    LOCAL_OCR_WAIT_S,
    State,
    VERIFY_ATTEMPTS,
    _OCR_POOL,
    _in_background,
    _with_retry,
)
from app.services.pipeline.selection import _pieces
from app.services.pipeline.generation import _extra_views

logger = logging.getLogger("carret.pipeline")


def _item_pair(s: State) -> tuple[bytes, bytes] | None:
    """item_dino 가드 입력 — 원본·결과에서 물건만 오려 같은 배경·크기에 놓은 쌍.
    원본 오리기는 캐시돼 재생성마다 다시 하지 않는다. 오리기 실패는 None — soft 가드라
    이미 비용 든 생성을 이것 때문에 막지 않는다."""
    try:
        return (compositor.isolate(s["original"], s.get("item_box"), original=True),
                compositor.isolate(s["result"]))
    except Exception as e:
        logger.warning(f"[guards] 물건 오리기 실패(item_dino 생략): {e}")
        return None


def _dino_band(s: State) -> tuple[list, float | None]:
    """이미지 전체 DINO (dino_band, soft) → (기준 밖이면 기록, 유사도). 유사도는
    score_similarity 가 다시 계산하지 않게 넘긴다."""
    g = guards.dino_band_guard(s["original"], s["result"])
    if g is None:
        return [], None
    return ([] if g.passed else [asdict(g)]), g.value


def _item_signals(s: State) -> tuple:
    """누끼 쌍 가드 item_dino·item_patch — 오리기(rembg) + DINO 추론(패치는 448 해상도 2회)."""
    pair = _item_pair(s)
    return guards.item_guard(pair), guards.item_patch_guard(pair)


def _remaining(deadline: float) -> float:
    return max(0.0, deadline - time.monotonic())


def _local_ocr_lines(s: State) -> tuple[list[str], list[str]]:
    """ocr_local 가드 입력 — 원본은 물건 영역(item_box)만, 결과는 전체 (생성본의 물건 위치는
    원본과 다르고, 프리셋 배경엔 글자가 없다). EasyOCR 조각(단어 단위) 목록이라 read_text(VLM)의
    줄 목록과 단위가 다르다 — 관측용 값이다."""
    from app.services.ai import local_ocr
    return (local_ocr.read_original(s["original"], s.get("item_box")),
            local_ocr.read_lines(s["result"]))


def _local_ocr_check(fut, deadline: float) -> tuple[dict | None, float | None]:
    """ocr_local 결과 (실패했으면 가드 기록, recall). 꺼져 있거나 늦거나 실패하면 (None, None)."""
    if fut is None:
        return None, None
    try:
        g = guards.local_ocr_guard(*fut.result(timeout=_remaining(deadline)))
    except Exception as e:
        fut.cancel()
        logger.warning(f"[guards] 로컬 OCR 실패(ocr_local 생략): {e!r}")
        return None, None
    if g is None:
        return None, None
    return (None if g.passed else asdict(g)), g.value


# FastAPI 스레드풀(기본 40)과 비슷한 크기 — 작으면 동시 요청이 몰릴 때 check_photo 가
# 큐에서 기다려 병렬화가 오히려 직렬보다 느려진다.
def validate_result(s: State) -> dict:
    """결과가 '제대로 된 사진'인지 VLM 으로 확인 (구도가 잘렸거나 자막/텍스트가 상품을 가리면
    invalid) — 라우팅(_route_after_validate)이 보고 generate 로 되돌릴지 정한다.
    기다리는 동안 관측 신호(dino_band, 켜져 있으면 로컬 OCR)를 같이 잰다 — 전부 soft 라
    결과를 막지 않는다. 누끼 비교는 그래프 밖(item_signals_and_save)."""
    photo = _in_background(detector.check_photo, s["result"])   # 스스로 예외를 삼킨다
    t0 = time.monotonic()
    # 로컬 OCR — 원본에 글자가 있을 때만 (없으면 비교할 게 없다)
    ocr = (_in_background(_local_ocr_lines, s, pool=_OCR_POOL)
           if settings.local_ocr_guard and s.get("item_texts") else None)
    try:
        report, dino = _dino_band(s)
        storage.save("result", s["result_name"], s["result"])
    except BaseException:
        for f in (photo, ocr):
            if f is not None:
                f.cancel()
        raise
    try:
        # 상한: 큐 대기 + VLM 타임아웃. 넘기면 구도 검사를 못 한 것 — check_photo 의
        # 기존 실패 정책(개방형, valid=True)과 같게.
        check = photo.result(timeout=settings.vlm_timeout_s + 10)
    except Exception as e:
        logger.warning(f"[validate_result] check_photo 대기 실패(무시): {e}")
        check = {"valid": True, "reason": ""}
    ocr_fail, ocr_recall = _local_ocr_check(ocr, t0 + LOCAL_OCR_WAIT_S)
    report = report + ([ocr_fail] if ocr_fail else [])
    return {"photo_check": check, "guard_report": report, "visual_similarity": dino,
            "ocr_local_recall": ocr_recall}


def _route_after_validate(s: State) -> str:
    check = s.get("photo_check") or {"valid": True}
    if check.get("valid", True):
        return "ok"
    if s.get("gen_attempts", 0) >= settings.max_generate_attempts:
        logger.info(f"[validate_result] 재생성 한도 소진, 마지막 결과로 진행: "
              f"{check.get('reason')}")
        return "ok"
    logger.info(f"[validate_result] invalid, 재생성 ({s.get('gen_attempts', 0)}/"
          f"{settings.max_generate_attempts}): {check.get('reason')}")
    return "retry"


def score_similarity(s: State) -> dict:
    """원본 vs 결과 DINOv2 코사인 유사도 — VLM judge와 별개인 로컬 벡터 점수."""
    if s.get("visual_similarity") is not None:
        # validate_result 의 dino_band 가드가 같은 이미지로 이미 계산함 — 점수만 부착
        score("visual_similarity", s["visual_similarity"], data_type="NUMERIC")
        return {}
    try:
        sim = embedder.cosine_similarity(s["original"], s["result"])
    except Exception as e:
        logger.warning(f"[score_similarity] 실패(무시): {e}")
        return {"visual_similarity": None}
    score("visual_similarity", sim, data_type="NUMERIC")
    return {"visual_similarity": sim}


TEXT_VERIFY_MAX = 8   # 글자 사후검증 항목 상한 — 많을수록 VLM 오판 1건에 게이트가 흔들린다


def _covered_by(key: str, covered: str) -> bool:
    """detect print 앵커 문장에 이 글자가 이미 들어 있나 — 단어 단위로만, 3글자 이상만.
    ("on" 이 "print on chest" 에 걸려 검증에서 빠지는 식의 오탐 방지)"""
    if not covered or len(key) < 3:
        return False
    return re.search(rf"(?<!\w){re.escape(key)}(?!\w)", covered) is not None


def _key_texts(item_texts: list, *, covered: str = "") -> list[tuple[str, str]]:
    """사후검증(verify 체크리스트)에 걸 글자 고르기 → [(글자, 대략 위치)].
    글자 하나하나가 통과 조건이 되므로 읽기 오차에 덜 흔들리는 것만 고른다:
    - 큰 글자(박스 면적순) 위주로 TEXT_VERIFY_MAX 개까지 — 성분표 같은 잔글씨는 제외
    - prompt_safe 가 잘라낸 긴 문장·1글자는 제외 (끊긴 문장·한 글자는 오판이 잦다)
    - covered(이미 다른 항목이 다루는 글자)에 들어 있으면 제외
    - 원본에서도 못 읽은 글자("?" 포함 — 읽기 프롬프트가 못 읽는 글자를 ? 로 쓰게 한다)는 제외:
      생성본이 "?" 와 일치할 수 없어 멀쩡한 결과도 반드시 떨어진다 (번호판 "17? 5433", 09-27)"""
    def area(t):
        if not all(k in t for k in ("x1", "y1", "x2", "y2")):
            return 0
        return (t["x2"] - t["x1"]) * (t["y2"] - t["y1"])

    out, seen = [], set()
    for t in sorted(item_texts or [], key=area, reverse=True):
        if len(out) >= TEXT_VERIFY_MAX:
            break
        raw = str(t.get("text", "")).strip()
        text = prompt_safe(raw)
        key, where = text.lower(), text_where(t)
        if (len(text) < 2 or len(raw) > TEXT_LOCK_MAX_CHARS or unreadable(raw)
                or (key, where) in seen or _covered_by(key, covered)):
            continue
        seen.add((key, where))
        out.append((text, where))
    return out


def _verify_targets(s: State) -> list:
    """verify 체크리스트 = detect 앵커 + read_text 가 읽은 글자 (_key_texts 로 고름).

    TEXT_LOCK 은 생성 전에 "글자를 지켜라"고 부탁만 한다 — 실제로 지켜졌는지는
    여기서 게이트에 건다 (뭉개지면 preserved:false → 재생성/배경 교체).
    detect 가 같은 글자를 print 앵커로 이미 올렸으면 중복으로 넣지 않는다."""
    # 부가품 위 마크는 보지 않는다 (썸네일에서 뺐다). 본구성품의 작은 인쇄(설명서 번호 등)는 "있는지만" (10-05 B)
    targets = [{**a, "loose": True} if a.get("on") == "component" and a.get("size") == "small" else a
               for a in s.get("anchors") or [] if a.get("on") != "accessory"]
    covered = " ".join(a.get("what", "") for a in targets
                       if a.get("category") == "print").lower()
    if not settings.text_gate:
        # 글자는 검사하지 않는다 — 로고 · 마크만 (10-11 사용자). 글자는 상세(검증) 사진으로 보강
        return [a for a in targets if not (a.get("category") == "print" and TEXT_ANCHOR.search(a.get("what") or ""))]
    targets += [{"category": "print", "what": f'text: "{text}"',
                 "where": where or "on the item"}
                for text, where in _key_texts(s.get("item_texts"), covered=covered)]
    return targets


# 글자 앵커 (로고가 아니라 글귀 · 숫자 · 제목) — text_gate 가 꺼지면 검사에서 뺀다
# label · number 는 뺐다 — "brand label logo", "model number plate" 같은 마크까지 검사에서 빠졌다 (10-11 리뷰)
TEXT_ANCHOR = re.compile(r"\b(text|lettering|letters|title|writing|caption|words?)\b|글자|문구", re.I)


def detail_requests(s: State) -> list[str]:
    """생성 결과에서 글자를 검사하지 않을 때, 판매자에게 따로 받을 검증 사진 (10-11)."""
    if settings.text_gate:
        return []
    has_text = (s.get("text_level") or "none") != "none" or any(
        a.get("category") == "print" and TEXT_ANCHOR.search(a.get("what") or "") for a in s.get("anchors") or [])
    return ["글자 · 라벨 · 인증 스티커를 가까이 찍은 사진 (검증용 — 대표사진의 글자는 AI 가 다시 그려 정확하지 않을 수 있어요)"] \
        if has_text else []


def verify(s: State) -> dict:
    out = _verify_marks(s)
    # 세트 프롬프트(앱 기본, 10-09)면 개수 게이트도 같이
    if ((settings.count_gate or s.get("clean_prompt") == "set") and s.get("mode", "generate") == "generate"
            and out.get("gate_passed") is not False and _pieces(s, s.get("set_views", 0))):
        out = {**out, **_count_gate(s, _pieces(s, s.get("set_views", 0)))}
    return out


def _count_gate(s: State, pieces: list) -> dict:
    """세트 구성품 개수 게이트 (settings.count_gate, 10-09) — 생성본에서 종류별로 다시 세어 원본과 다르면 탈락.
    setjson-flash 2차가 설명서 3권으로 늘었는데 마크 · 글자 게이트를 통과했다. 호출이 실패해도 막는다 (10-10)."""
    kinds = [k for k, _ in pieces]
    try:
        got = _with_retry(lambda: detector.count_pieces(
            s["original"], storage.load("result", s["result_name"]), kinds), "count_pieces", VERIFY_ATTEMPTS)
    except Exception as e:
        # 검사를 못 했으면 통과가 아니다 (10-10: 504 로 못 셌는데 카드 복제 · 스티커 지어낸 결과가 채택됐다)
        logger.warning(f"[count_gate] 확인 못 함 — 통과시키지 않음: {e}")
        return {"count_mismatch": [{"what": "(count check failed)", "want": None, "got": None}], "gate_passed": False}
    diff = [{"what": k, "want": n, "got": g} for (k, n), g in zip(pieces, got) if g != n]
    logger.info(f"[count_gate] {dict(zip(kinds, got))} · 다름 {diff}")
    return {"count_mismatch": diff, **({"gate_passed": False} if diff else {})}


def _verify_marks(s: State) -> dict:
    checks, gate_passed = [], None
    if s.get("mode", "generate") != "generate":
        # 배경 교체본은 물건 픽셀이 원본이다 — 확인해도 VLM 오판으로 거짓 경고만 붙을 수 있고
        # 결과를 바꾸지도 않는다 (VLM 1회 절약, 09-27). verify_failed 는 건드리지 않는다 —
        # "verify 호출 실패 → 배경 교체"면 그 사실이 배지·inspect 에 남아야 한다.
        # 생성본 게이트에서 떨어진 checks(무엇이 사라졌나)는 gate_checks 로 옮겨 남긴다
        # (checks 는 말풍선 좌표라 생성본 기준 — 합성본 위에 그리면 안 된다)
        out = {"checks": checks, "gate_passed": None, "added_text": []}
        if s.get("checks"):
            out["gate_checks"] = s["checks"]
        if s.get("added_text"):
            out["gate_added_text"] = s["added_text"]   # 생성본에 생겼던 글자 — 합성본 기준이 아님
        return out
    if s.get("detect_failed"):
        # 원본 마크 목록이 없으니 보존 여부를 확인할 수 없다 — 생성본은 통과로 치지 않는다
        return {"checks": checks, "gate_passed": False, "verify_failed": False}
    targets = _verify_targets(s)
    saved = storage.load("result", s["result_name"])
    if targets and settings.verify_combined:
        return _verify_combined(s, saved, targets)
    # 없던 글자 검사는 마크 검사와 서로 기다릴 이유가 없다 — 같이 돌려 VLM 왕복 한 번만큼 줄인다
    added_fut = _in_background(_added_text, s, saved)
    if not targets:
        added = _added_result(added_fut)
        return {"checks": checks, "verify_failed": False, "added_text": added,
                "gate_passed": False if added else gate_passed}
    try:
        checks = _with_retry(lambda: detector.verify_and_locate(
            saved, targets, s.get("item", "object"), strict=True), "verify", VERIFY_ATTEMPTS)
    except Exception:
        # 호출 실패(타임아웃·429·장애)는 "보존됨"이 아니다 — 통과로 치지 않는다 (→ 배경 교체).
        # 결과가 이미 실패라 없던 글자 검사를 기다리지 않는다 (장애 때 배경 교체가 늦어지지 않게)
        added_fut.cancel()
        return {"checks": [], "verify_failed": True, "gate_passed": False, "added_text": []}
    added = _added_result(added_fut)
    return {"checks": checks, "verify_failed": False, "added_text": added,
            "gate_passed": detector.all_preserved(checks, expected=len(targets)) and not added}


def _verify_combined(s: State, saved: bytes, targets: list) -> dict:
    """verify + added_text 한 호출 (settings.verify_combined, 10-05). 실패 처리는 따로 부를 때와 같다 —
    마크 판정을 못 받으면 verify_failed, 없던 글자 부분만 깨지면 막지 않는다."""
    try:
        checks, added = _with_retry(lambda: detector.verify_combined(
            s["original"], saved, targets, s.get("item", "object")), "verify_combined", VERIFY_ATTEMPTS)
    except Exception:
        return {"checks": [], "verify_failed": True, "gate_passed": False, "added_text": []}
    if not settings.text_gate:
        added = []   # 없던 글자도 보지 않는다 (10-11) — checks 만
    if added is None and settings.added_text_gate:
        # checks 만 성하면 재시도하지 않는다 — 없던 글자는 따로 부를 때처럼 "확인 못 함 = 막지 않음"
        logger.warning("[verify_combined] added 목록 없음 — 없던 글자 확인 못 함, 막지 않음")
    added = (added or []) if settings.added_text_gate else []
    _log_added(added)
    return {"checks": checks, "verify_failed": False, "added_text": added,
            "gate_passed": detector.all_preserved(checks, expected=len(targets)) and not added}


def _added_result(fut) -> list:
    """덧붙인 검사라 호출이 안 돼도(None) 막지 않는다 — 마크 검사(verify)는 그대로 한다 (10-03 리뷰)."""
    try:
        added = fut.result(timeout=(settings.vlm_timeout_s + 10) * VERIFY_ATTEMPTS)
    except Exception as e:
        fut.cancel()
        logger.warning(f"[added_text] 대기 실패 — 막지 않음: {e}")
        return []
    return added or []


def _added_text(s: State, result: bytes) -> list | None:
    """생성본에 새로 생긴 글자 · 로고 (설정으로 끄면 빈 목록). 호출이 끝내 실패하면 None — "확인 못 함"."""
    if not settings.added_text_gate or not settings.text_gate:
        return []
    try:
        others = _extra_views(s)
        added = _with_retry(lambda: (detector.added_text(s["original"], result, others) if others
                                     else detector.added_text(s["original"], result)), "added_text", VERIFY_ATTEMPTS)
    except Exception:
        return None
    _log_added(added)
    return added


def _log_added(added: list) -> None:
    if added:
        logger.info(f"[verify] 없던 글자 · 로고: {[a['what'] for a in added]}")


def _route_after_verify(s: State) -> str:
    """게이트 실패 → (1) 사라지거나 바뀐 마크를 알려주고 1회 재생성 → (2) 그래도 실패면
    원본 물건 픽셀을 그대로 쓰는 배경 교체 모드. 생성 모델이 로고·글자를
    지키지 못하는 경우의 정직성 폴백."""
    if s.get("gate_passed") is not False or s.get("mode", "generate") != "generate":
        return "done"
    if s.get("user_mode") == "generate":
        # 생성만 (10-11) — 검사에서 떨어져도 배경 교체로 가지 않는다. 1회 재생성 뒤엔 마지막 결과 + 탈락 기록
        if s.get("gate_retried") or s.get("detect_failed") or s.get("verify_failed"):
            return "done"
        return "regen"
    if s.get("detect_failed"):
        return "composite"   # 재생성해도 확인할 기준이 없다 (앞선 오리기 실패 후 생성한 경우)
    if s.get("verify_failed"):
        return "composite"   # 확인 자체를 못 했다 — 재생성해도 또 확인 못 할 가능성이 크다
    if not s.get("gate_retried"):
        return "regen"
    return "composite"


def mark_gate_retry(s: State) -> dict:
    # 검사 목록 표시([presence only])는 생성 프롬프트에 넣지 않는다 — 글자로 그려졌다 (10-09 qwen3-1009)
    lost = [prompt_safe(c["what"].replace("[presence only]", "").strip())
            for c in s.get("checks", []) if not c.get("preserved")]
    note = ("\n\nIMPORTANT: a previous attempt lost or altered these marks on the "
            "product: " + "; ".join(f'"{w}"' for w in lost if w) +
            ". They MUST remain exactly as in the input image.") if lost else ""
    added = [a["what"] for a in s.get("added_text") or []]
    if added:
        # 생긴 글자를 그대로 적지 않는다 — 단어를 쓰면 그걸 다시 그린다 (10-03)
        note += ("\n\nIMPORTANT: a previous attempt drew marks on the product that are not in the input "
                 "image. Every surface of the product must look exactly as in the input image.")
    if s.get("count_mismatch"):
        logger.warning(f"[gate] 구성품 개수 다름: {s['count_mismatch']}")
    logger.warning(f"[gate] 실패 → 1회 재생성: 사라짐 {lost} · 새로 생김 {added}")
    return {"gate_retried": True, "gate_note": note, "gate_added_text": s.get("added_text") or [],
            "photo_check": None, "count_mismatch": []}
