"""생성 — 정답 구도(선 그림 · 문장), 세트 구성품 · 배치, 프롬프트 조립, generate 노드."""
import logging
import hashlib
import json
import re
from app.core.config import settings
from app.prompts.presets import leave_out, text_lock
from app.services import compositions
from app.services.ai import detector
from app.services import style_refs
from app.services.ai.generator import _generate_ai
from app.services.persistence import storage

from app.services.pipeline.state import State, VERIFY_ATTEMPTS, _result_name, _with_retry
from app.services.pipeline.selection import SET_BACK, _pieces

logger = logging.getLogger("carret.pipeline")


# ── 정답 구도(스타일 참고) — 사용자가 정한 것 (헷갈리지 않게 한곳에) ─────────────────
# 1. 정답 사진 원본은 생성 모델에 넣지 않는다. 넣으면 정답 물건을 베낀다
#    (10-04 맥북 → 에어 몸체, 10-05 아이폰 → 16, 코트 → 티셔츠, 10-08 posts-textphoto-1008 도 같음).
# 2. 정답 구도는 정답에서 딴 "선 그림"으로 준다 (refs/sketch/, eval/style_sketch.py — OCR 로 글자 지우고 외곽선).
# 3. 기본 = 선 그림 + "구도 문장"(style_refs.json 의 layout) 둘 다 (10-08 사용자 지시). 문장만 · 선 그림만은 비교용:
#    - 문장은 동작 · 배치만 ("사진에 코트만 남긴다", "코트를 똑바로 세워") — 단추 · 옷깃 같은 내부 디테일,
#      "실루엣" 같은 말은 쓰지 않는다. 물건은 쉬운 이름 (포장 상자 · 본품 · 작은 조각)
#    - 레고가 아니라 "장난감"으로 넓게 — 포장 상자는 뒤, 내용물은 앞
#    - 휴대폰: 판매자 사진에 앞 · 뒤가 다 있을 때만 두 면 겹친 구도, 아니면 보이는 한 면만
#    - 코트: 정답은 사용자가 새로 준 style_coat.jpg (옷걸이 · 사람 없이 앞판 정면)
# 4. 실행 전 확인: 같은 시도가 study 에 있는지 먼저 찾고, 유료 실행은 매번 허락받는다 (CLAUDE.md).
# 5. 물건 역할 (10-05): 부가품(충전기 · 케이스 · 상자 · 폰 설명서)은 썸네일에서 빼고,
#    본구성품(레고 블록 · 피규어 · 레고 설명서)은 남긴다. 기준은 "빼도 같은 상품인가".
# 실험 실행: python eval/run_posts.py --posts ... --run-id ... --ref-text --ref-sketch
STYLE_REF_NOTE = (
    "\n\nThe LAST image is only a LINE DRAWING that shows the camera angle, placement and framing — it is "
    "not the item. Follow its angle, placement and framing, centered on a pure white background with a soft "
    "shadow. Take color, body, parts, materials and every printed detail ONLY from the photo(s) of the item. "
    "Do not add any text or logo.")
MULTI_VIEW_NOTE = (
    "\n\nThe FIRST image is the photo to edit — keep its camera angle and its item. The next {n} image(s) show "
    "the SAME item from other angles. Use them only to check shape, color, material and lettering that are "
    "blurry or hidden in the first image. Do NOT merge the other angles into the result and do NOT draw a "
    "second copy of the item.")


def _extra_views(s: State) -> list[bytes]:
    # 판매자가 화면에서 같은 물건 사진을 같이 보낸 경우(extra_views_explicit, 10-09)는 설정과 상관없이 쓴다
    if not settings.multi_view and not s.get("extra_views_explicit"):
        return []
    out = []
    for fid in (s.get("extra_view_ids") or [])[:2]:
        b = storage.load_original(fid)
        if b is not None:
            out.append(b)
    return out


