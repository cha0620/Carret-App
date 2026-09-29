"""app.services.pipeline — 경로 시나리오 (그래프 끝까지).

run_transform / run_transform_with_result 를 실제 그래프로 돌리고, 바깥(VLM·생성·누끼·DINO·채점)만
가짜로 바꾼다. 각 시나리오는 "이런 사진·이런 응답이면 → 어느 경로로 가서 무엇이 남나"를 본다:
돌려준 결과 · inspect JSON · 저장된 결과 이미지(색) · DB 경로 · 부른 외부 호출 순서.

노드 하나하나의 분기는 여기서 따로 보지 않는다 — 경로가 맞으면 노드도 맞다.
순수 규칙(생성 전 판단 표 · verify 에 걸 글자 고르기)은 test_pipeline_rules.py,
동시성·채점 경합·트레이싱은 test_pipeline_runtime.py.
"""
import io
import json
import types

import pytest
from PIL import Image

import app.core.tracing as tracing
import app.services.pipeline as pipeline_mod
from app.core.config import settings
from app.services.persistence import storage, store

ORIG, COMP = (120, 90, 60), (9, 9, 9)
GENS = [(250, 250, 250), (250, 200, 150), (250, 150, 50), (200, 250, 100), (150, 250, 200),
        (100, 200, 250)]     # 생성 n 번째 결과 색 — 어느 시도의 이미지가 남았는지 구분
GEN = GENS[0]
FID, PRESET = "fid", "studio_white"
MARK = {"category": "print", "what": "logo: ACME", "where": "front"}
BOX_A = {"x1": 100, "y1": 100, "x2": 900, "y2": 900}
BOX_B = {"x1": 200, "y1": 200, "x2": 800, "y2": 800}


def _png(color):
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), color).save(buf, format="PNG")
    return buf.getvalue()


def _color(image_bytes):
    return Image.open(io.BytesIO(image_bytes)).convert("RGB").resize((1, 1)).getpixel((0, 0))


def _close(c, ref, tol=3):
    return all(abs(a - b) <= tol for a, b in zip(c, ref))


def _label(image_bytes):
    """이미지 → "orig" | "gen1".."gen6" | "comp" — 가짜가 무엇을 받았는지 기록할 때."""
    c = _color(image_bytes)
    known = [("orig", ORIG), ("comp", COMP)] + [(f"gen{i + 1}", g) for i, g in enumerate(GENS)]
    return next((name for name, ref in known if _close(c, ref)), f"?{c}")


def _lines(n):
    return [{"text": f"LINE{i:02d}"} for i in range(n)]


