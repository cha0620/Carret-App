"""텍스트/로고 깨짐 확인 CLI — storage/text_check/{input,output} → output/report.json.

글자 읽기는 detector.read_item_text (물건 위 글자만, 자동 교정 금지),
비교는 metric.text_match (줄 단위, 순서 무관) 에 위임하고 여기서는 찍기만 한다.

    python scripts/run_text_check.py              # 이미 있는 output/ 결과를 다시 읽기만 (생성 비용 0, VLM 호출만)
    python scripts/run_text_check.py --generate   # output/ 에 결과가 없는 사진은 fal.ai 로 생성 (비용 발생)
    python scripts/run_text_check.py --ocr        # 비교용: EasyOCR(ko+en)로도 물건 영역만 읽어 함께 기록

기존 report.json 의 vlm_compare(원본/결과 나란히 비교) 칸은 다시 계산하지 않고 유지한다.
"""
import argparse
import io
import json
import sys
import time
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from PIL import Image

from app.prompts.presets import get_preset
from app.services.ai import detector, generator
from app.services.persistence import storage
from app.services.quality import metric

ROOT = storage.BASE / "text_check"
EXTS = {".jpg", ".jpeg", ".png", ".webp"}
OCR_MATCH = 0.95   # guards.OCR_MATCH_THRESHOLD 와 같은 기준

_ocr = None


def _easyocr_lines(image_bytes: bytes, box: dict | None) -> list[str]:
    """물건 영역(box, 0-1000)만 잘라 EasyOCR — 배경 글자를 구조적으로 배제."""
    global _ocr
    if _ocr is None:
        import easyocr
        _ocr = easyocr.Reader(["ko", "en"], gpu=False, verbose=False)
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    if box:
        w, h = img.size
        img = img.crop((box["x1"] * w // 1000, box["y1"] * h // 1000,
                        box["x2"] * w // 1000, box["y2"] * h // 1000))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return [t for _, t, conf in _ocr.readtext(buf.getvalue()) if conf >= 0.3]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--preset", default="studio_white")
    ap.add_argument("--generate", action="store_true")
    ap.add_argument("--ocr", action="store_true")
    args = ap.parse_args()

    report_path = ROOT / "output" / "report.json"
    old = {}
    if report_path.exists():
        old = {r["file"]: r for r in json.loads(report_path.read_text())}

    rows = []
    for src in sorted((ROOT / "input").iterdir()):
        if src.suffix.lower() not in EXTS:
            continue
        out = ROOT / "output" / f"{src.stem}_{args.preset}.jpg"
        orig = src.read_bytes()
        prev = old.get(src.name, {})
        gen_seconds = prev.get("gen_seconds")
        if not out.exists():
            if not args.generate:
                print(f"{src.name}: 결과 없음 — 건너뜀 (--generate 로 생성)")
                continue
            t = time.time()
            out.write_bytes(generator._generate_ai(orig, get_preset(args.preset)))
            gen_seconds = round(time.time() - t, 1)
        result = out.read_bytes()

        item = detector.classify(orig)["item"]
        before = detector.read_item_text(orig, item)
        after = detector.read_item_text(result, item)
        tb = [t["text"] for t in before["texts"]]
        ta = [t["text"] for t in after["texts"]]
        m = metric.text_match(tb, ta)

        row = {
            "file": src.name,
            "item": item,
            "gen_seconds": gen_seconds,
            "result_size": list(Image.open(io.BytesIO(result)).size),
            "texts_before": tb,
            "texts_after": ta,
            "text_recall": m["recall"],
            "changed": m["changed"],
            "added_tokens": m["added"],
            "guard_ocr_match": "pass" if m["recall"] >= OCR_MATCH else "FAIL",
            "guard_no_added_text": "pass" if not m["added"] else "FAIL",
            "reader": "vlm_item_text",
            "vlm_compare": prev.get("vlm_compare"),
        }
        if args.ocr:
            ob = _easyocr_lines(orig, before["item_box"])
            oa = _easyocr_lines(result, after["item_box"])
            row["ocr"] = {"engine": "easyocr", "before": ob, "after": oa,
                          **metric.text_match(ob, oa)}
        rows.append(row)
        print(f"{src.name}: item={item} recall={m['recall']} "
              f"changed={len(m['changed'])} added={m['added']}")

    report_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2))
    print(f"→ {report_path}")


if __name__ == "__main__":
    main()
