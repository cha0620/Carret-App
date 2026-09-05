"""dataset.json 의 url → images/ 로 일괄 다운로드."""
import json
import urllib.request
from pathlib import Path

HERE = Path(__file__).parent
IMG = HERE / "images"
IMG.mkdir(parents=True, exist_ok=True)

for e in json.loads((HERE / "dataset.json").read_text(encoding="utf-8")):
    if not e.get("url"):
        print(f"[skip] {e['file']} (url 없음)")
        continue
    req = urllib.request.Request(e["url"],
                                 headers={"User-Agent": "Mozilla/5.0"})
    try:
        data = urllib.request.urlopen(req, timeout=15).read()
        (IMG / e["file"]).write_bytes(data)
        print(f"[ok]   {e['file']} {len(data)}B")
    except Exception as ex:
        print(f"[fail] {e['file']}: {ex}")