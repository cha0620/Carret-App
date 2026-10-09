"""스타일 참고(정답 사진) 고르기 (settings.style_ref, 10-05 실험).

style_refs.json: [{"file", "category", "view": [각도], "prep": [준비물 — 자유 영어 문장], "parts": [화면용 라벨],
                   "items": [물건 이름 키워드], "note"}]
생성 모델엔 사진이 아니라 선 그림(refs/sketch/, eval/style_sketch.py)을 넣는다 — 사진을 주면 생김새 · 글자까지
베낀다 (study 10-04 §16). 이미지는 같은 폴더의 refs/ (남의 상품 사진이라 git 밖).

고르는 규칙: 같은 종류 → 주 사진과 같은 각도 하나(앞 · 뒤 두 면을 세운 정답은 사진 한 장으로 못 따라 해 안 고름)
→ 정답에 나온 준비물(상자 · 완성품 · 피규어 …)이 그 사진 한 장에 다 보이나 (VLM 대조 — 이름 목록을 정해 두지
않는다, 10-05 사용자 결정). 하나도 안 맞으면 None — 틀린 선 그림보다 없는 게 낫다.
"""
import copy
import json
import logging
from pathlib import Path

from app.core.config import settings

logger = logging.getLogger("carret.style_refs")
BACKEND = Path(__file__).resolve().parents[2]


def _path() -> Path:
    p = Path(settings.style_ref_file)
    return p if p.is_absolute() else BACKEND / p


_cache: dict = {}


def load() -> list[dict]:
    """정답 목록 — 파일이 바뀔 때만 다시 읽는다 (화면 목록 · 선 그림 요청마다 읽지 않게, 10-09 리뷰)."""
    try:
        p = _path()
        mtime = p.stat().st_mtime
        if _cache.get("key") != (str(p), mtime):
            _cache.update(key=(str(p), mtime), data=json.loads(p.read_text(encoding="utf-8")))
        return copy.deepcopy(_cache["data"])   # 호출부가 고쳐도 캐시는 그대로
    except (OSError, ValueError) as e:
        logger.warning(f"[style_ref] 목록 못 읽음 — 참고 없이: {e}")
        return []


def answer_photo(file: str) -> bytes | None:
    """정답 원본 사진 — 배치 정하기(layout_plan)에서만 본다. 생성 입력에는 넣지 않는다 (10-09)."""
    p = _path().parent / "refs" / Path(file).name
    return p.read_bytes() if p.is_file() else None


def sketch_path(file: str) -> Path | None:
    """선 그림 파일 경로 (없으면 None) — 화면 목록 · 이미지 서빙용 (10-09)."""
    img = _path().parent / "refs" / "sketch" / (Path(file).stem + ".png")
    return img if img.is_file() else None


def sketch(file: str) -> tuple[str, bytes] | None:
    img = sketch_path(file)
    if img is not None:
        return file, img.read_bytes()
    logger.warning(f"[style_ref] {file} 선 그림 없음 — 참고 없이")
    return None


# 서로 대신 쳐 주는 각도 (10-05) — 정면 ↔ 앞쪽 비스듬히, 뒷면 ↔ 뒤쪽 비스듬히. 필수 면(coverage.REQUIRED)도
# 같은 쌍을 대신 쳐 준다. 정확히 같아야만 하면 헤드폰 정면 · 보드게임 정면이 정답을 하나도 못 받았다
VIEW_COMPAT = {"front": {"front", "front_34"}, "front_34": {"front", "front_34"},
               "back": {"back", "rear_34"}, "rear_34": {"back", "rear_34"}}


def view_ok(ref_view: str, photo_view: str | None) -> bool:
    return photo_view is not None and photo_view in VIEW_COMPAT.get(ref_view, {ref_view})


def candidates(item: str | None, category: str | None, view: str | None) -> list[dict]:
    """종류 · 각도가 맞는 정답 — 물건 이름 키워드가 맞는 것 먼저."""
    ok = [e for e in load()
          if (not category or e.get("category") == category)
          and len(e.get("view") or []) == 1 and (not view or view_ok(e["view"][0], view))]
    name = (item or "").lower()
    hit = [e for e in ok if any(k.lower() in name for k in e.get("items", []))]
    return hit + [e for e in ok if e not in hit]


