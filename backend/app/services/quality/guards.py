"""출력 가드 - 생성 결과가 "이건 무조건 지켜야 한다"는 최소선을 기계적으로
재는 층. judge()(fidelity/realism/trust)는 VLM 의 "판단"이라 프롬프트/모델
버전에 따라 흔들릴 수 있는 미학 신호인 반면, 여기는 결정론적/저비용 신호
(임베딩 유사도, OCR 텍스트 매칭)로 진실(가드)과 미학(judge)을 분리한다.

severity:
  hard - 하나라도 실패하면 전체 결과를 block (사람에게 승격)
  soft - 실패해도 통과, 관측용 신호일 뿐

주의(의도적 설계): 아래 가드 계산 자체는 detector.py/pipeline.py 전역에 깔린
"실패(무시)" 컨벤션을 따르지 않는다 — 그 컨벤션은 이미 끝난(비용 든) 생성을
관측 신호 하나 때문에 되돌리지 않으려는 것이지만, 여기는 반대로 "무조건
지켜야 하는 최소선"을 재는 gate라서 계산 자체가 실패하면 fail-open(=자동
pass) 시키는 게 오히려 위험하다. 그래서 embedder/metric 호출 예외는 여기서
삼키지 않고 그대로 올린다 - 호출부가 알아서 처리(재시도/차단)하게 둔다.
"""
from dataclasses import dataclass

from app.core.tracing import score
from app.services.ai import embedder
from app.services.quality import metric

OCR_MATCH_THRESHOLD = 0.95
DINO_BAND = (0.75, 0.995)
# 누끼 딴 물건끼리의 DINO 유사도 하한. 경험값 없음 — soft 로 값만 모으고 eval 로 정한다.
ITEM_DINO_THRESHOLD = 0.80
# 누끼 쌍의 DINO 패치 유사도(물건 안쪽 하위 1%) 하한. 합성 테스트(주전자 누끼 1장, 2026-09-26):
# 조명 변화·이동 0.975~0.988, 흠집 한 줄 추가 0.92 — 실제 생성본 분포는 모른다. soft 로 값만 모은다.
ITEM_PATCH_THRESHOLD = 0.95


@dataclass
class GuardResult:
    name: str
    passed: bool
    value: float
    threshold: float
    severity: str  # "hard" | "soft"


def run_output_guards(orig: bytes, result: bytes,
                       ocr_before: list[str], ocr_after: list[str]) -> list[GuardResult]:
    """hard 판정에 쓰는 가드 전부 (+ 이미지 전체 dino_band). 누끼 비교는 item_guard 로 따로 —
    판정이 누끼(수 초)를 기다리지 않게."""
    guards: list[GuardResult] = []

    # 줄 단위·순서 무관 비교 — VLM 이 같은 글자를 다른 순서로 읽어도 깎이지 않고,
    # 줄 하나가 뭉개지면(NIKE→NlKE) 그 줄 점수만큼 recall 이 떨어진다.
    m = metric.text_match(ocr_before, ocr_after)
    ocr_ratio = m["recall"]
    guards.append(GuardResult(
        name="ocr_match", passed=ocr_ratio >= OCR_MATCH_THRESHOLD,
        value=ocr_ratio, threshold=OCR_MATCH_THRESHOLD, severity="hard"))

    # 원본 어느 줄과도 안 닮은 새 글자. soft(관측용): 두 번의 VLM 읽기가 줄을 다르게
    # 쪼개기만 해도("NIKE AIR" → "NIKE" + "AIR") 생겨서 hard 로 두면 오차단이 잦다.
    # 새로 얹힌 자막·워터마크는 check_photo(구도/오버레이 검사)가 따로 잡는다.
    added = m["added"]
    guards.append(GuardResult(
        name="no_added_text", passed=len(added) == 0,
        value=float(len(added)), threshold=0.0, severity="soft"))

    # dino_band 는 soft(관측용) — 계산 실패(모델 로드/메모리)로 이미 비용 든 변환을
    # 500 으로 날리지 않는다. hard 가드의 "예외는 삼키지 않는다" 원칙은 그대로.
    lo, hi = DINO_BAND
    try:
        dino = embedder.cosine_similarity(orig, result)
        guards.append(GuardResult(
            name="dino_band", passed=lo <= dino <= hi,
            value=dino, threshold=lo, severity="soft"))
    except Exception as e:
        print(f"[guards] dino_band 계산 실패(soft, 무시): {e}")

    for g in guards:
        score(g.name, g.value, data_type="NUMERIC")   # 키 없으면 noop (tracing.py 컨벤션)

    return guards


def item_guard(pair: tuple[bytes, bytes] | None) -> GuardResult | None:
    """item_dino — 원본·결과에서 물건만 오려(compositor.isolate) 비교한 DINO 유사도. soft(관측용).

    물건이 통째로 바뀌거나 형태·색·무늬가 달라진 걸 잡는다. 작은 하자(흠집 하나)는 임베딩을
    거의 못 움직여 여기서 못 잡는다 — 그건 verify(Wear Gate) 몫.
    pair 가 없거나(오리기 실패) 계산이 실패하면 None — 이미 비용 든 생성을 이것 때문에 막지 않는다."""
    if pair is None:
        return None
    try:
        sim = embedder.cosine_similarity(*pair, name="item_dino_similarity")
    except Exception as e:
        print(f"[guards] item_dino 계산 실패(soft, 무시): {e}")
        return None
    g = GuardResult(name="item_dino", passed=sim >= ITEM_DINO_THRESHOLD,
                    value=sim, threshold=ITEM_DINO_THRESHOLD, severity="soft")
    score(g.name, g.value, data_type="NUMERIC")
    return g


def item_patch_guard(pair: tuple[bytes, bytes] | None) -> GuardResult | None:
    """item_patch — 같은 누끼 쌍을 DINO 패치 단위로 비교한 국소 유사도 (embedder.patch_similarity).
    soft(관측용). item_dino(CLS 하나)가 못 보는 "한 군데만 바뀜"(흠집·글자 지워짐)을 보려는 값.
    실패하면 None — item_guard 와 같은 이유."""
    if pair is None:
        return None
    try:
        sim = embedder.patch_similarity(*pair)
    except Exception as e:
        print(f"[guards] item_patch 계산 실패(soft, 무시): {e}")
        return None
    g = GuardResult(name="item_patch", passed=sim >= ITEM_PATCH_THRESHOLD,
                    value=sim, threshold=ITEM_PATCH_THRESHOLD, severity="soft")
    score(g.name, g.value, data_type="NUMERIC")
    return g


def local_ocr_guard(before: list[str], after: list[str]) -> GuardResult | None:
    """ocr_local — 로컬 OCR(EasyOCR)로 읽은 원본·결과 글자의 줄 단위 recall. soft(관측용).
    VLM 두 번 읽기(ocr_match)와 얼마나 어긋나는지 모으려는 값. 원본에서 읽은 게 없으면 None."""
    if not before:
        return None
    recall = metric.text_match(before, after)["recall"]
    g = GuardResult(name="ocr_local", passed=recall >= OCR_MATCH_THRESHOLD,
                    value=recall, threshold=OCR_MATCH_THRESHOLD, severity="soft")
    score(g.name, g.value, data_type="NUMERIC")
    return g


def decide(guards: list[GuardResult]) -> tuple[str, list[GuardResult]]:
    """hard 가드가 하나라도 실패하면 block(+실패 목록), 아니면 pass(+가드 전체)."""
    hard_fails = [g for g in guards if g.severity == "hard" and not g.passed]
    if hard_fails:
        return "block", hard_fails
    return "pass", guards