class World:
    """바깥 세계. 속성을 바꿔 시나리오를 만든다 — 목록은 앞에서부터 한 번씩 쓰고, 다 쓰면 기본값."""

    def __init__(self, monkeypatch):
        self.calls = []            # 외부 호출 순서
        self.prompts = []          # generate 에 넘긴 프롬프트
        self.targets = []          # verify 에 넘긴 체크리스트
        self.compose_boxes = []
        self.seen = {"check_photo": [], "verify": [], "compose": [], "judge": []}   # 받은 이미지
        self.analysis = {"item": "chair", "considered": [], "anchors": [MARK], "item_box": None,
                         "photo_type": "product", "wear_level": "light", "watermark": "none",
                         "text_level": "none"}
        self.analyze_errors = []   # 예외를 앞에서부터 하나씩 던진다
        self.texts, self.read_box, self.read_error = [], None, None
        self.photo_checks = []     # 소진되면 valid
        self.verifies = []         # "pass" | "lost" | "short"(항목 하나 덜 답함) | 예외 — 소진되면 pass
        self.compose_error = None
        self.similarity = 0.9          # 원본 vs 생성본 (이미지 전체)
        self.comp_similarity = 0.77    # 원본 vs 합성본
        self.item_similarity = 0.85    # 누끼 쌍 (원본 누끼, 결과 누끼)
        self.isolate_error = None
        storage.save("original", f"{FID}.png", _png(ORIG))

        det = pipeline_mod.detector
        monkeypatch.setattr(det, "analyze", self._analyze)
        monkeypatch.setattr(det, "read_item_text", self._read)
        monkeypatch.setattr(det, "check_photo", self._check_photo)
        monkeypatch.setattr(det, "verify_and_locate", self._verify)
        monkeypatch.setattr(pipeline_mod, "_generate_ai", self._generate)
        comp = pipeline_mod.compositor
        monkeypatch.setattr(comp, "compose", self._compose("compose"))
        monkeypatch.setattr(comp, "compose_flat", self._compose("compose_flat"))
        monkeypatch.setattr(comp, "isolate", self._isolate)
        monkeypatch.setattr(pipeline_mod.embedder, "cosine_similarity", self._cosine)
        monkeypatch.setattr(pipeline_mod.embedder, "patch_similarity",
                            lambda o, r, bg=(128, 128, 128): 0.97)
        monkeypatch.setattr(pipeline_mod.judge, "judge", self._judge)

    # ── 가짜 ──
    def _analyze(self, img):
        self.calls.append("analyze")
        if self.analyze_errors:
            raise self.analyze_errors.pop(0)
        return dict(self.analysis)

    def _read(self, img, item="object", *, strict=False):
        self.calls.append("read_text")
        if self.read_error:
            raise self.read_error
        return {"texts": self.texts, "item_box": self.read_box}

    def _check_photo(self, img):
        self.calls.append("check_photo")
        self.seen["check_photo"].append(_label(img))
        return self.photo_checks.pop(0) if self.photo_checks else {"valid": True, "reason": ""}

    def _verify(self, img, targets, item="object", considered=None, *, strict=False, marks=False):
        self.calls.append("verify")
        self.seen["verify"].append(_label(img))
        self.targets.append(targets)
        v = self.verifies.pop(0) if self.verifies else "pass"
        if isinstance(v, Exception):
            raise v
        answered = targets[:-1] if v == "short" else targets
        return [{"what": t["what"], "preserved": v != "lost", **BOX_A} for t in answered]

    def _generate(self, original, preset, seed=None):
        self.calls.append("generate")
        self.prompts.append(preset["prompt"])
        return _png(GENS[self.count("generate") - 1])

    def _compose(self, name):
        def compose(image, bg, box=None, alpha=None):
            self.calls.append(name)
            self.seen["compose"].append(_label(image))
            self.compose_boxes.append(box)
            if self.compose_error:
                raise self.compose_error
            return _png(COMP)
        return compose

    def _isolate(self, image, box=None, original=False):
        if self.isolate_error:
            raise self.isolate_error
        return b"ISO_original" if original else b"ISO_result"

    def _cosine(self, o, r, name="dino_similarity"):
        if (o, r) == (b"ISO_original", b"ISO_result"):
            return self.item_similarity
        if _label(o) != "orig":
            return -1.0                          # 원본이 아닌 것과 비교 — 값으로 드러나게
        return self.comp_similarity if _label(r) == "comp" else self.similarity

    def _judge(self, orig, result):
        self.calls.append("judge")
        self.seen["judge"].append((_label(orig), _label(result)))
        return {"analysis": "ok", "fidelity": 5, "realism": 5, "trust": 5}

    # ── 실행·관찰 ──
    def run(self, **kw):
        return pipeline_mod.run_transform(FID, PRESET, **kw)

    def run_dev(self, provided=None):
        return pipeline_mod.run_transform_with_result(FID, PRESET, provided or _png(GEN))

    def inspect(self):
        return json.loads(storage.load("quality", f"{FID}_{PRESET}_inspect.json"))

    def result(self):
        return _label(storage.load("result", f"{FID}_{PRESET}.jpg"))

    def quality(self):
        return storage.load("quality", f"{FID}_{PRESET}.json")

    def route(self):
        row = store.get_result(FID, PRESET)
        return {k: row[k] for k in ("mode", "composite_reason", "photo_type", "wear_level")}

    def row(self):
        return store.get_result(FID, PRESET)

    def count(self, name):
        return self.calls.count(name)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setattr(tracing, "_client", None, raising=False)
    monkeypatch.setattr(tracing, "_disabled", True, raising=False)
    monkeypatch.setattr(settings, "pipeline_mode", "real", raising=False)
    monkeypatch.setattr(settings, "text_lock", True)
    monkeypatch.setattr(settings, "composite_first_min_texts", 12, raising=False)
    monkeypatch.setattr(settings, "max_generate_attempts", 2)
    monkeypatch.setattr(settings, "local_ocr_guard", False)
    import time as _time
    monkeypatch.setattr(pipeline_mod, "time", types.SimpleNamespace(
        time=_time.time, monotonic=_time.monotonic, sleep=lambda s: None))


