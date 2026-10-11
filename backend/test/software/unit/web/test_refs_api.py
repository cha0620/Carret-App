"""GET /api/refs · /api/refs/{name}.png · POST /api/transform 정답 구도/다른 사진 (10-09).

정답 목록은 tmp json + tmp 선 그림, run_transform 은 가짜 — VLM · fal 호출 없음.
"""
import json

import pytest
from pydantic import ValidationError

from app.core.config import settings
from app.schemas.image import TransformRequest

FID = "0123456789abcdef0123456789abcdef"
FID2 = "fedcba9876543210fedcba9876543210"


@pytest.fixture
def refs(tmp_path, monkeypatch):
    entries = [
        {"file": "lego.jpg", "note": "n", "category": "toy", "items": ["lego"], "layout": "stand it", "keep": "k"},
        {"file": "nosketch.jpg", "category": "toy"},
    ]
    p = tmp_path / "style_refs.json"
    p.write_text(json.dumps(entries), encoding="utf-8")
    (tmp_path / "refs" / "sketch").mkdir(parents=True)
    (tmp_path / "refs" / "sketch" / "lego.png").write_bytes(b"\x89PNG")
    (tmp_path / "secret.png").write_bytes(b"x")
    monkeypatch.setattr(settings, "style_ref_file", str(p))


@pytest.fixture
def fake_run(monkeypatch):
    rec = []

    def run_transform(fid, key, **kw):
        rec.append((key, kw))
        return {"result_name": "r.jpg", "prompt_used": "t", "checks": [], "bubbles": [],
                "gate_passed": None, "item": "lego", "considered": []}

    monkeypatch.setattr("app.services.pipeline.run_transform", run_transform)
    monkeypatch.setattr("app.services.pipeline.item_signals_and_save", lambda *a, **k: None)
    return rec


def test_refs_lists_only_with_sketch(client, refs):
    assert client.get("/api/refs").json() == {"refs": [
        {"file": "lego.jpg", "note": "n", "category": "toy", "items": ["lego"], "needs": [], "sketch_url": "/api/refs/lego.png"}]}


@pytest.mark.parametrize("name,code", [("lego", 200), ("nosketch", 404), ("secret", 404), ("..%2Fsecret", 404)])
def test_ref_sketch_serves_only_listed(client, refs, name, code):
    assert client.get(f"/api/refs/{name}.png").status_code == code


@pytest.mark.parametrize("ref_file", ["nope.jpg", "nosketch.jpg"])
def test_transform_unknown_or_sketchless_ref_400(client, refs, fake_run, ref_file):
    r = client.post("/api/transform", json={"file_id": FID, "preset": "studio_white", "ref_file": ref_file})
    assert r.status_code == 400 and fake_run == []


ITEM = "c" * 32


@pytest.fixture
def item_of(monkeypatch):
    """같이 넣을 사진의 묶음 — 서버가 같은 물건 · 상품 사진인지 본다 (10-09 리뷰)."""
    from app.api.routes import items as items_route
    photos = {"photos": [{"file_id": FID, "object": "o1", "slot": "product"},
                         {"file_id": FID2, "object": "o1", "slot": "product"}]}
    monkeypatch.setattr(items_route, "_load", lambda item_id: photos if item_id == ITEM else None)
    return photos


def test_transform_passes_ref_and_extra_views(client, refs, fake_run, item_of):
    r = client.post("/api/transform", json={"file_id": FID, "preset": "studio_white", "item_id": ITEM,
                                            "ref_file": "lego.jpg", "extra_view_ids": [FID2]})
    assert r.status_code == 200
    [(key, kw)] = fake_run
    assert key != "studio_white" and kw["clean_prompt"] == "set"   # 입력 조합이 다르면 다른 이름 · 앱 기본은 세트/최소
    assert (kw["ref_file"], kw["ref_layout"], kw["ref_sketch"], kw["ref_keep"]) == ("lego.jpg", "stand it", True, "k")
    assert kw["extra_view_ids"] == [FID2] and kw["extra_views_explicit"] is True


@pytest.mark.parametrize("extra", [
    {"extra_view_ids": [FID, FID2, FID]}, {"extra_view_ids": ["../etc/passwd"]},
    {"sell": [0], "accessories": [0]}])
def test_transform_request_rejects_bad_extra_or_accessory_overlap(extra):
    with pytest.raises(ValidationError):
        TransformRequest(file_id=FID, preset="studio_white", **extra)


def test_transform_rejects_extra_view_from_other_object_or_proof(client, refs, fake_run, item_of):
    """다른 물건 사진 · 근거 사진은 같이 넣지 않는다 — 화면만 믿지 않고 서버가 막는다 (10-09 리뷰)."""
    item_of["photos"][1]["slot"] = "proof"
    r = client.post("/api/transform", json={"file_id": FID, "preset": "studio_white", "item_id": ITEM,
                                            "extra_view_ids": [FID2]})
    assert r.status_code == 400 and not fake_run


