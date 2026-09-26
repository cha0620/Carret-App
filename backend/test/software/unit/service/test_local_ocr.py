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


def test_read_lines_keeps_order_and_duplicates(monkeypatch):
    _reader(monkeypatch, [(None, "B", 0.9), (None, "A", 0.9), (None, "B", 0.9)])
    assert local_ocr.read_lines(_img()) == ["B", "A", "B"]


def test_read_lines_nothing_read_is_empty(monkeypatch):
    _reader(monkeypatch, [])
    assert local_ocr.read_lines(_img()) == []


def test_read_lines_without_box_reads_whole_image(monkeypatch):
    r = _reader(monkeypatch)
    local_ocr.read_lines(_img(200, 100))
    assert r.sizes == [(200, 100)]


@pytest.mark.parametrize("box", [None, {}])
def test_read_lines_empty_box_is_whole_image(monkeypatch, box):
    r = _reader(monkeypatch)
    local_ocr.read_lines(_img(200, 100), box)
    assert r.sizes == [(200, 100)]


def test_read_lines_box_crops_normalized_0_1000(monkeypatch):
    r = _reader(monkeypatch)
    local_ocr.read_lines(_img(200, 100), {"x1": 250, "y1": 100, "x2": 750, "y2": 900})
    # 사방 BOX_PAD(30) 여유 → 220..780 × 70..930 → 44..156 × 7..93
    assert r.sizes == [(112, 86)]


def test_read_lines_box_floor_division(monkeypatch):
    r = _reader(monkeypatch)
    local_ocr.read_lines(_img(333, 77), {"x1": 101, "y1": 101, "x2": 899, "y2": 899})
    # 여유 뒤 71..929: 333*71//1000=23, 333*929//1000=309, 77*71//1000=5, 77*929//1000=71
    assert r.sizes == [(286, 66)]


def test_read_lines_full_box_equals_no_box(monkeypatch):
    r = _reader(monkeypatch)
    local_ocr.read_lines(_img(200, 100), {"x1": 0, "y1": 0, "x2": 1000, "y2": 1000})
    assert r.sizes == [(200, 100)]


def test_read_lines_ignores_extra_box_keys(monkeypatch):
    r = _reader(monkeypatch)
    local_ocr.read_lines(_img(200, 100), {"x1": 0, "y1": 0, "x2": 500, "y2": 500, "text": "x"})
    assert r.sizes == [(106, 53)]          # 0..530 (왼쪽·위는 0 에서 잘림)


@pytest.mark.parametrize("mode,color,fmt", [
    ("RGBA", (0, 0, 0, 0), "PNG"), ("L", 128, "PNG"), ("P", 3, "PNG"), ("RGB", (1, 2, 3), "JPEG"),
])
def test_read_lines_any_input_mode_is_sent_as_rgb_png(monkeypatch, mode, color, fmt):
    r = _reader(monkeypatch)
    assert local_ocr.read_lines(_img(mode=mode, color=color, fmt=fmt)) == ["NIKE"]
    assert r.sizes == [(200, 100)]


def test_read_lines_bad_bytes_raise_before_loading(monkeypatch):
    def no_load():
        raise AssertionError("이미지가 깨졌으면 모델을 안 띄운다")
    monkeypatch.setattr(local_ocr, "_load", no_load)
    with pytest.raises(Exception) as ei:
        local_ocr.read_lines(b"not an image")
    assert not isinstance(ei.value, AssertionError)


@pytest.mark.parametrize("box", [
    {"x1": 900, "y1": 0, "x2": 100, "y2": 1000},      # 뒤집힌 박스 (여유를 더해도 뒤집힘)
    {"x1": 0, "y1": 800, "x2": 1000, "y2": 100},
])
def test_read_lines_degenerate_box_raises(monkeypatch, box):
    """뒤집힌 박스는 예외 — 호출부(_local_ocr_check)가 삼켜 ocr_local 을 생략."""
    _reader(monkeypatch)
    with pytest.raises(Exception):
        local_ocr.read_lines(_img(), box)


def test_box_pad_constant():
    assert local_ocr.BOX_PAD == 30


def test_read_lines_zero_width_box_becomes_pad_strip(monkeypatch):
    """폭 0 박스도 사방 여유 덕에 2*BOX_PAD 폭 띠로 읽힌다 (예전엔 예외).
    detector._has_box 가 퇴화 박스를 이미 걸러 실제 호출부에선 오지 않는다."""
    r = _reader(monkeypatch)
    local_ocr.read_lines(_img(200, 100), {"x1": 500, "y1": 500, "x2": 500, "y2": 900})
    assert r.sizes == [(12, 46)]           # 470..530 × 470..930


def test_read_lines_box_pad_each_side(monkeypatch):
    r = _reader(monkeypatch)
    local_ocr.read_lines(_img(1000, 1000), {"x1": 400, "y1": 300, "x2": 600, "y2": 500})
    assert r.sizes == [(260, 260)]         # 370..630 × 270..530


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


