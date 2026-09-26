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
import io
from dataclasses import dataclass

from PIL import Image

from app.core.tracing import score
from app.services.ai import embedder
from app.services.quality import metric

FEATURE_SIM_THRESHOLD = 0.90
DEFECT_VISIBILITY_THRESHOLD = 0.85
OCR_MATCH_THRESHOLD = 0.95
DINO_BAND = (0.75, 0.995)


@dataclass
class GuardResult:
    name: str
    passed: bool
    value: float
    threshold: float
    severity: str  # "hard" | "soft"


def _crop(image_bytes: bytes, box: dict) -> bytes:
    """0-1000 정규화 좌표(detector.py 의 _box() 와 동일한 컨벤션) → 이 이미지
    자체 크기 기준 픽셀 좌표로 변환해 크롭, PNG bytes 로 재인코딩.

    원본/결과 두 이미지에 "같은" 정규화 좌표를 같은 의미 영역으로 가정한다
    (배경 교체 프리셋은 상품의 프레임 내 위치/구도를 바꾸지 않는다는 전제 —
    프리셋이 구도까지 바꾸게 되면 이 가정이 깨지는데, 그건 바로 validate_result
    가드[pipeline.py]가 별도로 잡는 문제라 여기서는 다루지 않는다)."""
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    w, h = img.size
    x1 = int(box["x1"] / 1000 * w)
    y1 = int(box["y1"] / 1000 * h)
    x2 = max(x1 + 1, int(box["x2"] / 1000 * w))
    y2 = max(y1 + 1, int(box["y2"] / 1000 * h))
    buf = io.BytesIO()
    img.crop((x1, y1, x2, y2)).save(buf, format="PNG")
    return buf.getvalue()


def defect_visibility(orig: bytes, result: bytes, box: dict) -> float:
    """결함 앵커가 결과에서도 같은 자리에 그대로 보이는가 - 크롭 간 DINO
    유사도. embedder.crop_sim 과 계산 방식은 같지만(크롭 후 cosine_similarity)
    "무엇을 재는가"의 의미와 threshold 가 달라 (feature=보존 확인, defect=하자
    가시성 확인) 별도 함수로 분리해뒀다 - 의미가 갈라지는 지점을 억지로
    하나의 함수 뒤에 숨기지 않기 위함."""
    return embedder.cosine_similarity(_crop(orig, box), _crop(result, box))


def run_output_guards(orig: bytes, result: bytes, anchors: list[dict],
                       ocr_before: list[str], ocr_after: list[str]) -> list[GuardResult]:
    """anchors 원소 형태: {"type": "feature"|"defect", "box": {x1,y1,x2,y2}}
    (0-1000 정규화 좌표, detector.py 의 checks/_box() 컨벤션과 동일).

    feature 앵커는 embedder.crop_sim(orig, result, box) 로 판정한다 - 이 함수는
    아직 embedder.py 에 없다(스코프 밖: 이 파일에서 새로 구현하지 않는다).
    feature 앵커가 있는데 embedder.crop_sim 이 없으면 AttributeError 로 바로
    터진다 - 의도된 동작이며, embedder.py 에 아래 시그니처 추가를 제안한다:

        def crop_sim(orig: bytes, result: bytes, box: dict) -> float:
            '''0-1000 정규화 box 로 원본/결과를 각각 크롭 후 DINO 코사인 유사도.'''
    """
    guards: list[GuardResult] = []

    for i, anchor in enumerate(anchors):
        box = anchor["box"]
        if anchor.get("type") == "feature":
            sim = embedder.crop_sim(orig, result, box)
            guards.append(GuardResult(
                name=f"feature_preserved[{i}]", passed=sim >= FEATURE_SIM_THRESHOLD,
                value=sim, threshold=FEATURE_SIM_THRESHOLD, severity="hard"))
        elif anchor.get("type") == "defect":
            vis = defect_visibility(orig, result, box)
            guards.append(GuardResult(
                name=f"defect_visible[{i}]", passed=vis >= DEFECT_VISIBILITY_THRESHOLD,
                value=vis, threshold=DEFECT_VISIBILITY_THRESHOLD, severity="hard"))

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


def decide(guards: list[GuardResult]) -> tuple[str, list[GuardResult]]:
    """hard 가드가 하나라도 실패하면 block(+실패 목록), 아니면 pass(+가드 전체)."""
    hard_fails = [g for g in guards if g.severity == "hard" and not g.passed]
    if hard_fails:
        return "block", hard_fails
    return "pass", guards
