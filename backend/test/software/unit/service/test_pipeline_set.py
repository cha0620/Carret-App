"""10-09 세트 프롬프트 · 구성품 개수 게이트 · qwen 인자 (VLM · fal 호출 없이 mock 만)."""

import json

import pytest

import app.services.pipeline as pipeline_mod
from app.services.ai import detector, generator

LEGO = [{"what": "lego set", "for_sale": True, "role": "main"},
        {"what": "instruction booklet 1", "for_sale": True, "role": "component"},
        {"what": "Instruction booklet 2", "for_sale": True, "role": "component"},
        {"what": "minifigure", "for_sale": True, "role": "component"},
        {"what": "charger", "for_sale": True, "role": "accessory"},
        {"what": "desk", "for_sale": False, "role": None}]


# ── set_pieces ──
def test_set_pieces_counts_kinds_strips_number_skips_accessory():
    assert pipeline_mod.set_pieces(LEGO) == [
        ("main piece", 1), ("instruction booklet", 2), ("minifigure", 1)]


def test_set_pieces_single_piece_is_not_a_set():
    assert pipeline_mod.set_pieces(LEGO[:1] + LEGO[4:]) is None


# ── set_prompt ──
def test_set_prompt_counts_and_back_front_layout():
    d = json.loads(pipeline_mod.set_prompt(pipeline_mod.set_pieces(LEGO)))
    assert d["set"] == {"pieces": 4, "main piece": 1, "instruction booklet": 2, "minifigure": 1}
    assert "instruction booklets standing at the back" in d["layout"]
    assert "minifigure in a row in front" in d["layout"]


# ── generate: clean_prompt 분기 ──
class _Stop(Exception):
    pass


def _gen(monkeypatch, **state):
    seen = {}

    def fake(img, preset, **kw):
        seen.update(preset=preset, kw=kw)
        raise _Stop

    monkeypatch.setattr(pipeline_mod, "_style_ref", lambda s: ("ref.png", b"sketch"))
    monkeypatch.setattr(pipeline_mod, "_generate_ai", fake)
    with pytest.raises(_Stop):
        pipeline_mod.generate({"preset": {"prompt": "BASE"}, "original": b"o", **state})
    return seen


def test_generate_set_uses_set_json_without_sketch(monkeypatch):
    seen = _gen(monkeypatch, clean_prompt="set", objects=LEGO)
    assert seen["preset"]["prompt"] == pipeline_mod.set_prompt(pipeline_mod.set_pieces(LEGO))
    assert seen["preset"]["no_negative"] is True and seen["kw"] == {}


def test_generate_set_but_not_a_set_falls_back_to_min(monkeypatch):
    seen = _gen(monkeypatch, clean_prompt="set", objects=LEGO[:1])
    # ref_sketch 가 없으면 정답 이미지를 넣지 않는다
    assert seen["preset"]["prompt"].startswith(pipeline_mod.MIN_PROMPT.split("{sketch}")[0] + " Photograph")
    assert "line drawing" not in seen["preset"]["prompt"]
    assert "style_ref" not in seen["kw"]


def test_generate_text_prompt_used_verbatim(monkeypatch):
    seen = _gen(monkeypatch, clean_prompt="text:hello {x}")
    assert seen["preset"]["prompt"] == "hello {x}"


# ── verify: 개수 게이트 ──
def _verify(monkeypatch, counts, gate=True, marks=None):
    monkeypatch.setattr(pipeline_mod, "_verify_marks", lambda s: dict(marks or {"gate_passed": True}))
    monkeypatch.setattr(pipeline_mod.settings, "count_gate", gate)
    monkeypatch.setattr(pipeline_mod, "_with_retry", lambda fn, *a, **k: fn())
    monkeypatch.setattr(pipeline_mod.storage, "load", lambda *a: b"r")
    calls = []

    def fake(o, r, kinds):
        calls.append(kinds)
        if isinstance(counts, Exception):
            raise counts
        return counts
    monkeypatch.setattr(detector, "count_pieces", fake)
    out = pipeline_mod.verify({"objects": LEGO, "original": b"o", "result_name": "x.jpg"})
    return out, calls


def test_count_gate_mismatch_fails_gate(monkeypatch):
    out, _ = _verify(monkeypatch, [1, 3, 1])
    assert out["gate_passed"] is False
    assert out["count_mismatch"] == [{"what": "instruction booklet", "want": 2, "got": 3}]


def test_count_gate_error_does_not_block(monkeypatch):
    out, _ = _verify(monkeypatch, RuntimeError("vlm down"))
    assert out == {"gate_passed": True}


def test_count_gate_off_or_already_failed_skips_call(monkeypatch):
    _, calls = _verify(monkeypatch, [9, 9, 9], gate=False)
    assert calls == []
    out, calls = _verify(monkeypatch, [9, 9, 9], marks={"gate_passed": False})
    assert calls == [] and out == {"gate_passed": False}


