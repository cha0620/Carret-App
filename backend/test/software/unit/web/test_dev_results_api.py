"""GET /dev/results — storage/result/*.jpg 를 원본/판정(judge)/인스펙트/피드백/원본메타와
묶어서 최신순으로 돌려주는지 검증. 계산 없이 저장된 산출물만 읽는 엔드포인트라
파일/DB 를 직접 깔아두고 응답 모양만 본다. 외부 API 호출 없음.
"""
import json
import os
import uuid

import pytest

from app.core import db
from app.core.config import settings
from app.services.persistence import store


@pytest.fixture()
def feedback_db(monkeypatch, tmp_path):
    """실제 backend/data/*.db 를 건드리지 않도록 격리된 sqlite 파일 사용."""
    db_path = tmp_path / "dev_results.db"
    monkeypatch.setattr(settings, "db_path", str(db_path))
    db.init_db()
    return db_path


def _fid() -> str:
    return uuid.uuid4().hex


def _put_result(root, file_id, preset, data=b"jpg", mtime=None):
    p = root / "result" / f"{file_id}_{preset}.jpg"
    p.write_bytes(data)
    if mtime is not None:
        os.utime(p, (mtime, mtime))
    return p


def _put_json(root, name, obj):
    (root / "quality" / name).write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")


def test_empty_result_dir_returns_empty_items(client, feedback_db, tmp_storage):
    r = client.get("/dev/results")
    assert r.status_code == 200
    assert r.json() == {"items": []}


def test_missing_result_dir_returns_empty_items(client, feedback_db, tmp_storage):
    (tmp_storage / "result").rmdir()
    r = client.get("/dev/results")
    assert r.status_code == 200
    assert r.json() == {"items": []}


def test_full_item_bundles_everything(client, feedback_db, tmp_storage, make_png):
    fid, preset = _fid(), "studio_white"
    (tmp_storage / "original" / f"{fid}.png").write_bytes(make_png())
    rp = _put_result(tmp_storage, fid, preset)
    judge = {"score": 0.9, "verdict": "pass", "note": "좋음"}
    inspect = {"anchors": [1, 2], "checks": {"text": True}, "gate": True, "guard": None}
    _put_json(tmp_storage, f"{fid}_{preset}.json", judge)
    _put_json(tmp_storage, f"{fid}_{preset}_inspect.json", inspect)
    store.record_original(fid, "png", "upload", original_name="내사진.png", size_bytes=123)
    store.record_result(fid, preset, rp.name, "mug", ["a", "b"], True, [], elapsed_s=1.5)
    store.save_feedback(fid, preset, 4, "괜찮은 결과네요", source="agent")

    r = client.get("/dev/results")

    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 1
    it = items[0]
    assert it["file_id"] == fid
    assert it["preset"] == preset
    assert it["orig"] == f"/storage/original/{fid}.png"
    assert it["result"] == f"/storage/result/{fid}_{preset}.jpg"
    assert it["created"] == pytest.approx(rp.stat().st_mtime)
    assert it["name"] == "내사진.png"
    assert it["db"]["preset_key"] == preset
    assert it["db"]["result_name"] == rp.name
    assert it["db"]["item"] == "mug"
    assert it["judge"] == judge
    assert it["inspect"] == inspect
    assert it["feedback"]["user"] is None
    assert it["feedback"]["agent"]["rating"] == 4
    assert it["feedback"]["agent"]["comment"] == "괜찮은 결과네요"


def test_missing_optional_artifacts_are_none(client, feedback_db, tmp_storage):
    fid, preset = _fid(), "studio_white"
    _put_result(tmp_storage, fid, preset)

    r = client.get("/dev/results")

    assert r.status_code == 200
    it = r.json()["items"][0]
    assert it["orig"] is None
    assert it["judge"] is None
    assert it["inspect"] is None
    assert it["feedback"] == {"user": None, "agent": None}
    assert it["db"] is None
    assert it["name"] is None


def test_judge_present_inspect_missing(client, feedback_db, tmp_storage):
    fid, preset = _fid(), "studio_white"
    _put_result(tmp_storage, fid, preset)
    _put_json(tmp_storage, f"{fid}_{preset}.json", {"score": 1})

    it = client.get("/dev/results").json()["items"][0]
    assert it["judge"] == {"score": 1}
    assert it["inspect"] is None


def test_inspect_present_judge_missing(client, feedback_db, tmp_storage):
    fid, preset = _fid(), "studio_white"
    _put_result(tmp_storage, fid, preset)
    _put_json(tmp_storage, f"{fid}_{preset}_inspect.json", {"gate": False})

    it = client.get("/dev/results").json()["items"][0]
    assert it["judge"] is None
    assert it["inspect"] == {"gate": False}


def test_name_falls_back_to_inbox_done(client, feedback_db, tmp_storage):
    fid, preset = _fid(), "studio_white"
    _put_result(tmp_storage, fid, preset)
    done = tmp_storage / "inbox" / "done"
    done.mkdir(parents=True)
    (done / f"{fid}_my_photo 01.jpg").write_bytes(b"x")

    it = client.get("/dev/results").json()["items"][0]
    assert it["name"] == "my_photo 01.jpg"


