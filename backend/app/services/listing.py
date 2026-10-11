"""여러 장 업로드를 물건 단위로 (10-04) — 사진들을 물건별로 묶고, 물건마다 팔 물건인지 · 근거 사진인지.

VLM(detector.group_objects)은 묶음 · 각도 · 설명을 제안만 한다. 사용자가 화면에서 고치면(edit) 그 값이
기준이 되고, 사진을 더 올려도 고친 묶음은 지킨다(merge). 같은 물건 두 개와 한 물건 두 각도는 AI 도
헷갈리니 사용자 확인을 거친다 (study 10-04 §5).

물건 하나: {"id", "kind", "proof_type", "proof_for", "name", "label", "desc", "category", "subtype", "for_sale"}
  kind     product (팔거나 보여 주는 물건) | proof (설명서 · 보증서 · 정품 마크 · 회로 · 상태 화면 — 상품을 보증하는 사진)
           업로드 칸(사진의 slot)이 있으면 칸이 정한다 — AI 는 묶음 · 각도 · 근거 종류만 (10-05)
  name     짧은 영어 명사 (프롬프트용), label · desc 는 사용자에게 보이는 한국어
  subtype  종류보다 좁은 물건 (coverage.SUBTYPES — 지금은 laptop 만), 아니면 None
사진 하나에는 "object"(물건 id 또는 None) · "view" · "state" · "occluded" · "blurry" · "item_visible" 이 붙는다.
  state    subtype 물건 사진만 — 놓인 모양 (노트북 펼침 · 닫음, coverage.STATES). 그 밖엔 None
"""
import re

from app.prompts.presets import prompt_safe
from app.services import compositions, coverage

KINDS = ("product", "proof")
ROLES = ("main", "component", "accessory")   # 10-05: 본품 | 본구성품 | 부가품 (부가품은 썸네일에서 뺀다)
PROOF_TYPES = {"document": "설명서 · 보증서 · 영수증", "mark": "정품 마크 · 시리얼",
               "internals": "내부 · 회로", "screen": "작동 · 상태 화면", "other": "그 밖의 근거"}
MAX_OBJECTS = 20
MAX_COUNT = 99
LABEL_MAX, DESC_MAX = 40, 200
_ID = re.compile(r"o[0-9]{1,3}")
# name 은 프롬프트에 들어갈 수 있는 값이라 사용자 입력을 넣지 않는다 — AI 가 준 영어 명사, 아니면 종류에서 정한 말.
# label · desc 는 화면에만 쓴다 (프롬프트에 넣지 않는다)
CATEGORY_NOUN = {"shoes": "shoes", "clothing": "clothing item", "bag": "bag", "electronics": "electronic device",
                 "vehicle": "vehicle", "watch": "watch", "media": "book or disc", "pack": "set", "other": "item"}
PROOF_NOUN = {"document": "document", "mark": "authenticity mark", "internals": "internal parts", "screen": "screen",
              "other": "photo"}


def _text(v, limit: int) -> str:
    """화면에 보일 글 — 제어문자 · 줄바꿈만 정리 (따옴표 · 길이 80 제한이 있는 prompt_safe 와 다르다)."""
    t = "".join(ch if ch.isprintable() else " " for ch in str("" if v is None else v))
    return " ".join(t.split())[:limit]


def _key(v) -> str:
    return str(v).strip().lower()


def _false(v) -> bool:
    """VLM 이 false 를 문자열 · 0 으로 줄 때도 거짓으로 (없으면 참 — 파는 물건으로 본다)."""
    if isinstance(v, str):
        return v.strip().lower() in ("false", "no", "0")
    return v is False or (isinstance(v, int) and v == 0)


def _count(v) -> int:
    try:
        n = int(v)
    except (TypeError, ValueError):
        return 1
    return min(max(n, 1), MAX_COUNT)


def _photo_flags(p: dict) -> dict:
    return {"occluded": p.get("occluded") is True, "blurry": p.get("blurry") is True,
            "item_visible": p.get("item_visible") is not False}


