"""팔 물건 고르기 — 판매자 선택 반영, 기본값(본체 하나), 상자 판별, 세트 구성품 세기."""
import re

from app.services.pipeline.state import State


def _inside(t: dict, b: dict) -> bool:
    """글자 박스 가운데가 물건 박스 안에 있나 (0-1000)."""
    if not all(k in t for k in ("x1", "y1", "x2", "y2")):
        return False
    cx, cy = (t["x1"] + t["x2"]) / 2, (t["y1"] + t["y2"]) / 2
    return b["x1"] <= cx <= b["x2"] and b["y1"] <= cy <= b["y2"]


BOX_RE = re.compile(r"\b(box|boxes|package|packaging)\b|상자|박스")


def _is_box(o: dict) -> bool:
    return bool(BOX_RE.search(f"{o.get('what') or ''} {o.get('name_ko') or ''}".lower()))


def rep_split(objs: list, idx: list[int], mains: list | None = None) -> tuple[list[int], list[int]]:
    """대표사진에 넣을 것 = 본체 하나 (10-11 사용자: "본체만 첫 사진으로, 구성품들은 밑에 검증용으로 넘기거나
    원하면 따로 소규모로 생성"). 10-10 엔 본체 + 상자였다. 여러 개 한 장 생성은 개수 · 정체성을 못 지킨다 (캘린더 4번).
    idx 중에서 고른다 — 대표 = 판매자가 고른 본품(mains) → 분석 본품 → 상자 아닌 첫 것. 반환: (대표 번호, 나머지 번호)."""
    idx = [i for i in idx if 0 <= i < len(objs)]
    if not idx:
        return [], []
    # 본품이 여럿(CD 두 장 · 게임 5장)이면 "여러 개"다 — 하나만 생성하면 파는 개수와 사진이 다르다. 예전대로 (multi_item)
    n_main = len([i for i in (mains or []) if i in idx]) if mains else \
        len([i for i in idx if objs[i].get("role") in (None, "main") and not _is_box(objs[i])])
    if n_main >= 2:
        return idx, []
    main = next((i for i in (mains or []) if i in idx), None)
    if main is None:
        main = next((i for i in idx if objs[i].get("role") in (None, "main") and not _is_box(objs[i])), None)
    if main is None:   # 분석이 상자를 본품으로 봤다(보드게임 상자) — 상자만 남으므로 구성품보다 그게 본체
        main = next((i for i in idx if objs[i].get("role") == "main"), None)
    if main is None:
        main = next((i for i in idx if not _is_box(objs[i])), idx[0])
    return [main], [i for i in idx if i != main]


def _rep_info(objs: list, rep: list[int], rest: list[int]) -> dict:
    """대표 조각(개수 게이트 · 세트 프롬프트 기준)과 상세로 넘긴 구성품 개수(인증용 목록).
    indices 는 objects 번호 — 화면에서 구성품 하나만 따로 만들 때 sell 로 보낸다 (10-11)."""
    pieces = [(f"main piece ({objs[rep[0]]['what']})", 1)] + [(objs[i]["what"], 1) for i in rep[1:]]
    detail: dict[str, list[int]] = {}
    for i in rest:
        if objs[i].get("for_sale") or objs[i].get("role"):
            k = re.sub(r"[\s#]*\d+$", "", (objs[i].get("what") or "").strip())
            detail.setdefault(k, []).append(i)
    return {"rep_pieces": pieces,
            "detail_pieces": [{"what": k, "count": len(ix), "indices": ix} for k, ix in detail.items()]}