# ── mark_gate_retry: [presence only] 표시는 프롬프트에 안 넣는다 (회귀, 10-09 qwen3-1009) ──
def test_mark_gate_retry_strips_presence_only_tag():
    out = pipeline_mod.mark_gate_retry({"checks": [{"what": "BRAUN [presence only]", "preserved": False}]})
    assert '"BRAUN"' in out["gate_note"] and "presence only" not in out["gate_note"]
    assert out["count_mismatch"] == []


# ── detector.count_pieces ──
@pytest.mark.parametrize("resp", [{"counts": [{"count": 1}]}, {"counts": [{"count": 1}, {"count": "2"}]},
                                  {"counts": [{"count": 1}, {"count": True}]}])
def test_count_pieces_bad_response_raises(monkeypatch, resp):
    monkeypatch.setattr(detector, "_call_pair", lambda *a, **k: resp)
    with pytest.raises(ValueError):
        detector.count_pieces(b"o", b"r", ["a", "b"])


def test_count_pieces_returns_ints(monkeypatch):
    monkeypatch.setattr(detector, "_call_pair", lambda *a, **k: {"counts": [{"count": 2}, {"count": 0}]})
    assert detector.count_pieces(b"o", b"r", ["a", "b"]) == [2, 0]


# ── generator: qwen 인자 · no_negative ──
def test_qwen_model_args():
    n, kw = generator._model_args("fal-ai/qwen-image-3/edit")
    assert n == 3 and kw["enable_prompt_expansion"] is False and kw["negative_prompt"]


def test_set_pieces_follows_seller_choice():
    """판매자가 고르면 고른 것만 — 부가품도 고르면 조각으로 센다 (10-09)."""
    objs = [{"what": "LEGO car", "for_sale": True, "role": "main"},
            {"what": "minifigure", "for_sale": True, "role": "component"},
            {"what": "box", "for_sale": True, "role": "accessory"}]
    assert pipeline_mod.set_pieces(objs, sell=[0, 2]) == [("main piece", 1), ("box", 1)]
    assert pipeline_mod.set_pieces(objs, sell=[0]) is None


def test_transform_request_accessories_must_not_overlap_photo():
    """부가품은 사진에 넣을 물건과 겹치면 안 된다 (10-09 화면: 사진에 넣기 · 부가품 · 안 팖)."""
    import pytest
    from app.schemas.image import TransformRequest
    ok = TransformRequest(file_id="a" * 32, preset="studio_white", sell=[0, 1], accessories=[2])
    assert ok.accessories == [2]
    with pytest.raises(ValueError):
        TransformRequest(file_id="a" * 32, preset="studio_white", sell=[0, 1], accessories=[1])


def test_extra_views_explicit_loads_even_when_multi_view_off(monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "multi_view", False)
    monkeypatch.setattr(pipeline_mod.storage, "load_original", lambda fid: fid.encode())
    assert pipeline_mod._extra_views({"extra_view_ids": ["a"], "extra_views_explicit": True}) == [b"a"]
    assert pipeline_mod._extra_views({"extra_view_ids": ["a"]}) == []


def test_components_for_and_analysis_share_one_list(monkeypatch):
    """게시글 구성품 목록과 전체 분석의 objects 는 하나 — 판매자가 고른 번호가 같은 물건을 가리키게 (10-09).
    참고 사진 조합이 바뀌면 다시 보고, 이미 저장된 분석의 objects 도 그 목록으로 맞춘다."""
    saved = {}
    monkeypatch.setattr(pipeline_mod.storage, "save", lambda kind, name, data: saved.__setitem__(name, data))
    monkeypatch.setattr(pipeline_mod.storage, "load", lambda kind, name: saved.get(name))
    box = {"x1": 0, "y1": 0, "x2": 10, "y2": 10}
    calls = []

    def comps(images):
        calls.append(len(images))
        return {"objects": [{"what": "pawn", "box": box, "for_sale": True, "role": "component", "photo": len(images) - 1}],
                "contents_hidden": False}
    monkeypatch.setattr(pipeline_mod.detector, "components", comps)
    monkeypatch.setattr(pipeline_mod.detector, "analyze",
                        lambda img: {"item": "game", "anchors": [], "objects": [{"what": "x", "box": box, "for_sale": True}]})
    fid = "a" * 32
    first = pipeline_mod.components_for(fid, b"x")
    assert pipeline_mod.analyze_original(fid, b"x")["objects"] == first["objects"]   # 분석도 같은 목록
    assert pipeline_mod.components_for(fid, b"x") == first and calls == [1]          # 같은 조합이면 다시 안 부름
    second = pipeline_mod.components_for(fid, b"x", [("b" * 32, b"y")])              # 참고 사진이 바뀌면 다시
    assert calls == [1, 2] and second["extras"] == ["b" * 32]
    assert pipeline_mod.load_analysis(fid)["objects"] == second["objects"]           # 저장된 분석도 맞춘다