def _style_ref(s: State) -> tuple[str, bytes] | None:
    """정답 선 그림 (settings.style_ref). 재생성 때는 처음 고른 것 그대로 (VLM 대조를 또 하지 않는다).
    게시글 단위로 미리 고른 정답(ref_file — 주 사진에서 준비물을 이미 대조함)이 있으면 그것, 없으면 이 사진으로 대조."""
    if not settings.style_ref:
        return None
    if s.get("style_ref"):
        return style_refs.sketch(s["style_ref"])
    if s.get("ref_file"):
        return style_refs.sketch(s["ref_file"])
    return style_refs.pick(s.get("item"), s.get("ref_category"), s.get("ref_view"), s["original"],
                           detector.prep_check)


# 정리한 프롬프트 (10-08) — 프리셋 + 꼬리 문장을 이어 붙이면 지시가 겹치고 부딪혔다 ("주름 펴기" vs "모양 그대로",
# "태그 그대로" vs "태그 그리지 마"). 순서: 배경 → 이미지 역할 → 구도 → 지킬 것 → 더하지 말 것. {keep} 은 물건별 예시.
# "흔한 모양으로 바꾸지 마"를 명시 — 드문 디자인(숄 같은 후드)을 정석 모양으로 바꿨다 (10-08)
CLEAN_PROMPT = (
    "Professional product photography: pure white studio background, soft even lighting, subtle shadow.\n\n"
    "The FIRST image is the item to photograph.{roles}\n\n"
    "Layout: {layout}\n\n"
    "Keep the item exactly as in the photos — its shape{keep}, color, material, parts, real tags · logos · text, "
    "and every stain, scratch, tear, hole, fading and wear mark in the same place and size. Even if it looks unusual, "
    "do not reshape it into a more typical or standard version, and do not repair, clean or restore it.\n\n"
    "Do not add anything that is not in the photos — no new labels, tags, logos, text, objects, line drawings or "
    "extra views.")
CLEAN_ROLE_EXTRA = " The next {n} image(s) show the SAME item from other angles — use them only to check details."
CLEAN_ROLE_SKETCH = (" The LAST image is only a LINE DRAWING that shows camera angle, placement and framing — it is "
                     "not the item and must not appear in the result.")
# qwen-image-3/edit 용 (10-09) — 문서: "순서가 중요, image 1 · 2 · 3 으로 부른다". 금지 문장은 빼고
# (generator.QWEN_NEGATIVE 로) 원하는 모습만, 물건 보존을 맨 앞에 (BFL 안내: 앞 단어가 더 중요)
QWEN_PROMPT = (
    "Image 1 is the item to photograph.{roles}\n\n"
    "Photograph the item from image 1 exactly as it is — same shape{keep}, color, material, parts, real tags, "
    "logos and text, and every stain, scratch, tear, hole, fading and wear mark in the same place and size, "
    "even if the design looks unusual.\n\n"
    "Layout: {layout}\n\n"
    "Pure white studio background, soft even lighting, subtle shadow.")
# 최소 프롬프트 (10-09 사용자: "나열된 거 다 빼고 코트라는 거 빼고") — 물건 이름 · 목록 · 금지 없이,
# 재시도 사유도 붙이지 않는다 (재시도도 같은 문장)
# 배경은 고른 분위기(preset background) — 실험 기본은 studio_white (10-09 앱 반영)
MIN_PROMPT = ("Image 1 is the item.{sketch} Photograph the item from image 1 exactly as it is, standing upright, "
              "front view, centered. {background}")