def test_review_uses_product_photos_and_answer_then_caches(client, refs, monkeypatch):
    """AI 사진 검토 (10-09) — 그 물건의 상품 사진만(근거 제외) · 정답 사진과 같이, 같은 조합이면 다시 안 부른다."""
    from app.api.routes import items as items_route
    from app.services.ai import detector
    item = {"item_id": ITEM, "photos": [{"file_id": FID, "object": "o1", "slot": "product"},
                                        {"file_id": FID2, "object": "o1", "slot": "proof"}],
            "objects": [{"id": "o1", "kind": "product", "for_sale": True, "label": "보드게임"}]}
    monkeypatch.setattr(items_route, "_load", lambda i: item if i == ITEM else None)
    saved, calls = {}, []
    monkeypatch.setattr(items_route.storage, "load", lambda kind, name: saved.get(name))
    monkeypatch.setattr(items_route.storage, "save", lambda kind, name, data: saved.__setitem__(name, data))
    monkeypatch.setattr(items_route.storage, "load_original", lambda fid: b"img")

    def review(images, answer=None, needs=None):
        calls.append((len(images), answer is not None))
        return {"photos": ["상자 앞면"], "missing": [{"what": "구성품을 펼쳐 찍은 사진", "hint": "판 위에 다 놓고"}],
                "base": 0, "with": []}
    monkeypatch.setattr(detector, "photo_review", review)
    monkeypatch.setattr(items_route.listing, "summary", lambda it: [{"id": "o1", "kind": "product", "for_sale": True}])
    r = client.post(f"/api/items/{ITEM}/review", params={"object_id": "o1"})
    assert r.status_code == 200 and r.json()["photos"] == [{"file_id": FID, "shows": "상자 앞면"}]
    client.post(f"/api/items/{ITEM}/review", params={"object_id": "o1"})
    assert calls == [(1, False)]   # 근거 사진은 안 넣고, 두 번째는 저장해 둔 걸


def test_review_passes_ref_needs_and_returns_base_with_ids(client, refs, monkeypatch, tmp_path):
    """정답의 needs 를 넘기고 캐시 키에 포함 · base/with 는 file_id 로 (10-09). 없는 정답이면 400."""
    from app.api.routes import items as items_route
    from app.services import style_refs
    from app.services.ai import detector
    p = tmp_path / "style_refs.json"
    p.write_text(json.dumps([{"file": "lego.jpg", "category": "toy", "needs": ["앞면"]}]), encoding="utf-8")
    item = {"item_id": ITEM, "photos": [{"file_id": FID, "object": "o1"}, {"file_id": FID2, "object": "o1"}],
            "objects": [{"id": "o1", "kind": "product", "for_sale": True, "label": "레고"}]}
    monkeypatch.setattr(items_route, "_load", lambda i: item if i == ITEM else None)
    saved, seen = {}, []
    monkeypatch.setattr(items_route.storage, "load", lambda kind, name: saved.get(name))
    monkeypatch.setattr(items_route.storage, "save", lambda kind, name, data: saved.__setitem__(name, data))
    monkeypatch.setattr(items_route.storage, "load_original", lambda fid: b"img")
    monkeypatch.setattr(style_refs, "answer_photo", lambda f: b"ans")
    monkeypatch.setattr(items_route.listing, "summary", lambda it: [{"id": "o1", "kind": "product", "for_sale": True}])

    def review(images, answer=None, needs=None):
        seen.append(needs)
        return {"photos": ["a", "b"], "missing": [], "base": 1, "with": [0]}
    monkeypatch.setattr(detector, "photo_review", review)
    r = client.post(f"/api/items/{ITEM}/review", params={"object_id": "o1", "ref_file": "lego.jpg"})
    assert r.status_code == 200 and seen == [["앞면"]]
    assert r.json()["base_file_id"] == FID2 and r.json()["with_ids"] == [FID]
    p.write_text(json.dumps([{"file": "lego.jpg", "category": "toy", "needs": ["뒷면"]}]), encoding="utf-8")
    client.post(f"/api/items/{ITEM}/review", params={"object_id": "o1", "ref_file": "lego.jpg"})
    assert seen == [["앞면"], ["뒷면"]]   # needs 가 바뀌면 캐시를 안 쓴다
    assert client.post(f"/api/items/{ITEM}/review", params={"object_id": "o1", "ref_file": "nope.jpg"}).status_code == 400


def test_transform_drops_selection_when_components_changed(client, refs, fake_run, monkeypatch):
    """구성품 목록이 다른 참고 사진 조합이면 sell · mains 를 버린다 (10-09 리뷰)."""
    from app.services import pipeline
    monkeypatch.setattr(pipeline, "load_components", lambda fid: {"extras": ["old"]})
    monkeypatch.setattr(pipeline.storage, "load_original", lambda fid: b"img")
    monkeypatch.setattr(pipeline, "components_for", lambda *a, **k: {})
    r = client.post("/api/transform", json={"file_id": FID, "preset": "studio_white", "sell": [0, 1], "mains": [0]})
    assert r.status_code == 200 and fake_run[0][1]["sell"] is None and fake_run[0][1]["mains"] is None


def test_transform_passes_mains(client, refs, fake_run, monkeypatch):
    from app.services import pipeline
    monkeypatch.setattr(pipeline, "load_components", lambda fid: None)
    client.post("/api/transform", json={"file_id": FID, "preset": "studio_white", "sell": [0, 1], "mains": [1]})
    assert fake_run[0][1]["mains"] == [1]
