"""기능 평가 - AI 행동 계측 (유료, nightly).

실행: pytest backend/test -m eval
지표: item_acc / recall / precision / considered 성실성
"""
import json
from pathlib import Path

import pytest

from app.services import detector

pytestmark = pytest.mark.eval

HERE = Path(__file__).parent
DATASET = json.loads((HERE / "dataset.json").read_text(encoding="utf-8"))


def _norm(s: str) -> str:
    return s.strip().lower()


def _match(a: str, b: str) -> bool:
    a, b = _norm(a), _norm(b)
    return a in b or b in a          # 부분 포함 허용 (hoodie⊂hooded 등)


import time

@pytest.fixture(scope="module")
def report():
    rows = []
    for i, e in enumerate(DATASET):
        img = (HERE / "images" / e["file"]).read_bytes()
        cls = detector.classify(img)
        anchors = detector.detect_defects(
            img, cls["item"], cls["considered"])
        rows.append({"gt": e, "cls": cls, "anchors": anchors})
    return rows


def test_item_accuracy(report):
    ok = sum(_match(r["cls"]["item"], r["gt"]["item"]) for r in report)
    acc = ok / len(report)
    print(f"\n[item_acc] {ok}/{len(report)} = {acc:.2f}")
    assert acc >= 0.8, "식별 정확도 회귀"


def test_detect_recall_precision(report):
    tp = fp = fn = 0
    for r in report:
        gts   = set(r["gt"]["defects"])                  # 정답 종류
        cats  = {a["category"] for a in r["anchors"]}    # 검출 종류 (중복 자동 제거)

        hit   = gts & cats      # 맞힌 것
        miss  = gts - cats      # 놓친 것
        extra = cats - gts      # 오검출

        print(f'{r["gt"]["file"]}: miss={sorted(miss)} got={sorted(cats)} extra={sorted(extra)}')

        tp += len(hit)
        fn += len(miss)
        fp += len(extra)

    recall = tp / (tp + fn) if (tp + fn) else 1.0
    prec   = tp / (tp + fp) if (tp + fp) else 1.0
    print(f"\n[recall] {recall:.2f}  [precision] {prec:.2f}")
    print(f"[counts] tp={tp} fp={fp} fn={fn}")
    assert recall >= 0.8, "검출 재현율 회귀"
    assert prec >= 0.8, "검출 정밀도 회귀"


def test_considered_sanity(report):
    ok = sum(len(r["cls"]["considered"]) >= 3 for r in report)
    rate = ok / len(report)
    print(f"\n[considered>=3] {rate:.2f}")
    assert rate >= 0.9, "루브릭 성실성 회귀"