DEFAULT_BACKGROUND = "Pure white studio background."
MIN_ROLE_SKETCH = " Image {k} is a line drawing that only shows the framing."
# 세트 프롬프트 (10-09) — 좌표 없이 종류 · 개수 · 대략 배치만 JSON 으로 (setjson 실험: 설명서 2 · 피규어 3 그대로).
# 물건 이름 대신 분석의 what 을 종류로 쓰고, 본체(main)는 "main piece" — 이름을 쓰면 흔한 모양으로 끌려간다
def plan_layout(s: State, pieces: list[tuple[str, int]], extras: list[bytes]) -> str | None:
    """세트 배치를 모델이 정한다 (10-09 사용자: 규칙으로 위 · 아래를 정하긴 애매 — 정답 사진 기준으로).
    판매자가 고른(또는 품목에 맞는 기본) 정답 사진의 배치를 이 구성품에 맞춘 한두 문장. 같은 구성 · 같은 정답 ·
    같은 참고 사진 수면 저장해 둔 걸 쓴다. 정답 사진이 없거나 호출이 실패하면 None (규칙 배치로)."""
    ref = s.get("ref_file")
    # 정답마다 손으로 쓴 세트 구도 문장(set_layout)이 있으면 그걸 쓴다 — 모델이 정한 배치가 정답과 딴판이었다 (10-10 캘린더)
    fixed = next((e.get("set_layout") for e in style_refs.load() if e["file"] == ref), None) if ref else None
    if fixed:
        return fixed
    answer = style_refs.answer_photo(ref) if ref else None
    if answer is None:
        return None
    key = hashlib.sha1(json.dumps([ref, pieces, len(extras)], sort_keys=True).encode()).hexdigest()[:12]
    name = f"{s['file_id']}_layout_{key}.json"
    try:
        cached = storage.load("quality", name)
        if cached:
            return json.loads(cached)["layout"]
    except Exception:
        pass
    try:
        text = _with_retry(lambda: detector.layout_plan(answer, [s["original"], *extras], pieces),
                           "layout_plan", VERIFY_ATTEMPTS)
    except Exception as e:
        logger.warning(f"[layout_plan] 실패 — 규칙 배치로: {e}")
        return None
    try:   # 저장 실패로 변환이 깨지지 않게 — 다음에 다시 부를 뿐 (10-09 리뷰)
        storage.save("quality", name, json.dumps({"layout": text}, ensure_ascii=False).encode("utf-8"))
    except Exception as e:
        logger.warning(f"[layout_plan] 저장 실패(무시): {e}")
    logger.info(f"[layout_plan] {ref} → {text}")
    return text


def set_prompt(pieces: list[tuple[str, int]], views: int = 0, background: str = "", planned: str | None = None) -> str:
    def plural(k: str, n: int) -> str:
        return k if n < 2 or k.endswith("s") else k + ("es" if k.endswith(("x", "ch", "sh")) else "s")
    # 단어 단위로 본다 — "card" 가 "cardigan" 에 걸리지 않게 (10-09 리뷰)
    is_back = {k: bool(set(re.findall(r"[a-z]+", k)) & set(SET_BACK)) for k, _ in pieces}
    back = [plural(k, n) for k, n in pieces if is_back[k]]
    front = [plural(k, n) for k, n in pieces if not k.startswith("main piece") and not is_back[k]]
    layout = ", ".join(([f"{' and '.join(back)} standing at the back"] if back else [])
                       + ["main piece large at front center"]
                       + ([f"{' and '.join(front)} in a row in front"] if front else []))
    return json.dumps({
        "task": "photograph the set from image 1 exactly as it is",
        "background": background or DEFAULT_BACKGROUND,
        **({"other_photos": f"image{'s 2-' + str(1 + views) if views > 1 else ' 2'} show the same set from other "
                               "angles and more of its pieces — every listed piece goes into the one photo, once"}
           if views else {}),
        "set": {"pieces": sum(n for _, n in pieces), **dict(pieces)},
        "layout": planned or layout,
        "keep": "same number, shape, color and print of every piece"}, ensure_ascii=False)


QWEN_ROLE_EXTRA = " Image 2{to} shows the same item from other angles, only for checking details."
QWEN_ROLE_SKETCH = " Image {k} is a line drawing that only shows camera angle, placement and framing."
REF_LAYOUT_NOTE = (
    "\n\nLayout: {layout} Take color, body, parts, materials and every printed detail ONLY from the photo(s) "
    "of the item. Do not add any object, text or logo that is not in the photos.")