def apply_selection(a: dict, sell: list | None, answer_count: int | None = None, mains: list | None = None) -> dict:
    """팔 물건 고르기 (10-03) — 사용자가 고른 objects 로 개수 · 위치 · 이름을 다시 정한다.
    고르지 않은 물건은 leave_out(생성에서 빼라) · leave_out_boxes(배경 교체에서 지운다)로,
    그 물건 위에만 있는 글자는 글자 잠금에서 뺀다 ("빼라"와 "글자를 지켜라"가 같은 물건에 붙지 않게).
    마크(anchors)는 위치 좌표가 없어 못 가른다 — 알려진 한계.
    sell 이 analyze objects 목록보다 길거나 맞는 번호가 없으면 분석 판단 그대로.
    eval 은 고르는 화면이 없어 정답 개수(answer_count)만 쓴다 — 둘 다 주면 answer_count 가 개수를 덮는다."""
    if a.get("detect_failed"):
        return a
    out = dict(a)
    objs = a.get("objects") or []
    if sell is not None and objs:
        idx = {i for i in sell if isinstance(i, int) and not isinstance(i, bool) and 0 <= i < len(objs)}
        # 판매자가 고르면 고른 것 전부를 생성한다 (10-11 사용자: "리스트 주고 선택된 걸 생성").
        # 본체 하나로 줄이는 건 고르지 않았을 때의 기본값뿐 (_drop_accessories). 세트 구성품 · 개수는
        # _pieces → set_pieces 가 고른 번호(sell)로 센다
        picked = [objs[i] for i in sorted(idx)]
        dropped = [o for i, o in enumerate(objs) if i not in idx]
        if picked:
            # 개수 = 본품 수 (10-09) — 구성품(피규어 · 카드)까지 세면 "여러 개"가 돼 배경 교체로 간다.
            # role 이 없는 옛 분석이면 예전처럼 고른 수
            has_roles = any(o.get("role") for o in picked)
            if mains is not None:   # 판매자가 본품을 골랐다 (10-09)
                out["item_count"] = max(1, len([i for i in mains if i in idx]))
            else:
                out["item_count"] = max(1, sum(o.get("role") == "main" for o in picked)) if has_roles else len(picked)
            # 박스는 기준 사진(photo 0)의 것만 — 참고 사진의 박스는 다른 사진의 좌표다 (10-09)
            # 박스 · 글자 · 뺄 이름은 기준 사진(photo 0)의 것만 — 참고 사진의 박스는 다른 사진의 좌표다 (10-09)
            here = [o for o in picked if o.get("photo", 0) == 0]
            gone = [o for o in dropped if o.get("photo", 0) == 0]
            if here:   # 고른 게 전부 참고 사진에 있으면 분석의 item_box 그대로
                out["item_box"] = {"x1": min(o["box"]["x1"] for o in here), "y1": min(o["box"]["y1"] for o in here),
                                   "x2": max(o["box"]["x2"] for o in here), "y2": max(o["box"]["y2"] for o in here)}
            names = list(dict.fromkeys(o["what"] for o in picked))
            out["item"] = names[0] if len(names) == 1 else " and ".join(names[:3])
            # 고른 것과 같은 이름(CD 두 장 중 한 장)은 이름으로 빼라고 하면 남길 것까지 지운다 — 박스로만 뺀다
            out["leave_out"] = list(dict.fromkeys(o["what"] for o in gone if o["what"] not in names))
            out["leave_out_boxes"] = [o["box"] for o in gone]
            out["sell_boxes"] = [o["box"] for o in here]
            if "item_texts" in a:   # 옛 분석(글자 목록 없음)에 빈 목록을 만들면 read_text 가 "이미 읽음"으로 건너뛴다
                out["item_texts"] = [t for t in a.get("item_texts") or []
                                     if not (any(_inside(t, o["box"]) for o in gone)
                                             and not any(_inside(t, o["box"]) for o in here))]
    elif objs:
        out.update(_drop_accessories(out, objs))
    if isinstance(answer_count, int) and not isinstance(answer_count, bool) and answer_count >= 1:
        out["item_count"] = answer_count
    return out


