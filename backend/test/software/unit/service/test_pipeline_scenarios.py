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
# 10-03 알려진 버그: pipeline.State 에 item_cut_off 키가 없어 LangGraph 가 analyze 결과에서 떨어뜨린다
#   → 그래프에선 cut_off 가 절대 안 나온다 (_composite_first_reason 단위로는 맞음). State 에 키를 넣으면 이 표시를 뗀다.


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
        self.compose_drop_keep = []    # compose 에 넘긴 (drop, keep) 박스 — 10-03 팔 물건 고르기
        self.seen = {"check_photo": [], "verify": [], "compose": [], "judge": []}   # 받은 이미지
        self.analysis = {"item": "chair", "considered": [], "anchors": [MARK], "item_box": None,
                         "photo_type": "product", "wear_level": "light", "watermark": "none",
                         "text_level": "none"}
        self.analyze_errors = []   # 예외를 앞에서부터 하나씩 던진다
        self.texts, self.read_box, self.read_error = [], None, None
        self.photo_checks = []     # 소진되면 valid
        self.verifies = []         # "pass" | "lost" | "short"(항목 하나 덜 답함) | 예외 — 소진되면 pass
        self.added = []            # added_text 답 — [{"what","where"}] 목록 | 예외, 소진되면 [] (10-03)
        self.added_calls = []      # added_text 에 넘긴 (원본, 생성본) — calls 순서 검사와 섞지 않으려고 따로
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
        monkeypatch.setattr(det, "added_text", self._added)
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

    def _added(self, original, result):
        self.added_calls.append((_label(original), _label(result)))
        a = self.added.pop(0) if self.added else []
        if isinstance(a, Exception):
            raise a
        return a

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
        def compose(image, bg, box=None, alpha=None, *, flat=False, drop=None, keep=None):
            self.calls.append(name)
            self.seen["compose"].append(_label(image))
            self.compose_boxes.append(box)
            self.compose_drop_keep.append((drop, keep))
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
    out = w.run(score_quality=True)          # eval · dev 경로 — 채점까지

    assert w.calls == ["analyze", "generate", "check_photo", "verify", "judge"]
    assert out["mode"] == "generate" and out["composite_reason"] is None
    assert out["gate_passed"] is True and out["gen_attempts"] == 1
    assert out["visual_similarity"] == 0.9 and out["item_similarity"] == 0.85
    assert out["guard_report"] == []
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
    assert row["bubbles"] is None                      # 말풍선 제거 — 열은 남고 NULL


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
                       "generate", "check_photo", "verify", "compose"]
    assert out["mode"] == "composite" and out["composite_reason"] == "gate_failed"
    assert out["gate_passed"] is None and out["checks"] == [] and "bubbles" not in out
    assert out["item_similarity"] is None and out["guard_report"] == []
    assert w.result() == "comp"
    assert w.seen["compose"] == ["orig"]
    ins = w.inspect()
    assert [c["preserved"] for c in ins["gate_checks"]] == [False]
    assert ins["visual_similarity"] == 0.77 and ins["guard_report"] == []   # 합성본으로 다시 잰다
    assert w.route()["mode"] == "composite"


def test_verify_answering_fewer_items_than_asked_fails_gate(w):
    """VLM 이 항목을 빠뜨리면(빈 응답 포함) 빠진 건 확인 못 한 것 — 통과가 아니다."""
    w.verifies = ["short", "short"]
    out = w.run()
    assert w.count("generate") == 2 and out["composite_reason"] == "gate_failed"


# ══ 생성 전 배경 교체 (plan · read_text) ═════════════════════════════
@pytest.mark.parametrize("analysis,reason,composer", [
    ({"text_level": "dense"}, "text_dense", "compose"),
    ({"wear_level": "heavy"}, "wear_heavy", "compose"),
    ({"photo_type": "document", "text_level": "simple"}, "document", "compose_flat"),])
def test_composite_first_skips_generate_and_verify(w, analysis, reason, composer):
    w.analysis.update(analysis)
    out = w.run()

    assert w.calls == ["analyze", composer]
    assert w.seen["compose"] == ["orig"]
    assert out["mode"] == "composite" and out["composite_reason"] == reason
    assert out["gate_passed"] is None and out["prompt_used"].startswith("COMPOSITE")
    assert w.result() == "comp"
    assert w.inspect()["composite_reason"] == reason
    assert w.route()["composite_reason"] == reason