def _clean_object(o: dict, oid: str) -> dict:
    kind = o.get("kind") if o.get("kind") in KINDS else "product"
    name = prompt_safe(o.get("name") or "")[:60] or "object"
    out = {"id": oid, "kind": kind, "name": name,
           "label": _text(o.get("label"), LABEL_MAX) or name,
           "desc": _text(o.get("desc"), DESC_MAX)}
    if kind == "proof":
        pt = str(o.get("proof_type") or "").strip().lower()
        out.update(proof_type=pt if pt in PROOF_TYPES else "other", proof_for=o.get("proof_for"),
                   category=None, for_sale=False, count=1, role=None, part_of=None)
    else:
        category = coverage.norm_category(o.get("category"))
        out.update(proof_type=None, proof_for=None, category=category,
                   subtype=coverage.norm_subtype(o.get("subtype"), category),
                   for_sale=not _false(o.get("for_sale")), count=_count(o.get("count")),
                   role=o.get("role") if o.get("role") in ROLES else "main",
                   part_of=o.get("part_of") if o.get("role") in ("component", "accessory") else None)
    if kind == "proof":
        out["subtype"] = None
    return out


def _fix_states(objects: list[dict], photos: list[dict]) -> None:
    """사진의 state 는 그 사진이 가리키는 물건의 subtype 에 맞는 값만 (물건을 옮기면 옛 상태가 남지 않게)."""
    sub = {o["id"]: o.get("subtype") for o in objects}
    for p in photos:
        p["state"] = coverage.norm_state(p.get("state"), sub.get(p.get("object")))


def _fix_proof_links(objects: list[dict]) -> None:
    """근거 사진이 가리키는 물건이 없거나 상품이 아니면 None. 본구성품 · 부가품의 part_of 는 본품만 가리킨다."""
    products = {o["id"] for o in objects if o["kind"] == "product"}
    mains = {o["id"] for o in objects if o["kind"] == "product" and o.get("role", "main") == "main"}
    for o in objects:
        if o["kind"] == "proof" and o.get("proof_for") not in products:
            o["proof_for"] = None
        if o.get("part_of") is not None and (o.get("part_of") not in mains or o["part_of"] == o["id"]):
            o["part_of"] = None


def _enforce_slots(objects: list[dict], photos: list[dict], slots: list[str]) -> None:
    """종류는 판매자가 올린 칸이 정한다 (10-05). 한 물건의 사진이 모두 한 칸이면 그 칸 종류로 바꾸고,
    칸이 섞였으면 다른 칸 사진을 그 칸 종류의 새 물건으로 떼어 낸다 (AI 물건 하나당 하나).
    slot 이 None 인 사진(칸이 생기기 전에 올린 옛 사진)은 AI 판단 그대로."""
    by_obj: dict[str, set] = {}
    for p, s in zip(photos, slots):
        if p["object"] and s:
            by_obj.setdefault(p["object"], set()).add(s)
    split: dict[tuple, str] = {}
    for o in list(objects):
        got = by_obj.get(o["id"], set())
        if len(got) == 1:
            want = next(iter(got))
            if o["kind"] != want:
                o.update(_as_kind(o, want))
        elif len(got) > 1:
            for want in got - {o["kind"]}:
                if len(objects) >= MAX_OBJECTS:
                    break
                nid = f"o{len(objects) + 1}"
                while any(x["id"] == nid for x in objects):
                    nid = f"o{int(nid[1:]) + 1}"
                objects.append({**o, **_as_kind(o, want), "id": nid})
                split[(o["id"], want)] = nid
    kinds = {o["id"]: o["kind"] for o in objects}
    for p, s in zip(photos, slots):
        if s and p["object"] and kinds.get(p["object"]) != s:
            p["object"] = split.get((p["object"], s), p["object"])   # 물건 수 상한이면 떼지 않고 그대로
        if kinds.get(p["object"]) != "product":
            p["view"] = None
    for o in objects:
        if o["kind"] == "proof" and o.get("proof_for") is None:
            products = [x["id"] for x in objects if x["kind"] == "product" and x["for_sale"]]
            if len(products) == 1:
                o["proof_for"] = products[0]       # 파는 물건이 하나면 근거는 그 물건 것


def _as_kind(o: dict, kind: str) -> dict:
    """정리된 물건을 다른 종류로 — 이름 · 설명은 두고 종류별 필드만 다시."""
    raw = {k: v for k, v in o.items() if k not in ("id", "kind")}
    if kind == "proof" and o["kind"] != "proof":
        # 상품 이름("흰색 운동화")이 근거에 붙지 않게 — 종류에서 정한 말로, 사용자가 화면에서 고친다
        raw.update(name=PROOF_NOUN["other"], label="근거 사진", desc="")
    return {k: v for k, v in _clean_object({**raw, "kind": kind}, o["id"]).items() if k != "id"}