@pytest.fixture()
def w(monkeypatch):
    return World(monkeypatch)


# ══ 생성 경로 (product) ═════════════════════════════════════════════
def test_product_without_text_generates_verifies_and_judges(w):
    out = w.run()

    assert w.calls == ["analyze", "generate", "check_photo", "verify", "judge"]
    assert out["mode"] == "generate" and out["composite_reason"] is None
    assert out["gate_passed"] is True and out["gen_attempts"] == 1
    assert [b["label"] for b in out["bubbles"]] == ["🏷️  ACME"]
    assert out["visual_similarity"] == 0.9 and out["item_similarity"] == 0.85
    assert out["guard_report"] == [] and out["judge_pending"] is False
    assert w.result() == "gen1" and w.quality() is not None
    # 검사는 전부 생성본을, 채점은 (원본, 생성본)을 본다
    assert w.seen == {"check_photo": ["gen1"], "verify": ["gen1"], "compose": [],
                      "judge": [("orig", "gen1")]}
    ins = w.inspect()
    assert ins["mode"] == "generate" and ins["item_patch_similarity"] == 0.97
    assert ins["anchors"] == [MARK] and ins["photo_type"] == "product"
    assert w.route() == {"mode": "generate", "composite_reason": None,
                         "photo_type": "product", "wear_level": "light"}
    row = w.row()
    assert row["item"] == "chair" and row["gate_passed"] == 1
    assert json.loads(row["bubbles"])[0]["what"] == MARK["what"]


def test_product_with_simple_text_reads_it_locks_prompt_and_verifies_it(w):
    """글자는 생성 프롬프트에 넣고(TEXT_LOCK) verify 체크리스트에도 건다. 원본에서도 못 읽은 줄("?")은 둘 다 뺀다."""
    w.analysis["text_level"] = "simple"
    w.texts = [{"text": "SONATA", **BOX_B}, {"text": "17? 5433"}]

    out = w.run()

    assert w.calls[:3] == ["analyze", "read_text", "generate"]
    assert '"SONATA"' in w.prompts[0] and "17?" not in w.prompts[0]
    whats = [t["what"] for t in w.targets[0]]
    assert whats == [MARK["what"], 'text: "SONATA"']
    assert out["mode"] == "generate" and out["gate_passed"] is True
    assert [t["text"] for t in w.inspect()["item_texts"]] == ["SONATA", "17? 5433"]


@pytest.mark.parametrize("lock,level", [(False, "simple"), (True, "none")])
def test_text_is_not_read_when_lock_off_or_no_text(w, monkeypatch, lock, level):
    monkeypatch.setattr(settings, "text_lock", lock)
    w.analysis["text_level"] = level
    w.run()
    assert "read_text" not in w.calls and w.count("generate") == 1


def test_read_text_failure_generates_without_text(w):
    w.analysis["text_level"] = "simple"
    w.read_error = RuntimeError("vlm down")
    w.texts = [{"text": "SONATA"}]      # 읽기가 실패했으니 쓰이면 안 된다
    out = w.run()
    assert w.calls[:3] == ["analyze", "read_text", "generate"]
    assert "SONATA" not in w.prompts[0]
    assert out["mode"] == "generate" and w.inspect()["item_texts"] == []


def test_soft_guard_below_band_is_reported_but_never_blocks(w):
    w.similarity, w.item_similarity = 0.4, 0.4     # dino_band·item_dino 둘 다 기준 밖
    out = w.run()
    assert out["mode"] == "generate" and out["gate_passed"] is True and w.count("generate") == 1
    assert {g["name"] for g in out["guard_report"]} == {"dino_band", "item_dino"}
    assert all(g["severity"] == "soft" for g in out["guard_report"])
    assert w.inspect()["guard_report"] == out["guard_report"]


def test_cutout_failure_only_drops_item_similarity(w):
    w.isolate_error = RuntimeError("rembg")
    out = w.run()
    assert out["mode"] == "generate" and out["item_similarity"] is None
    assert out["visual_similarity"] == 0.9
    assert w.inspect()["item_patch_similarity"] is None