@pytest.mark.parametrize("analysis,reason", [
    ({"item_cut_off": True}, "cut_off"),
    ({"item_cut_off": True, "text_level": "simple", "item_count": 3}, "cut_off"),   # 잘림이 먼저,
    ({"item_count": 2}, "multi_item"),])
def test_cut_off_or_multi_item_goes_composite_without_generating(w, analysis, reason):
    """10-03: 물건이 잘렸거나(cut_off) 여러 개(multi_item)면 생성하지 않고 배경만 바꾼다."""
    w.analysis.update(analysis)
    out = w.run(composition="shoes_side")
    assert w.calls == ["analyze", "compose"]
    assert w.seen["compose"] == ["orig"]
    assert out["mode"] == "composite" and out["composite_reason"] == reason
    assert out["gate_passed"] is None and out["prompt_used"].startswith("COMPOSITE")
    assert w.result() == "comp" and w.inspect()["composite_reason"] == reason
    assert w.route()["composite_reason"] == reason


def test_cut_off_from_cached_analysis(w):
    """미리 분석(/api/analyze)해 둔 결과의 item_cut_off 도 그대로 쓴다 — 한 개를 골라도 잘림이면 배경 교체."""
    _cached(w, CD_L, KEYB, count=1, item_cut_off=True)
    out = w.run(sell=[0])
    assert "analyze" not in w.calls and "generate" not in w.calls
    assert out["composite_reason"] == "cut_off"
    assert w.compose_drop_keep == [([KEYB["box"]], [CD_L["box"]])]


def test_inside_view_keeps_original_without_any_check(w):
    w.analysis.update(photo_type="inside_view", text_level="dense", wear_level="heavy")
    out = w.run(score_quality=True)         # 채점을 켜도 원본이면 안 한다

    assert w.calls == ["analyze"]          # 생성·오리기·검사·채점 없음
    assert out["mode"] == "original" and out["composite_reason"] == "inside_view"
    assert out["gate_passed"] is None
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


def test_defer_leaves_item_signals_for_after_response(w):
    """라우트 경로: 응답 때 누끼 비교는 비어 있고, 응답 뒤 item_signals_and_save 가 채운다."""
    out = w.run(defer_signals=True)
    assert out["item_signals_pending"] is True and out["item_similarity"] is None
    assert "judge" not in w.calls and w.quality() is None     # 운영 경로는 채점하지 않는다
    assert w.inspect()["item_similarity"] is None
    pipeline_mod.item_signals_and_save(FID, PRESET)
    assert w.inspect()["item_similarity"] == 0.85 and w.inspect()["item_patch_similarity"] == 0.97


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


@pytest.mark.parametrize("analysis", [
    {"text_level": "dense"}, {"wear_level": "heavy"},
    {"photo_type": "document"},])
def test_dev_never_composites_or_keeps_original(w, analysis):
    """dev 그래프는 verify 프롬프트 튜닝용 — 배경 교체로 빠지면 볼 게 없고 inspect 에 엉뚱한 사유가 남는다."""
    w.analysis.update(analysis)
    out = w.run_dev()
    assert "compose" not in w.calls and "compose_flat" not in w.calls
    assert out["mode"] == "generate" and w.inspect()["composite_reason"] is None


@pytest.mark.parametrize("count,attached", [(None, True), (1, True), (2, False),])
def test_composition_skipped_when_several_items(w, count, attached):
    """10-03: 구도 문장은 한 개 기준 — 여러 개(CD 2장)면 하나로 합쳐 버려서 붙이지 않는다."""
    from app.services.compositions import BY_KEY
    w.analysis.update(item_count=count)
    out = w.run(composition="shoes_side")
    side = BY_KEY["shoes_side"]["prompt"]
    assert all((side in p) == attached for p in w.prompts)
    assert (side in out["prompt_used"]) == attached
    # 10-03: 여러 개면 아예 생성하지 않는다 (multi_item 배경 교체)
    assert bool(w.prompts) == attached
    assert out["composite_reason"] == (None if attached else "multi_item")


# ══ 10-01: analyze 가 글자를 주면 글자 읽기 VLM 을 부르지 않는다 ═══════
def test_texts_from_analyze_skip_read_text_call(w):
    w.analysis.update(text_level="simple", item_texts=[{"text": "BRAUN"}])
    out = w.run()
    assert "read_text" not in w.calls
    assert out["mode"] == "generate" and '"BRAUN"' in w.prompts[0]