def normalize(data, n: int, slots: list[str] | None = None) -> dict:
    """VLM 응답 → {"objects": [...], "photos": [n 개, 입력 순서]}. 물건 id 는 o1, o2 … 로 다시 붙인다.
    물건 목록이 비면 모든 사진을 물건 하나로 (예전 한 물건 가정과 같은 결과)."""
    if isinstance(data, list) and data and isinstance(data[0], dict):
        data = data[0]
    if not isinstance(data, dict):
        raise ValueError(f"objects: 응답 형식이 다름: {str(data)[:200]}")
    raw_objects = [o for o in (data.get("objects") or []) if isinstance(o, dict)][:MAX_OBJECTS]
    ids, objects = {}, []
    for i, o in enumerate(raw_objects):
        key = _key(o.get("id")) if o.get("id") is not None else f"\0noid{i}"   # id 없는 물건은 사진이 가리킬 수 없다
        if key in ids:
            continue
        ids[key] = f"o{len(objects) + 1}"
        objects.append(_clean_object(o, ids[key]))
    for o in objects:
        if o["kind"] == "proof" and o["proof_for"] is not None:
            o["proof_for"] = ids.get(_key(o["proof_for"]))
        if o.get("part_of") is not None:
            o["part_of"] = ids.get(_key(o["part_of"]))

    by_index = {}
    for p in data.get("photos") or []:
        idx = p.get("index") if isinstance(p, dict) else None
        if isinstance(idx, int) and not isinstance(idx, bool) and 0 <= idx < n:   # true 가 1 이 되지 않게
            by_index.setdefault(idx, p)
    single = not objects and n > 0     # 묶음이 없다 (옛 모양 응답 등) — 모든 사진을 물건 하나로, 각도는 살린다
    if single:
        objects = [_clean_object({"name": data.get("item"), "category": data.get("category")}, "o1")]
    kinds = {o["id"]: o["kind"] for o in objects}
    photos = []
    for i in range(n):
        p = by_index.get(i, {})
        oid = "o1" if single else (ids.get(_key(p["object"])) if p.get("object") is not None else None)
        view = coverage.norm_view(p.get("view")) if kinds.get(oid) == "product" else None
        photos.append({"object": oid, "view": view, "state": p.get("state"), **_photo_flags(p)})
    used = {p["object"] for p in photos}
    if objects and not used - {None}:      # 물건은 냈는데 사진을 하나도 안 붙였다 — 묶음 없는 응답과 같게
        return normalize({k: v for k, v in data.items() if k != "objects"}, n, slots)
    if slots and len(slots) == n and any(slots):
        _enforce_slots(objects, photos, slots)
        used = {p["object"] for p in photos}
    objects = [o for o in objects if o["id"] in used]       # 사진이 하나도 없는 물건은 버린다
    _fix_proof_links(objects)
    _fix_states(objects, photos)
    return {"objects": objects, "photos": photos}