def _drop_accessories(a: dict, objs: list) -> dict:
    """부가품(충전기 · 케이스 · 상자 — 없어도 같은 상품)은 썸네일에서 뺀다 (10-05 사용자 결정).
    사용자가 고르지 않았을 때만 — 고르면 고른 대로. 본품 · 본구성품이 하나도 없으면 그대로 둔다.
    개수(item_count)는 바꾸지 않는다 — 본품 + 본구성품(레고 차 + 피규어 + 설명서)을 세면 "여러 개"가 돼 배경 교체로 간다."""
    # 기준 사진(photo 0)의 것만 — 박스 · 글자 · 이름이 다 이 사진 기준이다 (10-09 리뷰)
    objs = [o for o in objs if o.get("photo", 0) == 0]
    sale = [i for i, o in enumerate(objs) if o.get("for_sale")]
    rep, rest = rep_split(objs, sale)
    # 대표사진 = 본체 하나 (10-11 사용자) — 상자 · 구성품 · 부가품은 빼고 상세(인증)로
    keep = [objs[i] for i in rep]
    drop = [objs[i] for i in rest]
    info = _rep_info(objs, rep, rest) if rep else {}
    if not drop or not keep:
        return info
    names = {o["what"] for o in keep}
    # 박스는 기준 사진(photo 0)의 것만 — 게시글 구성품 목록엔 참고 사진에서 본 것도 있다 (10-09)
    here = keep
    out = {"leave_out": list(dict.fromkeys([*(a.get("leave_out") or []),
                                             *(o["what"] for o in drop if o["what"] not in names)])),
           "leave_out_boxes": [*(a.get("leave_out_boxes") or []), *(o["box"] for o in drop if o.get("box"))],
           **({"item_box": {"x1": min(o["box"]["x1"] for o in here), "y1": min(o["box"]["y1"] for o in here),
                            "x2": max(o["box"]["x2"] for o in here), "y2": max(o["box"]["y2"] for o in here)}}
              if all(o.get("box") for o in here) else {}),
           **info}
    if "item_texts" in a:
        out["item_texts"] = [t for t in a.get("item_texts") or []
                             if not (any(_inside(t, o["box"]) for o in drop)
                                     and not any(_inside(t, o["box"]) for o in keep))]
    return out

SET_BACK = ("book", "books", "manual", "manuals", "booklet", "booklets", "box", "boxes", "package", "card", "cards", "poster")


def _seen(s: State, views: int) -> list:
    """실제로 생성에 들어간 사진(기준 0 ~ 참고 views 장)에서 보인 구성품만 — 모델이 못 본 부품을 요구 · 검사하지 않게 (10-09 리뷰).
    sell 번호는 전체 목록 기준이라 순서는 그대로 두고, 못 본 건 for_sale=False 로 표시한다."""
    return [o if o.get("photo", 0) <= views else {**o, "_unseen": True} for o in s.get("objects") or []]


def _pieces(s: State, views: int) -> list[tuple[str, int]] | None:
    """이 변환의 세트 구성품. 정답(fixed_pieces)이 있으면 그것 — 분석 이름("main piece", "photo pack")이 모호하고
    개수가 틀려 개수 게이트가 엉뚱하게 셌다 (10-10 캘린더). 없으면 분석 objects 에서 (생성에 들어간 사진만)."""
    fixed = [(str(k), int(n)) for k, n in (s.get("fixed_pieces") or s.get("rep_pieces") or []) if int(n) > 0]
    if fixed:
        # 정답이 한 개뿐이면 세트가 아니다 — 분석으로 넘어가지 않는다 (정답이 이긴다)
        return fixed if sum(n for _, n in fixed) >= 2 else None
    return set_pieces(_seen(s, views), s.get("sell"), s.get("mains"))


def set_pieces(objects: list | None, sell: list | None = None, mains: list | None = None) -> list[tuple[str, int]] | None:
    """팔 물건(본체 + 구성품)을 종류별로 센다. 조각이 2개 미만이면 세트가 아니다 → None.
    종류 이름은 what 에서 끝 번호를 뗀 것 ("instruction booklet 2" → "instruction booklet").
    sell(판매자가 고른 objects 번호)이 있으면 고른 것만, 부가품도 고르면 넣는다 — 선택은 판매자가 (10-09 사용자).
    _unseen(생성에 안 들어간 참고 사진에서만 보인 것)은 세지 않는다."""
    kinds: dict[str, int] = {}
    objs = objects or []
    if sell is not None:
        idx = {i for i in sell if isinstance(i, int) and not isinstance(i, bool) and 0 <= i < len(objs)}
        # 판매자가 본품을 고르면 그게 본품, 나머지 고른 것은 구성품 (10-09 — 본품 · 구성품 · 부가품을 판매자가)
        objs = [{**o, "for_sale": True,
                 "role": ("main" if i in mains else "component") if mains is not None
                 else (o.get("role") if o.get("role") != "accessory" else "component")}
                for i, o in enumerate(objs) if i in idx]
    for o in objs:
        if o.get("_unseen") or not o.get("for_sale") or o.get("role") not in (None, "main", "component"):
            continue
        k = "main piece" if o.get("role") in (None, "main") else re.sub(r"[\s#]*\d+$", "", (o.get("what") or "").strip().lower())
        if k:
            kinds[k] = kinds.get(k, 0) + 1
    return list(kinds.items()) if sum(kinds.values()) >= 2 else None