# ══ 10-03: 팔 물건 고르기 — sell · answer_count 가 analyze 결과를 바꾸고 생성 프롬프트에 실린다 ═══════
# sell 번호는 사용자가 본 분석(저장된 것)의 번호 — 저장된 분석이 있을 때만 쓴다
CD_L = {"what": "CD", "box": {"x1": 50, "y1": 100, "x2": 450, "y2": 900}, "for_sale": True}
CD_R = {"what": "CD", "box": {"x1": 550, "y1": 100, "x2": 950, "y2": 900}, "for_sale": True}
KEYB = {"what": "keyboard", "box": {"x1": 0, "y1": 0, "x2": 1000, "y2": 80}, "for_sale": False}
LEAVE = "leave out the other objects that are in the photo"   # 이름 없는 한 문장 (10-03)


def _cached(w, *objs, count=None, **extra):
    """/api/analyze 가 미리 분석해 저장해 둔 상태 — 변환 때 analyze VLM 을 부르지 않는다."""
    w.analysis.update(item="CD", objects=list(objs), item_count=count or 1, **extra)
    storage.save("quality", f"{FID}_analysis.json",
                 json.dumps({**w.analysis, "detect_failed": False}).encode())


def _multi_item_composite(w, out, drop_keep):
    """여러 개 → 생성하지 않고 원본 픽셀로 배경만 (10-03, 개수 문장은 효과가 없어 뺐다)."""
    assert out["mode"] == "composite" and out["composite_reason"] == "multi_item"
    assert "generate" not in w.calls and "verify" not in w.calls and w.prompts == []
    assert out["prompt_used"].startswith("COMPOSITE")
    assert w.compose_drop_keep == [drop_keep]
    assert w.result() == "comp" and w.route()["composite_reason"] == "multi_item"


def test_sell_ignored_when_analysis_is_fresh(w):
    """저장된 분석이 없으면 지금 분석한 목록 순서가 사용자가 본 것과 다를 수 있어 sell 을 버린다."""
    w.analysis.update(item="CD", objects=[CD_L, CD_R, KEYB], item_count=2)
    out = w.run(sell=[2])
    assert w.calls[0] == "analyze"
    _multi_item_composite(w, out, (None, None))             # 분석 판단(2개) 그대로, 뺄 것 없음


def test_sell_order_composition_then_leave_out_then_text_lock(w):
    """한 개 고르면 구도가 붙고, 순서는 구도 → (개수) → 뺄 물건 → 글자 잠금."""
    from app.services.compositions import BY_KEY
    _cached(w, CD_L, KEYB, count=2, text_level="simple", item_texts=[{"text": "BRAUN"}])
    w.run(sell=[0], composition="shoes_side")
    p = w.prompts[0]
    side = BY_KEY["shoes_side"]["prompt"]
    assert side in p and "This photo shows" not in p          # 고른 게 하나 → 개수 문장 없음
    assert p.index(side) < p.index(LEAVE) < p.index('"BRAUN"')


def test_text_only_on_unchosen_object_is_not_locked(w):
    """안 고른 키보드 위 글자는 글자 잠금에서 빠진다 — "빼라"와 "지켜라"가 같은 물건에 붙지 않게."""
    _cached(w, CD_L, KEYB, text_level="simple", item_texts=[
        {"text": "SONY", "x1": 100, "y1": 400, "x2": 300, "y2": 500},      # CD 위
        {"text": "LOGI", "x1": 500, "y1": 10, "x2": 600, "y2": 50}])       # 키보드 위
    w.run(sell=[0])
    assert '"SONY"' in w.prompts[0] and "LOGI" not in w.prompts[0]


def test_one_of_two_same_name_is_not_named_in_leave_out(w):
    """CD 2장 중 1장 — "CD 를 빼라"고 하면 남길 CD 까지 지운다. 이름으로는 빼지 않는다."""
    _cached(w, CD_L, CD_R, count=2)
    w.run(sell=[0])
    assert LEAVE not in w.prompts[0] and "This photo shows" not in w.prompts[0]


def test_selection_is_not_saved_into_cached_analysis(w):
    """고른 결과가 저장된 분석에 섞이면 다음 변환(다른 무드 · 다른 선택)이 옛 선택을 물려받는다."""
    _cached(w, CD_L, CD_R, KEYB, count=2)
    out = w.run(sell=[0])
    assert out["mode"] == "generate"
    saved = json.loads(storage.load("quality", f"{FID}_analysis.json"))
    assert not {"leave_out", "leave_out_boxes", "sell_boxes"} & set(saved) and saved["item_count"] == 2
    w.prompts.clear()
    w.calls.clear()
    out = w.run()                                        # 선택 없음 → 분석 판단(2개) 그대로
    assert "analyze" not in w.calls
    _multi_item_composite(w, out, (None, None))