def test_local_ocr_guard_on_reads_original_box_and_whole_result(w, monkeypatch):
    from app.services.ai import local_ocr
    monkeypatch.setattr(settings, "local_ocr_guard", True)
    seen = []
    monkeypatch.setattr(local_ocr, "read_original", lambda img, box: seen.append(box) or ["SONATA"])
    monkeypatch.setattr(local_ocr, "read_lines", lambda img: [])
    w.analysis.update(text_level="simple", item_box=BOX_A)
    w.texts = [{"text": "SONATA"}]

    out = w.run()

    assert seen == [BOX_A]
    assert w.inspect()["ocr_local_recall"] == 0.0
    assert [g["name"] for g in out["guard_report"]] == ["ocr_local"]


def test_embedder_failure_leaves_similarity_empty_and_does_not_block(w, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("DINO OOM")
    monkeypatch.setattr(pipeline_mod.embedder, "cosine_similarity", boom)
    out = w.run()
    assert out["mode"] == "generate" and out["gate_passed"] is True
    assert out["visual_similarity"] is None and out["item_similarity"] is None


def test_nothing_to_verify_skips_vlm_and_leaves_gate_open(w):
    w.analysis["anchors"] = []
    out = w.run()
    assert "verify" not in w.calls and out["gate_passed"] is None and out["mode"] == "generate"


# ══ 생성 후 검사 → 재생성 ══════════════════════════════════════════
def test_invalid_photo_regenerates_with_reason_then_passes(w):
    w.photo_checks = [{"valid": False, "reason": "잘림"}]
    out = w.run()
    assert w.count("generate") == 2 and w.count("verify") == 1
    assert w.seen["check_photo"] == ["gen1", "gen2"] and w.seen["verify"] == ["gen2"]
    assert w.result() == "gen2"
    assert "잘림" not in w.prompts[0] and '"잘림"' in w.prompts[1]
    assert out["gen_attempts"] == 2 and out["photo_check"]["valid"] is True


def test_invalid_photo_stops_at_max_attempts_and_keeps_last(w):
    w.photo_checks = [{"valid": False, "reason": "잘림"}] * 5
    out = w.run()
    assert w.count("generate") == settings.max_generate_attempts == 2
    assert out["mode"] == "generate" and out["photo_check"]["valid"] is False
    assert w.result() == "gen2" and w.seen["verify"] == ["gen2"]


def test_invalid_photo_without_reason_uses_default_note(w):
    w.photo_checks = [{"valid": False, "reason": ""}]
    w.run()
    assert "rejected for this reason" in w.prompts[1] and "구도가 잘렸거나" in w.prompts[1]


def test_gate_fail_once_regenerates_with_lost_marks_then_passes(w):
    w.verifies = ["lost"]
    out = w.run()
    assert w.count("generate") == 2 and w.count("verify") == 2
    assert "lost or altered" not in w.prompts[0]
    assert "lost or altered these marks" in w.prompts[1] and "ACME" in w.prompts[1]
    assert w.seen["verify"] == ["gen1", "gen2"] and w.result() == "gen2"
    assert out["mode"] == "generate" and out["gate_passed"] is True
    assert w.inspect()["gate_retried"] is True


def test_gate_fail_twice_switches_to_composite_and_keeps_what_was_lost(w):
    w.verifies = ["lost", "lost"]
    w.similarity = 0.4       # 생성본 가드 기록이 합성본으로 넘어가지 않는지 보려고
    out = w.run()

    assert w.calls == ["analyze", "generate", "check_photo", "verify",
                       "generate", "check_photo", "verify", "compose", "judge"]
    assert out["mode"] == "composite" and out["composite_reason"] == "gate_failed"
    assert out["gate_passed"] is None and out["checks"] == [] and out["bubbles"] == []
    assert out["item_similarity"] is None and out["guard_report"] == []
    assert w.result() == "comp"
    assert w.seen["compose"] == ["orig"] and w.seen["judge"] == [("orig", "comp")]
    ins = w.inspect()
    assert [c["preserved"] for c in ins["gate_checks"]] == [False]
    assert ins["visual_similarity"] == 0.77 and ins["guard_report"] == []   # 합성본으로 다시 잰다
    assert w.route()["mode"] == "composite"


@pytest.mark.parametrize("errors,verify_calls", [
    ([ValueError("bad json"), ValueError("bad json")], 2),   # 재시도할 만한 실패 — 2회
    ([TypeError("bug")], 1),                                  # 다시 해도 같다 — 1회
])
def test_verify_call_failure_goes_composite_without_regenerating(w, errors, verify_calls):
    w.verifies = errors
    out = w.run()
    assert w.count("generate") == 1 and w.count("verify") == verify_calls
    assert out["mode"] == "composite" and out["composite_reason"] == "verify_failed"
    assert out["verify_failed"] is True and w.inspect()["verify_failed"] is True


def test_verify_first_call_fails_then_succeeds_takes_normal_path(w):
    w.verifies = [ValueError("429"), "pass"]
    out = w.run()
    assert w.count("verify") == 2 and out["mode"] == "generate" and out["gate_passed"] is True


def test_gate_fail_twice_and_cutout_fails_keeps_generated_result(w):
    w.verifies = ["lost", "lost"]
    w.compose_error = ValueError("물건을 찾지 못함")
    out = w.run()
    assert out["mode"] == "composite_failed" and out["gate_passed"] is False
    assert w.result() == "gen2" and w.seen["judge"] == [("orig", "gen2")]
    assert w.inspect()["composite_error"] == "물건을 찾지 못함"


def test_verify_answering_fewer_items_than_asked_fails_gate(w):
    """VLM 이 항목을 빠뜨리면(빈 응답 포함) 빠진 건 확인 못 한 것 — 통과가 아니다."""
    w.verifies = ["short", "short"]
    out = w.run()
    assert w.count("generate") == 2 and out["composite_reason"] == "gate_failed"


def test_gate_fail_then_verify_call_fails_is_verify_failed(w):
    w.verifies = ["lost", ValueError("x"), ValueError("x")]
    out = w.run()
    assert w.count("generate") == 2
    assert out["composite_reason"] == "verify_failed"


# ══ 생성 전 배경 교체 (plan · read_text) ═════════════════════════════
@pytest.mark.parametrize("analysis,reason,composer", [
    ({"text_level": "dense"}, "text_dense", "compose"),
    ({"wear_level": "heavy"}, "wear_heavy", "compose"),
    ({"photo_type": "document", "text_level": "simple"}, "document", "compose_flat"),
    # 찢김·접힘이 넓은 문서는 펴지 않는다 — 네 모서리에 맞추면 상태가 좋아 보인다
    ({"photo_type": "document", "wear_level": "heavy"}, "document", "compose"),
])
def test_composite_first_skips_generate_and_verify(w, analysis, reason, composer):
    w.analysis.update(analysis)
    out = w.run()

    assert w.calls == ["analyze", composer, "judge"]
    assert w.seen["compose"] == ["orig"] and w.seen["judge"] == [("orig", "comp")]
    assert out["mode"] == "composite" and out["composite_reason"] == reason
    assert out["gate_passed"] is None and out["prompt_used"].startswith("COMPOSITE")
    assert w.result() == "comp"
    assert w.inspect()["composite_reason"] == reason
    assert w.route()["composite_reason"] == reason


@pytest.mark.parametrize("n,min_texts,expected", [
    (11, 12, "generate"), (12, 12, "composite"),
    (40, 0, "generate"),          # 0 = 이 안전망 끔
])
def test_many_text_lines_after_reading_go_composite(w, monkeypatch, n, min_texts, expected):
    monkeypatch.setattr(settings, "composite_first_min_texts", min_texts)
    w.analysis["text_level"] = "simple"
    w.texts = _lines(n)
    out = w.run()
    assert out["mode"] == expected
    if expected == "composite":
        assert out["composite_reason"] == "text_heavy" and "generate" not in w.calls


@pytest.mark.parametrize("analysis_box,read_box,expected", [
    (BOX_A, BOX_B, BOX_A),      # analyze 가 준 위치가 우선
    (None, BOX_B, BOX_B),       # 없을 때만 read_text 값
])
def test_item_box_priority_reaches_compose(w, analysis_box, read_box, expected):
    w.analysis.update(text_level="simple", item_box=analysis_box)
    w.texts, w.read_box = _lines(12), read_box
    w.run()
    assert w.compose_boxes == [expected]


def test_inside_view_keeps_original_without_any_check(w):
    w.analysis.update(photo_type="inside_view", text_level="dense", wear_level="heavy")
    out = w.run()

    assert w.calls == ["analyze"]          # 생성·오리기·검사·채점 없음
    assert out["mode"] == "original" and out["composite_reason"] == "inside_view"
    assert out["gate_passed"] is None and out["judge_pending"] is False
    assert out["prompt_used"].startswith("ORIGINAL")
    assert w.result() == "orig" and w.quality() is None
    assert w.route() == {"mode": "original", "composite_reason": "inside_view",
                         "photo_type": "inside_view", "wear_level": "heavy"}


# ══ analyze 실패 = "지킬 것 없음"이 아니다 ═════════════════════════════
@pytest.mark.parametrize("errors,analyze_calls", [
    ([ValueError("bad json"), ValueError("bad json")], 2),
    ([TypeError("bug")], 1),
])
def test_analyze_failure_goes_composite_detect_failed(w, errors, analyze_calls):
    w.analyze_errors = errors
    out = w.run()
    assert w.count("analyze") == analyze_calls and "generate" not in w.calls
    assert out["mode"] == "composite" and out["composite_reason"] == "detect_failed"
    assert out["detect_failed"] is True and out["photo_type"] is None
    assert w.inspect()["photo_type"] is None


def test_analyze_retry_success_takes_normal_path(w):
    w.analyze_errors = [ValueError("429")]
    out = w.run()
    assert w.count("analyze") == 2 and out["mode"] == "generate" and out["detect_failed"] is False


# ══ 생성 전 오리기 실패 ══════════════════════════════════════════════
def test_dense_cutout_failure_reads_text_once_then_generates_with_lock(w):
    """dense 로 곧장 왔으면 글자를 아직 안 읽었다 — 읽지 않고 생성하면 글자 보호가 통째로 빠진다."""
    w.analysis["text_level"] = "dense"
    w.compose_error = ValueError("no item")
    w.texts = [{"text": "SONATA"}]
    out = w.run()

    assert w.calls[:4] == ["analyze", "compose", "read_text", "generate"]
    assert w.count("read_text") == 1 and '"SONATA"' in w.prompts[0]
    assert out["mode"] == "generate" and out["composite_reason"] is None
    assert w.inspect()["composite_error"] == "no item"


def test_dense_cutout_failure_with_many_lines_does_not_loop_back(w):
    """오리기가 이미 실패했으면 12줄 이상이어도 다시 배경 교체로 보내지 않는다 (무한 왕복 방지)."""
    w.analysis["text_level"] = "dense"
    w.compose_error = ValueError("no item")
    w.texts = _lines(20)
    out = w.run()
    assert w.count("compose") == 1 and w.count("generate") == 1
    assert out["mode"] == "generate"


def test_text_heavy_cutout_failure_generates_without_reading_again(w):
    w.analysis["text_level"] = "simple"
    w.texts = _lines(12)
    w.compose_error = ValueError("no item")
    out = w.run()
    assert w.calls[:4] == ["analyze", "read_text", "compose", "generate"]
    assert w.count("read_text") == 1 and out["mode"] == "generate"


def test_detect_failed_cutout_failure_generates_but_never_passes_gate(w):
    w.analyze_errors = [TypeError("bug")]
    w.compose_error = ValueError("no item")
    out = w.run()
    assert "read_text" in w.calls and w.count("generate") == 1
    assert "verify" not in w.calls              # 확인할 기준이 없다
    assert out["mode"] == "composite_failed" and out["gate_passed"] is False
    assert w.result() == "gen1"


@pytest.mark.parametrize("analysis", [
    {"wear_level": "heavy"},
    {"wear_level": "heavy", "text_level": "simple"},
    {"photo_type": "document"},
])
def test_wear_heavy_or_document_cutout_failure_keeps_original(w, analysis):
    """생성하면 하자를 지우거나 글자를 바꾼다고 본 사진 — 오리기가 안 되면 생성 대신 원본."""
    w.analysis.update(analysis)
    w.compose_error = ValueError("no item")
    out = w.run()
    assert "generate" not in w.calls and "read_text" not in w.calls and "judge" not in w.calls
    assert out["mode"] == "original" and out["composite_reason"] in ("wear_heavy", "document")
    assert w.result() == "orig"


def test_worst_path_fits_recursion_limit(w, monkeypatch):
    """생성 전 오리기 실패 → 글자 읽기 → 구도 반려 × 한도 → 게이트 재생성 → 오리기 실패.
    시도 횟수는 게이트 재생성 뒤에도 이어서 센다 — 재생성은 한 번뿐이다 (5 + 1)."""
    monkeypatch.setattr(settings, "max_generate_attempts", 5)
    w.analysis["text_level"] = "dense"
    w.texts = [{"text": "SONATA"}]
    w.compose_error = ValueError("no item")
    w.photo_checks = [{"valid": False, "reason": "x"}] * 10
    w.verifies = ["lost", "lost"]
    out = w.run()
    assert w.count("generate") == 6 and out["mode"] == "composite_failed"


# ══ 채점 시점 · 모드 ═════════════════════════════════════════════════
def test_defer_judge_marks_pending_and_does_not_judge(w):
    out = w.run(defer_judge=True)
    assert "judge" not in w.calls and out["judge_pending"] is True and w.quality() is None


def test_defer_judge_on_original_is_not_pending(w):
    w.analysis["photo_type"] = "inside_view"
    assert w.run(defer_judge=True)["judge_pending"] is False


def test_stale_quality_report_is_removed_even_when_not_judged(w):
    storage.save("quality", f"{FID}_{PRESET}.json", b'{"old": 1}')
    w.analysis["photo_type"] = "inside_view"
    w.run()
    assert w.quality() is None


def test_record_result_failure_does_not_fail_transform(w, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("db locked")
    monkeypatch.setattr(pipeline_mod.store, "record_result", boom)
    assert w.run()["mode"] == "generate"


def test_mock_mode_passes_original_through_without_calls(w, monkeypatch):
    monkeypatch.setattr(settings, "pipeline_mode", "mock", raising=False)
    storage.save("quality", f"{FID}_{PRESET}.json", b'{"old": 1}')
    out = w.run()
    assert w.calls == [] and out["prompt_used"] == "PASS-THROUGH" and out["gate_passed"] is None
    assert w.result() == "orig" and w.quality() is None
    assert store.get_result(FID, PRESET) is not None


@pytest.mark.parametrize("mode", ["real", "mock"])
def test_missing_original_raises(monkeypatch, mode):
    monkeypatch.setattr(settings, "pipeline_mode", mode, raising=False)
    with pytest.raises(FileNotFoundError):
        pipeline_mod.run_transform("nope", PRESET)


# ══ dev 그래프: 주어진 결과로 뒷단만 ═════════════════════════════════
def test_dev_uses_provided_result_verifies_and_judges(w):
    out = w.run_dev()
    assert w.calls == ["analyze", "verify", "judge"]
    assert out["prompt_used"].startswith("TEST") and out["mode"] == "generate"
    assert out["gate_passed"] is True and w.quality() is not None
    assert w.route()["mode"] == "generate"


@pytest.mark.parametrize("analysis", [
    {"text_level": "dense"}, {"wear_level": "heavy"},
    {"photo_type": "document"}, {"photo_type": "inside_view"},
])
def test_dev_never_composites_or_keeps_original(w, analysis):
    """dev 그래프는 verify 프롬프트 튜닝용 — 배경 교체로 빠지면 볼 게 없고 inspect 에 엉뚱한 사유가 남는다."""
    w.analysis.update(analysis)
    out = w.run_dev()
    assert "compose" not in w.calls and "compose_flat" not in w.calls
    assert out["mode"] == "generate" and w.inspect()["composite_reason"] is None


def test_dev_text_heavy_does_not_composite(w):
    w.analysis["text_level"] = "simple"
    w.texts = _lines(20)
    out = w.run_dev()
    assert w.calls[:2] == ["analyze", "read_text"] and out["mode"] == "generate"


def test_dev_detect_failed_fails_gate_and_still_judges(w):
    w.analyze_errors = [TypeError("bug")]
    out = w.run_dev()
    assert "verify" not in w.calls and w.count("judge") == 1
    assert out["detect_failed"] is True and out["gate_passed"] is False