def test_name_falls_back_when_db_original_name_is_null(client, feedback_db, tmp_storage):
    fid, preset = _fid(), "studio_white"
    _put_result(tmp_storage, fid, preset)
    store.record_original(fid, "jpg", "inbox", original_name=None)
    done = tmp_storage / "inbox" / "done"
    done.mkdir(parents=True)
    (done / f"{fid}_restored.jpg").write_bytes(b"x")

    it = client.get("/dev/results").json()["items"][0]
    assert it["name"] == "restored.jpg"


def test_db_name_wins_over_inbox_done(client, feedback_db, tmp_storage):
    fid, preset = _fid(), "studio_white"
    _put_result(tmp_storage, fid, preset)
    store.record_original(fid, "jpg", "inbox", original_name="from_db.jpg")
    done = tmp_storage / "inbox" / "done"
    done.mkdir(parents=True)
    (done / f"{fid}_from_done.jpg").write_bytes(b"x")

    it = client.get("/dev/results").json()["items"][0]
    assert it["name"] == "from_db.jpg"


def test_name_none_when_neither_db_nor_done(client, feedback_db, tmp_storage):
    fid, preset = _fid(), "studio_white"
    _put_result(tmp_storage, fid, preset)
    done = tmp_storage / "inbox" / "done"
    done.mkdir(parents=True)
    (done / f"{_fid()}_other.jpg").write_bytes(b"x")  # 다른 file_id 는 매칭 안 됨

    it = client.get("/dev/results").json()["items"][0]
    assert it["name"] is None


def test_invalid_filenames_are_skipped(client, feedback_db, tmp_storage):
    good = _fid()
    _put_result(tmp_storage, good, "studio_white")
    res = tmp_storage / "result"
    (res / "foo.jpg").write_bytes(b"x")                      # 구분자/preset 없음
    (res / f"{good}.jpg").write_bytes(b"x")                  # hex32 인데 preset 없음
    (res / f"{good}_.jpg").write_bytes(b"x")                 # preset 빈 문자열
    (res / f"{good[:31]}_studio_white.jpg").write_bytes(b"x")  # 31자
    (res / f"{good}0_studio_white.jpg").write_bytes(b"x")    # 33자
    (res / f"{good}_studio_white.png").write_bytes(b"x")     # jpg 아님

    items = client.get("/dev/results").json()["items"]
    assert [(i["file_id"], i["preset"]) for i in items] == [(good, "studio_white")]


def test_non_hex_32_char_file_id_is_skipped(client, feedback_db, tmp_storage):
    bad = "z" * 32
    (tmp_storage / "result" / f"{bad}_studio_white.jpg").write_bytes(b"x")

    items = client.get("/dev/results").json()["items"]
    assert items == []


def test_preset_with_underscore_is_kept_whole(client, feedback_db, tmp_storage):
    fid = _fid()
    _put_result(tmp_storage, fid, "studio_white_v2")
    _put_json(tmp_storage, f"{fid}_studio_white_v2.json", {"ok": 1})

    it = client.get("/dev/results").json()["items"][0]
    assert it["file_id"] == fid
    assert it["preset"] == "studio_white_v2"
    assert it["judge"] == {"ok": 1}


def test_items_sorted_newest_first(client, feedback_db, tmp_storage):
    a, b, c = _fid(), _fid(), _fid()
    _put_result(tmp_storage, a, "studio_white", mtime=1_000_000)
    _put_result(tmp_storage, b, "studio_white", mtime=3_000_000)
    _put_result(tmp_storage, c, "studio_white", mtime=2_000_000)

    items = client.get("/dev/results").json()["items"]
    assert [i["file_id"] for i in items] == [b, c, a]
    assert [i["created"] for i in items] == [3_000_000, 2_000_000, 1_000_000]


def test_same_file_id_multiple_presets_are_separate_items(client, feedback_db, tmp_storage, make_png):
    fid = _fid()
    (tmp_storage / "original" / f"{fid}.jpg").write_bytes(make_png())
    _put_result(tmp_storage, fid, "studio_white", mtime=1_000_000)
    _put_result(tmp_storage, fid, "lifestyle", mtime=2_000_000)
    store.save_feedback(fid, "studio_white", 5, "좋다", source="user")

    items = client.get("/dev/results").json()["items"]
    assert [i["preset"] for i in items] == ["lifestyle", "studio_white"]
    assert all(i["orig"] == f"/storage/original/{fid}.jpg" for i in items)
    by_preset = {i["preset"]: i for i in items}
    assert by_preset["studio_white"]["feedback"]["user"]["rating"] == 5
    assert by_preset["lifestyle"]["feedback"] == {"user": None, "agent": None}


def test_broken_json_only_blanks_that_item(client, feedback_db, tmp_storage):
    """쓰다 만 json 하나가 목록 전체를 500 으로 만들지 않는다 — 그 칸만 None."""
    bad, good = _fid(), _fid()
    _put_result(tmp_storage, bad, "studio_white", mtime=1000)
    _put_result(tmp_storage, good, "studio_white", mtime=2000)
    (tmp_storage / "quality" / f"{bad}_studio_white.json").write_text("{not json")
    _put_json(tmp_storage, f"{good}_studio_white.json", {"fidelity": 4})

    r = client.get("/dev/results")

    assert r.status_code == 200
    items = {it["file_id"]: it for it in r.json()["items"]}
    assert items[bad]["judge"] is None
    assert items[good]["judge"] == {"fidelity": 4}