def test_apply_selection_counts_mains_and_uses_base_photo_boxes():
    """세트에서 구성품까지 골라도 개수는 본품 수 — "여러 개"로 배경 교체에 빠지지 않게. 박스는 기준 사진 것만 (10-09)."""
    b0 = {"x1": 0, "y1": 0, "x2": 100, "y2": 100}
    b1 = {"x1": 900, "y1": 900, "x2": 1000, "y2": 1000}
    a = {"item": "game", "objects": [
        {"what": "box", "box": b0, "for_sale": True, "role": "main", "photo": 0},
        {"what": "pawn", "box": b1, "for_sale": True, "role": "component", "photo": 1},
        {"what": "mug", "box": b1, "for_sale": False, "role": "accessory", "photo": 1}]}
    out = pipeline_mod.apply_selection(a, [0, 1])
    assert out["item_count"] == 1
    assert out["item_box"] == b0 and out["leave_out_boxes"] == []


def test_set_pieces_skip_pieces_only_in_photos_not_sent():
    """생성에 안 들어간 참고 사진에서만 보인 부품은 요구 · 검사하지 않는다 (10-09 리뷰)."""
    box = {"x1": 0, "y1": 0, "x2": 1, "y2": 1}
    s = {"objects": [{"what": "box", "box": box, "for_sale": True, "role": "main", "photo": 0},
                     {"what": "pawn", "box": box, "for_sale": True, "role": "component", "photo": 0},
                     {"what": "card", "box": box, "for_sale": True, "role": "component", "photo": 2}]}
    assert pipeline_mod.set_pieces(pipeline_mod._seen(s, 1), [0, 1, 2]) == [("main piece", 1), ("pawn", 1)]
    assert ("card", 1) in pipeline_mod.set_pieces(pipeline_mod._seen(s, 2), [0, 1, 2])


def test_plan_layout_uses_answer_photo_caches_and_falls_back(monkeypatch):
    """세트 배치는 정답 사진 기준으로 모델이 — 같은 구성이면 다시 안 부르고, 정답이 없거나 실패하면 None (10-09)."""
    saved, calls = {}, []
    monkeypatch.setattr(pipeline_mod.storage, "save", lambda kind, name, data: saved.__setitem__(name, data))
    monkeypatch.setattr(pipeline_mod.storage, "load", lambda kind, name: saved.get(name))
    monkeypatch.setattr(pipeline_mod.style_refs, "answer_photo", lambda f: b"ans" if f == "style_pack.jpg" else None)

    def plan(answer, photos, pieces):
        calls.append((answer, len(photos)))
        return "box standing at the back, board flat in the middle, pawns in front"
    monkeypatch.setattr(pipeline_mod.detector, "layout_plan", plan)
    s = {"file_id": "a" * 32, "original": b"o", "ref_file": "style_pack.jpg"}
    pieces = [("main piece", 1), ("pawn", 4)]
    assert pipeline_mod.plan_layout(s, pieces, [b"x"]).startswith("box standing")
    assert pipeline_mod.plan_layout(s, pieces, [b"x"]) and calls == [(b"ans", 2)]      # 저장해 둔 걸 다시
    assert pipeline_mod.plan_layout({**s, "ref_file": None}, pieces, []) is None       # 정답 없으면 규칙 배치로
    assert '"layout": "box standing' in pipeline_mod.set_prompt(pieces, planned="box standing")


def test_photo_review_prompt_lists_needs_only_when_given():
    from app import prompts as P
    assert "- 게임판 앞면" in P.photo_review_prompt(2, True, ["게임판 앞면"])
    assert "must show these parts" not in P.photo_review_prompt(2, False, None)


def test_set_pieces_mains_marks_main_rest_component():
    """판매자가 본품을 고르면 그 번호만 main piece, 나머지 고른 것은 구성품 (10-09)."""
    objs = [{"what": "car", "for_sale": True, "role": "main"},
            {"what": "figure", "for_sale": True, "role": "main"}]
    assert sorted(pipeline_mod.set_pieces(objs, sell=[0, 1], mains=[1])) == [("car", 1), ("main piece", 1)]


def test_apply_selection_item_count_follows_mains():
    box = {"x1": 0, "y1": 0, "x2": 10, "y2": 10}
    a = {"item": "x", "objects": [{"what": w, "box": box, "for_sale": True, "role": "main", "photo": 0}
                                  for w in ("a", "b", "c")]}
    assert pipeline_mod.apply_selection(a, [0, 1, 2], mains=[0, 2])["item_count"] == 2
    assert pipeline_mod.apply_selection(a, [0, 1, 2], mains=[])["item_count"] == 1


def test_transform_request_mains_must_be_in_sell():
    import pytest
    from app.schemas.image import TransformRequest
    assert TransformRequest(file_id="a" * 32, preset="studio_white", sell=[0, 1], mains=[1]).mains == [1]
    with pytest.raises(ValueError):
        TransformRequest(file_id="a" * 32, preset="studio_white", sell=[0], mains=[1])


def test_transform_request_accepts_null_lists():
    """화면은 고르지 않은 칸을 null 로 보낸다 — 응답용 필드가 요청 클래스에 잘못 들어가 422 가 났다 (10-09)."""
    from app.schemas.image import TransformRequest
    r = TransformRequest(file_id="a" * 32, preset="studio_white", sell=None, accessories=None, mains=None,
                         extra_view_ids=None, ref_file=None, item_id=None)
    assert r.accessories is None
