"""dataset.json 의 url → eval/images/ 로 내려받기 (이미 있으면 건너뜀).

    cd backend && python eval/fetch.py

url 이 비어 있는 항목은 images/ 에 직접 넣은 파일로 본다. 사진은 git 에 올리지 않는다
(중고 거래 게시물 = 남의 사진, 번호판·얼굴이 찍혀 있을 수 있다).
"""
import json
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).parent
IMAGES = HERE / "images"


def main() -> int:
    IMAGES.mkdir(exist_ok=True)
    missing = 0
    for e in json.loads((HERE / "dataset.json").read_text(encoding="utf-8")):
        dst = IMAGES / e["file"]
        if dst.exists():
            continue
        if not e.get("url"):
            print(f"[없음] {e['file']} — url 이 없어 images/ 에 직접 넣어야 한다")
            missing += 1
            continue
        req = urllib.request.Request(e["url"], headers={"User-Agent": "Mozilla/5.0"})
        try:
            dst.write_bytes(urllib.request.urlopen(req, timeout=20).read())
            print(f"[ok]   {e['file']}")
        except Exception as ex:
            print(f"[실패] {e['file']}: {ex}")
            missing += 1
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