def test_read_lines_box_pad_clamped_on_one_side_only(monkeypatch):
    r = _reader(monkeypatch)
    local_ocr.read_lines(_img(1000, 1000), {"x1": 5, "y1": 500, "x2": 100, "y2": 990})
    assert r.sizes == [(130, 530)]         # 0..130 × 470..1000


def test_read_lines_missing_box_key_raises_key_error(monkeypatch):
    _reader(monkeypatch)
    with pytest.raises(KeyError):
        local_ocr.read_lines(_img(), {"x1": 0, "y1": 0, "x2": 500})


def test_read_lines_reader_exception_propagates(monkeypatch):
    class Boom:
        def readtext(self, png):
            raise RuntimeError("ocr crashed")
    monkeypatch.setattr(local_ocr, "_load", lambda: Boom())
    with pytest.raises(RuntimeError):
        local_ocr.read_lines(_img())


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


def test_read_original_caches_same_bytes_and_box(monkeypatch):
    calls = _counting(monkeypatch)
    img = _img()
    box = {"x1": 0, "y1": 0, "x2": 500, "y2": 500}
    assert local_ocr.read_original(img, box) == ["NIKE"]
    assert local_ocr.read_original(bytes(bytearray(img)), dict(box)) == ["NIKE"]
    assert len(calls) == 1


def test_read_original_box_key_order_independent(monkeypatch):
    calls = _counting(monkeypatch)
    img = _img()
    local_ocr.read_original(img, {"x1": 0, "y1": 0, "x2": 500, "y2": 500})
    local_ocr.read_original(img, {"y2": 500, "x2": 500, "y1": 0, "x1": 0})
    assert len(calls) == 1


def test_read_original_different_box_or_bytes_is_miss(monkeypatch):
    calls = _counting(monkeypatch)
    img = _img()
    local_ocr.read_original(img, None)
    local_ocr.read_original(img, {"x1": 0, "y1": 0, "x2": 500, "y2": 500})
    local_ocr.read_original(_img(color=(0, 0, 0)), None)
    assert len(calls) == 3


def test_read_original_none_and_empty_box_share_key(monkeypatch):
    calls = _counting(monkeypatch)
    img = _img()
    local_ocr.read_original(img, None)
    local_ocr.read_original(img, {})
    assert len(calls) == 1


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


def test_read_original_empty_result_is_cached(monkeypatch):
    calls = _counting(monkeypatch, results=[])
    img = _img()
    assert local_ocr.read_original(img) == []
    assert local_ocr.read_original(img) == []
    assert len(calls) == 1


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


def test_read_original_lru_evicts_oldest(monkeypatch):
    calls = _counting(monkeypatch)
    n = local_ocr._ORIG_CACHE_MAX
    assert n == 16
    imgs = [_img(color=(i, 0, 0)) for i in range(n + 1)]
    for b in imgs[:n]:
        local_ocr.read_original(b)
    local_ocr.read_original(imgs[0])           # 0번을 최근으로 → 1번이 가장 오래됨
    assert len(calls) == n
    local_ocr.read_original(imgs[n])           # 넘침 → 1번 축출
    assert len(local_ocr._ORIG_CACHE) == n
    local_ocr.read_original(imgs[0])
    assert len(calls) == n + 1
    local_ocr.read_original(imgs[1])
    assert len(calls) == n + 2


def test_read_original_concurrent_calls_consistent(monkeypatch):
    _counting(monkeypatch)
    img = _img()
    out, errs = [], []

    def worker():
        try:
            out.append(local_ocr.read_original(img))
        except Exception as e:  # pragma: no cover
            errs.append(e)
    ts = [threading.Thread(target=worker) for _ in range(8)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert errs == [] and out == [["NIKE"]] * 8
    assert len(local_ocr._ORIG_CACHE) == 1


# ── _load: 지연 import · 싱글톤 (가짜 easyocr 모듈) ──
def _fake_easyocr(monkeypatch):
    made = []

    class Reader:
        def __init__(self, langs, **kw):
            made.append((langs, kw))
    monkeypatch.setitem(sys.modules, "easyocr", types.SimpleNamespace(Reader=Reader))
    monkeypatch.setattr(local_ocr, "_reader", None)
    return made


def test_load_builds_reader_once_with_ko_en_cpu(monkeypatch):
    made = _fake_easyocr(monkeypatch)
    r1 = _REAL_LOAD()
    r2 = _REAL_LOAD()
    assert r1 is r2
    assert made == [(["ko", "en"], {"gpu": False, "verbose": False})]


def test_load_concurrent_builds_once(monkeypatch):
    made = _fake_easyocr(monkeypatch)
    out = []
    ts = [threading.Thread(target=lambda: out.append(_REAL_LOAD())) for _ in range(8)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert len(made) == 1 and len({id(r) for r in out}) == 1


def test_load_missing_easyocr_raises_import_error(monkeypatch):
    """easyocr 는 requirements 밖 — 없으면 ImportError (가드 쪽에서 삼킨다)."""
    monkeypatch.setitem(sys.modules, "easyocr", None)
    monkeypatch.setattr(local_ocr, "_reader", None)
    with pytest.raises(ImportError):
        _REAL_LOAD()
    assert local_ocr._reader is None


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