def has_prep(e: dict, image: bytes, check) -> bool:
    """정답의 준비물이 이 사진에 다 보이나 — check(image, things) 는 안 보이는 것 목록 (detector.prep_check).
    확인 못 하면(호출 실패) 안 보이는 것으로."""
    things = e.get("prep") or []
    if not things:
        return True
    try:
        missing = check(image, things)
    except Exception as ex:
        logger.warning(f"[style_ref] {e['file']} 준비물 확인 실패 — 이 정답은 안 씀: {ex}")
        return False
    if missing:
        logger.info(f"[style_ref] {e['file']} 준비물 없음: {missing}")
    return not missing


def plan(item: str | None, category: str | None, photos: list[dict], load_image, check) -> tuple[str, str] | None:
    """게시글 단위: 물건의 사진들 [{file_id, view}] 중에서 정답과 그걸 따라 할 주 사진을 같이 고른다.
    상자가 다른 사진에만 있으면 그 사진으론 상자를 그릴 수 없다 — 준비물이 한 장에 다 보이는 사진이어야.
    반환: (정답 파일, 주 사진 file_id) 또는 None. VLM 대조는 (정답, 사진) 쌍마다 1회 — 종류 · 각도로 먼저 거른다."""
    for e in candidates(item, category, None):
        for p in photos:
            if not view_ok(e["view"][0], p.get("view")):
                continue
            img = load_image(p["file_id"])
            if img is not None and has_prep(e, img, check):
                return e["file"], p["file_id"]
    return None


def pick(item: str | None, category: str | None, view: str | None, image: bytes, check) -> tuple[str, bytes] | None:
    """사진 한 장 (게시글 단위로 미리 고르지 않았을 때) — 그 사진에서 준비물이 맞는 첫 정답의 선 그림."""
    for e in candidates(item, category, view):
        if has_prep(e, image, check):
            return sketch(e["file"])
    return None


def shows_accessory(file: str) -> bool:
    """고른 정답이 상자 같은 부가품을 같이 보여 주나 — 그러면 썸네일에서 부가품을 빼지 않는다."""
    e = next((x for x in load() if x.get("file") == file), {})
    return any(p.get("role") == "accessory" for p in e.get("parts") or [])


def plan_text(item: str | None, category: str | None, views: list[str | None],
              has_box: bool = True) -> tuple[str, str] | None:
    """정답 구도를 문장으로 (10-08 실험) — 이미지를 안 넣으니 준비물 VLM 대조 없이 종류 · 이름으로만 고른다.
    문장(layout)은 판매자 물건 기준이라 상자 같은 게 없으면 문장이 알아서 빠지게 쓴다.
    두 면(앞 · 뒤)을 세운 정답은 판매자 사진에 두 면이 다 있을 때만 그 문장, 아니면 layout_single.
    반환: (정답 파일, 문장) 또는 None."""
    name = (item or "").lower()
    entries = [e for e in load() if e.get("layout")]
    hit = [e for e in entries if any(k.lower() in name for k in e.get("items", []))]
    same = [e for e in entries if category and e.get("category") == category and e not in hit]
    have = {v for v in views if v}
    for e in hit + same:
        need = e.get("view") or []
        if len(need) > 1 and not all(any(view_ok(n, v) for v in have) for n in need):
            if e.get("layout_single"):
                side = next((v for v in have if any(view_ok(n, v) for n in need)), None)
                if side:
                    return e["file"], e["layout_single"].format(side="back" if side in ("back", "rear_34") else "front")
            continue
        # 판매자 사진에 포장 상자가 없으면 상자 문장을 쓰지 않는다 — 설명서를 상자로 바꿨다 (10-08)
        if not has_box and e.get("layout_nobox"):
            return e["file"], e["layout_nobox"]
        return e["file"], e["layout"]
    return None