def test_sell_union_box_and_drop_keep_reach_composite(w):
    """배경 교체로 가도 고른 물건들의 합집합 박스로 오리고, 안 고른 물건 박스는 지운다."""
    _cached(w, CD_L, CD_R, KEYB, wear_level="heavy")
    out = w.run(sell=[0, 1])
    assert out["mode"] == "composite" and "generate" not in w.calls
    assert w.compose_boxes == [{"x1": 50, "y1": 100, "x2": 950, "y2": 900}]
    assert w.compose_drop_keep == [([KEYB["box"]], [CD_L["box"], CD_R["box"]])]


@pytest.mark.parametrize("answer", [None, 1, 0])
def test_answer_count_one_or_ignored_generates_with_composition(w, answer):
    """eval: 고르는 화면 없이 정답 개수만 — 1개(또는 무시되는 값)면 생성 + 구도, 개수 문장 없음."""
    from app.services.compositions import BY_KEY
    out = w.run(composition="shoes_side", answer_count=answer)
    p = w.prompts[0]
    assert out["mode"] == "generate" and BY_KEY["shoes_side"]["prompt"] in p
    assert "This photo shows" not in p


# ══ 10-03: 생성본에 없던 글자 · 로고가 생기면 게이트 실패 ═══════
ADDED = [{"what": "H4", "where": "chest"}]


def test_added_text_fails_gate_then_regenerates_without_naming_it(w):
    w.added = [ADDED, []]
    out = w.run()
    assert w.count("generate") == 2 and out["mode"] == "generate" and out["gate_passed"] is True
    assert "drew marks on the product that are not in the input image" in w.prompts[1]
    assert "H4" not in w.prompts[1]                     # 생긴 글자를 프롬프트에 다시 쓰지 않는다
    assert all(o == "orig" for o, _ in w.added_calls)


def test_added_text_call_failure_does_not_block(w):
    """덧붙인 검사라 호출이 안 돼도 막지 않는다 — 마크 검사는 그대로 (10-03 리뷰)."""
    w.added = [RuntimeError("down")] * 3
    out = w.run()
    assert out["mode"] == "generate" and out["gate_passed"] is True and "verify" in w.calls




def test_verify_call_failure_goes_composite_without_waiting_added_text(w):
    """verify 호출이 실패하면 이미 실패 — 병렬 added_text 결과는 기다리지도 쓰지도 않는다 (10-04)."""
    w.verifies = [TypeError("bug")] * 4
    w.added = [ADDED] * 4
    out = w.run()
    assert out["mode"] == "composite" and out["composite_reason"] == "verify_failed"
    assert w.count("generate") == 1 and not out.get("added_text")


# ══ 10-05: verify + added_text 한 호출 (settings.verify_combined) ═══════
@pytest.fixture()
def combined(w, monkeypatch):
    """verify_combined 를 World 의 verify · added 가짜를 합쳐 흉내 — 따로 부르는 두 함수는 안 불려야 한다."""
    monkeypatch.setattr(settings, "verify_combined", True)
    w.combined_calls = 0

    def fake(original, result, targets, item="object"):
        w.combined_calls += 1
        checks = w._verify(result, targets, item)
        a = w.added.pop(0) if w.added else []
        return checks, a
    monkeypatch.setattr(pipeline_mod.detector, "verify_combined", fake)
    return w


def test_combined_added_fails_gate_then_regenerates_in_one_call_each(combined):
    w = combined
    w.added = [ADDED, []]
    out = w.run()
    assert w.count("generate") == 2 and out["gate_passed"] is True
    assert w.combined_calls == 2 and w.added_calls == []      # 따로 부르는 added_text 는 안 쓴다


def test_combined_added_none_does_not_block(combined):
    combined.added = [None]
    out = combined.run()
    assert out["mode"] == "generate" and out["gate_passed"] is True and combined.count("generate") == 1


def test_combined_added_ignored_when_gate_off(combined, monkeypatch):
    monkeypatch.setattr(settings, "added_text_gate", False)
    combined.added = [ADDED]
    out = combined.run()
    assert out["gate_passed"] is True and combined.count("generate") == 1 and not out.get("added_text")


def test_combined_call_failure_goes_verify_failed(combined):
    combined.verifies = [RuntimeError("down")] * 8
    out = combined.run()
    assert out["mode"] == "composite" and out["composite_reason"] == "verify_failed"
