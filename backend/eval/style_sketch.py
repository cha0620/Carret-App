"""스타일 참고 사진 → 선 그림 (10-05). 남의 상품 사진을 그대로 주면 생김새 · 글자까지 베낀다 (study 10-04 §16 ③) —
ControlNet 처럼 선만 남겨 준다 (§16 ④). 글자 · 로고(EasyOCR 상자)를 지우고, 인쇄 무늬 같은 짧은 선을 버리고,
물건 외곽선(rembg)을 굵게.

    cd backend
    python eval/style_sketch.py          # style_refs.json 전부 → data/refs/sketch/<이름>.png
"""
import io
import json
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))


def sketch(img, reader):
    img = img.copy()
    tm = np.zeros(img.shape[:2], np.uint8)
    for box, _, _ in reader.readtext(img, text_threshold=0.5, low_text=0.3):
        cv2.fillPoly(tm, [np.array(box, np.int32)], 255)
    img = cv2.inpaint(img, cv2.dilate(tm, np.ones((7, 7), np.uint8)), 7, cv2.INPAINT_TELEA)
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    e = cv2.Canny(cv2.bilateralFilter(g, 9, 60, 9), 60, 150)
    n, lab, st, _ = cv2.connectedComponentsWithStats(e)
    keep = np.zeros_like(e)
    lim = 0.06 * max(e.shape)
    for i in range(1, n):
        if max(st[i][2], st[i][3]) >= lim:
            keep[lab == i] = 255
    from rembg import remove
    alpha = np.array(Image.open(io.BytesIO(remove(cv2.imencode(".png", img)[1].tobytes()))))[:, :, 3]
    cs, _ = cv2.findContours((alpha > 128).astype(np.uint8) * 255, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    out = np.full_like(g, 255)
    out[keep > 0] = 0
    cv2.drawContours(out, cs, -1, 0, max(2, img.shape[0] // 300))
    return out


def main():
    from app.services.ai import local_ocr
    reader = local_ocr._load()
    data = HERE / "data"
    (data / "refs" / "sketch").mkdir(exist_ok=True)
    for e in json.loads((data / "style_refs.json").read_text(encoding="utf-8")):
        src = data / "refs" / e["file"]
        img = cv2.imread(str(src))
        if img is None:
            print(f"없음: {src}"); continue
        dst = data / "refs" / "sketch" / (Path(e["file"]).stem + ".png")
        cv2.imwrite(str(dst), sketch(img, reader))
        print(dst.name)


if __name__ == "__main__":
    main()