def apply(item: dict, out: dict, new_ids: set[str]) -> None:
    """분류 결과를 묶음에 넣는다. 사용자가 고친 적 없으면 통째로 바꾸고 (사진이 늘면 판단이 나아질 수 있다),
    고쳤으면 고친 묶음을 지키고 새 사진만 붙인다 — 새 사진은 AI 가 같이 묶은 기존 사진의 물건으로,
    기존 사진과 안 묶였으면 AI 가 만든 물건을 새로 더한다."""
    photos = item["photos"]
    new_ids = set(new_ids) | {p["file_id"] for p in photos if p.get("unclassified")}   # 지난번 묶기 실패한 사진도
    for p in photos:
        p.pop("unclassified", None)
    if not item.get("user_edited"):
        item["objects"] = out["objects"]
        for p, v in zip(photos, out["photos"]):
            p.update(v)
        _fix_states(item["objects"], photos)
        return
    ai = {o["id"]: o for o in out["objects"]}
    votes: dict[str, dict[str, int]] = {}
    for p, v in zip(photos, out["photos"]):
        if p["file_id"] not in new_ids and v["object"] and p.get("object"):
            votes.setdefault(v["object"], {}).setdefault(p["object"], 0)
            votes[v["object"]][p["object"]] += 1
    to_mine = {a: max(c, key=c.get) for a, c in votes.items()}
    objects = item.setdefault("objects", [])
    used = {o["id"] for o in objects}
    added_proof: dict[str, str] = {}                         # 새로 더한 내 물건 id → AI 가 준 proof_for (AI id)
    made: dict[tuple, str] = {}                              # (AI 물건, 칸) → 칸이 달라 새로 만든 내 물건
    for p, v in zip(photos, out["photos"]):
        if p["file_id"] not in new_ids:
            continue
        a = v["object"]
        slot = p.get("slot")
        mine = to_mine.get(a) if a else None
        kind = next((o["kind"] for o in objects if o["id"] == mine), None)
        # 기존 사진과 안 묶인 AI 물건 → 새 물건. 묶였어도 내 물건의 종류가 이 사진의 칸과 다르면(사용자가
        # 근거 사진을 상품으로 옮겨 둔 경우 등) 칸 종류의 새 물건으로 — 칸이 종류를 정한다 (10-05)
        clash = bool(a and slot and mine and kind != slot)
        if a and (a not in to_mine or clash) and len(objects) < MAX_OBJECTS:
            if clash and (a, slot) in made:
                mine = made[(a, slot)]
            else:
                nid = next(f"o{k}" for k in range(1, 10**4) if f"o{k}" not in used)
                used.add(nid)
                new = {**ai[a], "id": nid, "proof_for": None, "part_of": None}   # AI id 는 내 id 와 다르다
                if slot and new["kind"] != slot:
                    new.update(_as_kind(new, slot))
                objects.append(new)
                if ai[a].get("proof_for") and new["kind"] == "proof":
                    added_proof[nid] = ai[a]["proof_for"]
                if clash:
                    made[(a, slot)] = nid
                else:
                    to_mine[a] = nid
                mine = nid
            kind = next(o["kind"] for o in objects if o["id"] == mine)
        p.update(v, object=mine, view=v["view"] if kind == "product" else None)
        item["needs_review"] = True                          # 고친 뒤에 AI 가 붙인 사진 — 다시 확인받는다
    for o in objects:                                        # AI id 와 내 id 가 둘 다 o1, o2 … 라 섞지 않고 to_mine 으로만
        if o["id"] in added_proof:
            o["proof_for"] = to_mine.get(added_proof[o["id"]])
    _fix_proof_links(objects)
    _fix_states(objects, photos)


def edit(item: dict, body: dict) -> list[str]:
    """사용자가 고친 묶음 → 오류 목록 (있으면 item 을 바꾸지 않는다).
    body: {"objects": [{"id", "kind", "label", "desc", "category", "for_sale", "proof_type", "proof_for"}],
           "photos": [{"file_id", "object", "view", "state"}]} — 사진은 묶음의 사진 전부, 한 번씩."""
    errors = []
    objs = body.get("objects")
    if not isinstance(objs, list) or len(objs) > MAX_OBJECTS:
        return [f"objects 는 {MAX_OBJECTS}개 이하 목록"]
    old = {o["id"]: o for o in item.get("objects") or []}
    clean, seen = [], set()
    for o in objs:
        if not isinstance(o, dict):
            errors.append("objects 항목이 객체가 아니다")
            continue
        oid = o.get("id")
        if not isinstance(oid, str) or not _ID.fullmatch(oid) or oid in seen:
            errors.append(f"물건 id {oid!r} — o 뒤에 숫자, 겹치면 안 된다")
            continue
        seen.add(oid)
        if o.get("kind") not in KINDS:
            errors.append(f"{oid}: kind {o.get('kind')!r} (product | proof)")
            continue
        if o["kind"] == "product" and o.get("category") not in coverage.CATEGORIES:
            errors.append(f"{oid}: category {o.get('category')!r} (가능: {', '.join(coverage.CATEGORIES)})")
            continue
        if o["kind"] == "proof" and (o.get("proof_type") or "other") not in PROOF_TYPES:
            errors.append(f"{oid}: proof_type {o.get('proof_type')!r} (가능: {', '.join(PROOF_TYPES)})")
            continue
        prev = old.get(oid, {})
        same = prev and prev.get("kind") == o["kind"] and (
            prev.get("category") == o.get("category") if o["kind"] == "product"
            else prev.get("proof_type") == (o.get("proof_type") or "other"))
        name = prev.get("name") if same else (CATEGORY_NOUN.get(o.get("category"), "item") if o["kind"] == "product"
                                              else PROOF_NOUN[o.get("proof_type") or "other"])
        # subtype 은 사용자가 고르지 않는다 — 종류가 그대로면 AI 값을 지키고, 바꾸면 버린다 (name 과 같은 이유)
        clean.append(_clean_object({**o, "name": name, "subtype": prev.get("subtype") if same else None,
                                    "for_sale": o.get("for_sale") is True,
                                    "role": o.get("role") or prev.get("role")}, oid))
    ids = {o["id"] for o in clean}

    rows = body.get("photos")
    have = {p["file_id"]: p for p in item["photos"]}
    if not isinstance(rows, list):
        return errors + ["photos 는 목록"]
    given = {}
    for r in rows:
        fid = r.get("file_id") if isinstance(r, dict) else None
        if fid not in have or fid in given:
            errors.append(f"사진 {fid!r} — 이 묶음에 없거나 두 번 나왔다")
            continue
        oid = r.get("object")
        if oid is not None and oid not in ids:
            errors.append(f"사진 {fid}: 없는 물건 {oid!r}")
            continue
        view = r.get("view")
        if view is not None and view not in coverage.VIEWS:
            errors.append(f"사진 {fid}: view {view!r}")
            continue
        given[fid] = (oid, view, r.get("state"))
    if set(given) != set(have) and not errors:
        errors.append(f"사진이 빠졌다: {', '.join(sorted(set(have) - set(given)))}")
    if errors:
        return errors

    used = {oid for oid, _, _ in given.values() if oid}
    clean = [o for o in clean if o["id"] in used]           # 사진이 하나도 없는 물건은 지운다
    _fix_proof_links(clean)
    kinds = {o["id"]: o["kind"] for o in clean}
    for p in item["photos"]:
        oid, view, state = given[p["file_id"]]
        p.update(object=oid, view=view if kinds.get(oid) == "product" else None, state=state)
    _fix_states(clean, item["photos"])
    item.update(objects=clean, user_edited=True, needs_review=False)
    return []


