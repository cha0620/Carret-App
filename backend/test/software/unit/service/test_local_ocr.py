"""app.services.ai.local_ocr — EasyOCR 래퍼. 실제 easyocr 는 절대 안 부른다:
_load 를 가짜 Reader 로 바꾸거나, _load 자체를 볼 땐 sys.modules 에 가짜 easyocr 를 넣는다."""
import io
import sys
import threading
import types
from collections import OrderedDict

import pytest
from PIL import Image

from app.services.ai import local_ocr

# software/conftest.py 가 _load 를 막기 전(수집 시점)의 진짜 함수 — _load 자체를 검사할 때만
_REAL_LOAD = local_ocr._load


@pytest.fixture(autouse=True)
def fresh_cache(monkeypatch):
    monkeypatch.setattr(local_ocr, "_ORIG_CACHE", OrderedDict())


class FakeReader:
    """readtext(png bytes) → [(bbox, text, conf)]. 받은 이미지 크기를 기록한다."""
    def __init__(self, results=None):
        self.results = results if results is not None else [(None, "NIKE", 0.9)]
        self.sizes = []

    def readtext(self, png):
        assert png[:8] == b"\x89PNG\r\n\x1a\n"
        self.sizes.append(Image.open(io.BytesIO(png)).size)
        return self.results


def _reader(monkeypatch, results=None):
    r = FakeReader(results)
    monkeypatch.setattr(local_ocr, "_load", lambda: r)
    return r


def _img(w=200, h=100, mode="RGB", color=(255, 255, 255), fmt="PNG"):
    buf = io.BytesIO()
    Image.new(mode, (w, h), color).save(buf, format=fmt)
    return buf.getvalue()


# ── read_lines ──
def test_read_lines_filters_by_min_conf_inclusive(monkeypatch):
    assert local_ocr.MIN_CONF == 0.3
    _reader(monkeypatch, [(None, "A", 0.3), (None, "B", 0.2999), (None, "C", 1.0),
                          (None, "D", 0.0)])
    assert local_ocr.read_lines(_img()) == ["A", "C"]


def test_read_lines_box_crops_normalized_0_1000(monkeypatch):
    r = _reader(monkeypatch)
    local_ocr.read_lines(_img(200, 100), {"x1": 250, "y1": 100, "x2": 750, "y2": 900})
    # 사방 BOX_PAD(30) 여유 → 220..780 × 70..930 → 44..156 × 7..93
    assert r.sizes == [(112, 86)]


def test_read_lines_zero_width_box_becomes_pad_strip(monkeypatch):
    """폭 0 박스도 사방 여유 덕에 2*BOX_PAD 폭 띠로 읽힌다 (예전엔 예외).
    detector._has_box 가 퇴화 박스를 이미 걸러 실제 호출부에선 오지 않는다."""
    r = _reader(monkeypatch)
    local_ocr.read_lines(_img(200, 100), {"x1": 500, "y1": 500, "x2": 500, "y2": 900})
    assert r.sizes == [(12, 46)]           # 470..530 × 470..930


def test_read_lines_box_pad_clamped_to_image(monkeypatch):
    """0 과 1000 근처 박스는 여유가 이미지 밖으로 나가지 않는다 (0..1000 로 자름)."""
    seen = []
    r = _reader(monkeypatch)
    real_crop = Image.Image.crop
    monkeypatch.setattr(Image.Image, "crop",
                        lambda self, b: seen.append(b) or real_crop(self, b))
    local_ocr.read_lines(_img(1000, 1000), {"x1": 10, "y1": 20, "x2": 990, "y2": 985})
    assert seen == [(0, 0, 1000, 1000)]
    assert r.sizes == [(1000, 1000)]


# ── read_original: sha1 + box 키 LRU 캐시 ──
def _counting(monkeypatch, results=None):
    calls = []
    real = local_ocr.read_lines

    def spy(b, box=None):
        calls.append((b, box))
        return real(b, box)
    monkeypatch.setattr(local_ocr, "read_lines", spy)
    _reader(monkeypatch, results)
    return calls


def test_read_original_caches_tuple_and_returns_fresh_list(monkeypatch):
    _counting(monkeypatch)
    img = _img()
    first = local_ocr.read_original(img)
    first.append("MUTATED")
    second = local_ocr.read_original(img)
    assert second == ["NIKE"] and isinstance(second, list)
    second.clear()
    third = local_ocr.read_original(img)
    assert third == ["NIKE"] and third is not second
    assert all(isinstance(v, tuple) for v in local_ocr._ORIG_CACHE.values())


def test_read_original_exception_is_not_cached(monkeypatch):
    state = {"n": 0}

    def flaky(b, box=None):
        state["n"] += 1
        if state["n"] == 1:
            raise RuntimeError("first read failed")
        return ["OK"]
    monkeypatch.setattr(local_ocr, "read_lines", flaky)
    img = _img()
    with pytest.raises(RuntimeError):
        local_ocr.read_original(img)
    assert len(local_ocr._ORIG_CACHE) == 0
    assert local_ocr.read_original(img) == ["OK"]
    assert state["n"] == 2


# ── _load: 지연 import · 싱글톤 (가짜 easyocr 모듈) ──
def _fake_easyocr(monkeypatch):
    made = []

    class Reader:
        def __init__(self, langs, **kw):
            made.append((langs, kw))
    monkeypatch.setitem(sys.modules, "easyocr", types.SimpleNamespace(Reader=Reader))
    monkeypatch.setattr(local_ocr, "_reader", None)
    return made


def test_load_concurrent_builds_once(monkeypatch):
    made = _fake_easyocr(monkeypatch)
    out = []
    ts = [threading.Thread(target=lambda: out.append(_REAL_LOAD())) for _ in range(8)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert len(made) == 1 and len({id(r) for r in out}) == 1


def test_module_import_does_not_import_easyocr():
    import importlib
    import subprocess
    code = ("import sys; import app.services.ai.local_ocr; "
            "print('easyocr' in sys.modules)")
    root = importlib.import_module("app").__path__[0].rsplit("/app", 1)[0]
    out = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True,
                         text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "False"