def generate(s: State) -> dict:
    preset = s["preset"]
    set_views = 0
    # 고른 정석 구도의 틀(가운데 · 여백 · 수평) — 각도는 잠금이 지킨다. 글자 잠금은 그 뒤에.
    # 물건이 여러 개면 구도를 붙이지 않는다 — 구도 문장은 한 개 기준이라 여럿을 하나로 합친다 (10-03 CD 2장)
    composition = s.get("composition") if (s.get("item_count") or 1) <= 1 else None
    extra = (compositions.prompt_for(composition)
             + leave_out(s.get("leave_out") or []) + text_lock(s.get("item_texts") or []))
    if extra:
        preset = {**preset, "prompt": preset["prompt"] + extra}
    if s.get("gate_note"):
        preset = {**preset, "prompt": preset["prompt"] + s["gate_note"]}
    prior_check = s.get("photo_check")
    if prior_check and not prior_check.get("valid", True):
        # 재시도 — 직전 반려 사유를 프롬프트에 붙여서 "맹목적" 재시도가 되지
        # 않게 한다 (같은 프롬프트를 그대로 다시 던지면 같은 결함이 반복되기 쉬움).
        note = prior_check.get("reason") or "구도가 잘렸거나 텍스트가 상품을 가림"
        preset = {**preset, "prompt": preset["prompt"] + (
            f"\n\nIMPORTANT: a previous attempt was rejected for this reason: "
            f"\"{note}\". Show the FULL item in frame (no cropping/zoom) and do "
            f"NOT add any caption, subtitle, watermark, or overlaid text.")}
    # 정답 구도를 문장으로 받았으면 선 그림 이미지는 넣지 않는다 (10-08 실험)
    if s.get("ref_layout"):
        # 문장과 함께 정답 선 그림 (10-08) — 원본 사진은 물건을 베껴 안 쓴다 (posts-textphoto-1008)
        ref = style_refs.sketch(s["ref_file"]) if s.get("ref_sketch") and s.get("ref_file") else None
    else:
        ref = _style_ref(s)
    kept_lo = s.get("leave_out") or []   # 최소 · 세트 프롬프트에도 붙일 "뺄 물건" (아래 부가품 처리 반영)
    if ref and s.get("leave_out") and s.get("sell") is None and style_refs.shows_accessory(ref[0]):
        # 판매자가 고르지 않았을 때만 — 고르면 고른 대로 (10-09 사용자: 구성품 · 부가품 선택은 판매자가)
        # 정답 사진이 상자 같은 부가품을 같이 보여 준다 (준비물로 확인함) — 썸네일에서 부가품을 빼지 않는다
        acc = {o["what"] for o in s.get("objects") or [] if o.get("role") == "accessory"}
        lo = [n for n in s["leave_out"] if n not in acc]
        kept_lo = lo
        if lo != s["leave_out"]:
            preset = {**preset, "prompt": preset["prompt"].replace(leave_out(s["leave_out"]), leave_out(lo))}
    extras = _extra_views(s)
    if extras:
        preset = {**preset, "prompt": preset["prompt"] + MULTI_VIEW_NOTE.format(n=len(extras))}
    if ref:
        preset = {**preset, "prompt": preset["prompt"] + STYLE_REF_NOTE}
        logger.info(f"[style_ref] {ref[0]}")
    if s.get("ref_layout"):
        preset = {**preset, "prompt": preset["prompt"] + REF_LAYOUT_NOTE.format(layout=s["ref_layout"])}
    if isinstance(s.get("clean_prompt"), str) and s["clean_prompt"].startswith("text:"):
        extras = []   # 프롬프트를 통째로 받은 실험 (10-09 세트 JSON) — 메인 사진(+선 그림)만
        ref = ref if s.get("ref_sketch") else None   # 정답 원본 사진은 넣지 않는다
        preset = {**preset, "no_negative": True, "prompt": s["clean_prompt"][5:]}
    elif s.get("clean_prompt") == "set" and _pieces(s, len(extras[:2])):
        # 세트면 종류 · 개수 JSON (선 그림은 넣지 않는다 — 그림 속 다른 레고를 베꼈다, 10-09).
        # 입력은 최대 3장(qwen 한도) — 메인 + 같은 세트의 다른 상품 사진 2장으로 구성품을 다 담는다.
        # 근거 사진(인증서 · 영수증 같은 문서, slot=proof)은 extra_view_ids 에 애초에 없다 (10-09 사용자)
        extras, ref = extras[:2], None
        set_views = len(extras)
        pieces = _pieces(s, len(extras))
        planned = plan_layout(s, pieces, extras)   # 정답 사진 기준 배치 (정답이 없거나 실패하면 규칙 배치)
        # 생성하라고 고른 구성품은 "빼라" 에서 뺀다 — 기본값(본체 하나)이 붙인 leave_out 과 부딪히지 않게 (10-11)
        wanted = {re.sub(r"[\s#]*\d+$", "", re.sub(r"^main piece \((.*)\)$", r"\1", k)).strip().lower() for k, _ in pieces}
        kept_lo = [n for n in kept_lo if re.sub(r"[\s#]*\d+$", "", n).strip().lower() not in wanted]
        # 기능 문장은 남긴다 (10-09 리뷰): 판매자가 "안 팔아요"로 고른 물건 빼기 — 나열 · 금지 문장과 달리 지시 자체
        preset = {**preset, "no_negative": True,
                  "prompt": set_prompt(pieces, views=len(extras), planned=planned,
                                       background=preset.get("background") or DEFAULT_BACKGROUND)
                  + leave_out(kept_lo)}
    elif s.get("clean_prompt") in ("min", "set"):
        ref = ref if s.get("ref_sketch") else None   # "line drawing" 이라 부르니 선 그림만 (원본 정답 사진 금지)
        extras = []   # 최소: 메인 사진 + 선 그림만
        preset = {**preset, "no_negative": True,
                  "prompt": MIN_PROMPT.format(sketch=MIN_ROLE_SKETCH.format(k=2) if ref else "",
                                             background=preset.get("background") or DEFAULT_BACKGROUND)
                  # 정답 구도 = 선 그림 + 문장 (사용자 고정 규칙) · 고른 정석 구도 · 뺄 물건 (10-09 리뷰)
                  + (f" Layout: {s['ref_layout']}" if ref and s.get("ref_layout") else "")
                  + compositions.prompt_for(composition) + leave_out(kept_lo)}
    elif s.get("clean_prompt") and s.get("ref_layout"):
        # 정리한 프롬프트로 통째로 바꾼다 (10-08 실험) — 재시도 사유는 뒤에 다시 붙인다
        retry = preset["prompt"].split("\n\nIMPORTANT: a previous attempt", 1)
        if "qwen-image" in settings.fal_model:
            # 생성기가 앞의 3장만 넣는다 — 실제로 들어가는 장수로 번호를 맞춘다
            extras = extras[:3 - 1 - (1 if ref else 0)]
            roles = ((QWEN_ROLE_EXTRA.format(to=f"-{1 + len(extras)}" if len(extras) > 1 else "") if extras else "")
                     + (QWEN_ROLE_SKETCH.format(k=2 + len(extras)) if ref else ""))
            template = QWEN_PROMPT
        else:
            roles = (CLEAN_ROLE_EXTRA.format(n=len(extras)) if extras else "") + (CLEAN_ROLE_SKETCH if ref else "")
            template = CLEAN_PROMPT
        preset = {**preset, "prompt": template.format(roles=roles, layout=s["ref_layout"], keep=s.get("ref_keep") or "")
                  + ("\n\nIMPORTANT: a previous attempt" + retry[1].split("\n\n")[0] if len(retry) > 1 else "")}
    kw = {**({"style_ref": ref[1]} if ref else {}), **({"extra_views": extras} if extras else {})}
    gen = _generate_ai(s["original"], preset, **kw)
    result_name = _result_name(s)
    n = s.get("gen_attempts", 0) + 1
    if settings.keep_attempts:
        # 게이트에 걸려 배경 교체로 가면 생성본이 덮여 무엇이 바뀌었는지 볼 수 없다 (10-01) — eval 에서만 켠다
        try:
            storage.save("attempts", f"{s['file_id']}_{s['preset_key']}_try{n}.jpg", gen)
        except Exception as e:
            logger.warning(f"[generate] 시도 이미지 저장 실패(무시): {e}")
    # 저장은 validate_result 가 한다 — 검사 도중 예외로 끝난 이미지가 /storage/result/ 의
    # 예측 가능한 URL 에 남지 않게.
    return {
        "result": gen,
        "result_name": result_name,
        "prompt_used": preset["prompt"],
        "gen_prompts": (s.get("gen_prompts") or []) + [preset["prompt"]],
        "set_views": set_views,
        "gen_attempts": n,
        "style_ref": ref[0] if ref else None,
        "visual_similarity": None,   # 새 이미지 — 이전 결과 기준 값은 무효
        "item_similarity": None,
        "item_patch_similarity": None,
        "ocr_local_recall": None,
    }