def ensure_objects(item: dict) -> None:
    """10-04 전에 만든 묶음(objects 없음) — 사진 전부를 물건 하나로 본다 (그때의 가정 그대로)."""
    if item.get("objects") is not None or not item.get("photos"):
        return
    for p in item["photos"]:
        p.setdefault("object", "o1")
    has = any(p["object"] == "o1" for p in item["photos"])
    item["objects"] = [_clean_object({"name": item.get("item"), "category": item.get("category")}, "o1")] if has else []


def summary(item: dict) -> list[dict]:
    """물건마다 사진 · 빠진 면 · 다시 찍을 사진 · 구도 후보 (근거 사진은 빠진 면 · 구도 없음)."""
    out = []
    for o in item.get("objects") or []:
        ph = [p for p in item["photos"] if p.get("object") == o["id"]]
        row = {**o, "photo_ids": [p["file_id"] for p in ph],
               "proof_type_label": PROOF_TYPES.get(o.get("proof_type")) if o["kind"] == "proof" else None}
        if o["kind"] == "product":
            sub = o.get("subtype")
            cov = coverage.check(o["category"], ph, sub)
            row.update(missing=cov["missing"],
                       retake=[{"file_id": ph[r["index"]]["file_id"], "reason": r["reason"]} for r in cov["retake"]],
                       complete=cov["complete"], compositions=compositions.options(o["category"], ph, sub))
        else:
            row.update(missing=[], retake=[], complete=True, compositions=[])
        out.append(row)
    return out


def best_photo(row: dict, photos: list[dict]) -> str | None:
    """물건의 대표 사진 하나 — 다시 찍을 필요 없는 사진 중 정석 구도에 맞는 것(대표컷 구도가 앞) →
    다시 찍을 필요 없는 전체 사진 → 구도에 맞는 아무 사진 → 그 물건 첫 사진. 흐린 사진이 구도 순서 때문에 먼저 뽑히지 않게."""
    mine = [p for p in photos if p.get("object") == row["id"]]
    ok = {p["file_id"] for p in mine if not coverage.problems(p)}
    in_comp = [fid for c in row.get("compositions") or [] for fid in c["photo_ids"]]
    good_whole = [p["file_id"] for p in mine if p["file_id"] in ok and p.get("view") not in coverage.CLOSE_UPS]
    for pool in ([f for f in in_comp if f in ok], good_whole, in_comp, [p["file_id"] for p in mine]):
        if pool:
            return pool[0]
    return None


def main_object(rows: list[dict]) -> dict | None:
    """대표 물건 — 파는 본품 중 사진이 가장 많은 것 (같으면 앞의 것). 본품이 없으면 파는 상품, 그것도 없으면 상품 중에서.
    충전기 사진이 더 많아도 휴대폰이 대표다 (10-05 역할)."""
    for pool in ([r for r in rows if r["kind"] == "product" and r["for_sale"] and r.get("role", "main") == "main"],
                 [r for r in rows if r["kind"] == "product" and r["for_sale"]],
                 [r for r in rows if r["kind"] == "product"]):
        if pool:
            return max(pool, key=lambda r: (len(r["photo_ids"]), -rows.index(r)))
    return None
