"""원본 vs 결과: 독립 검출 → 매칭 → 좌표 출력 + recall/precision."""
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.services import detector, storage

BASE = storage.BASE / "dataset"
IMG, AFTER = BASE / "img", BASE / "after"

for orig in sorted(IMG.glob("*.png")):
    result = AFTER / f"{orig.stem}.jpg"
    if not result.exists():
        result = AFTER / f"{orig.stem}.png"
    if not result.exists():
        print(f"[skip] {orig.name}: 결과 없음")
        continue

    print(f"\n=== {orig.name} ===")
    oa = detector.detect_defects(orig.read_bytes())       # 원본: 의미만
    ra = detector.detect_with_boxes(result.read_bytes())  # 결과: 의미+좌표
    m = detector.match_anchors(oa, ra)

    rec = len(m["matched"]) / len(oa) if oa else 1.0
    pre = len(m["matched"]) / len(ra) if ra else 1.0
    print(f"  원본 {len(oa)}개 / 결과 {len(ra)}개 → recall={rec:.2f} precision={pre:.2f}")

    for o, r in m["matched"]:
        print(f"  ✅ {r['what']} @ {r['where']} [{r['x1']},{r['y1']}~{r['x2']},{r['y2']}]")
    for o in m["missed"]:
        print(f"  ❌ 사라짐: {o['what']} @ {o['where']}")
    for r in m["new"]:
        print(f"  ⚠️ 신규/환각: {r['what']} @ {r['where']}")