"""준비물 대조 재기 (10-05) — 게시글의 상품 사진을 앱처럼 묶고(POST /api/items), 물건마다 종류 · 각도가 맞는
정답 후보 × 사진 쌍에 준비물 대조(detector.prep_check)를 전부 돌려 남긴다 (생성 없음). 사람 정답과 맞춰 보는 용도.

    cd backend
    python eval/measure_prep.py --run-id prep-1005
"""
import argparse
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
IMAGES = HERE / "data" / "images"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--posts", default="", help="쉼표로 구분 (없으면 상품 사진이 있는 게시글 전부)")
    a = ap.parse_args()
    run_dir = HERE / "results" / "runs" / a.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    os.environ.update(STORAGE_BACKEND="local", STORAGE_DIR=str(run_dir / "storage"), DB_PATH=str(run_dir / "carret.db"),
                      LANGFUSE_PUBLIC_KEY="", LANGFUSE_SECRET_KEY="")   # 저장소 프롬프트로
    sys.path.insert(0, str(HERE.parent))
    from fastapi.testclient import TestClient
    from app.core import db
    from app.services import style_refs
    from app.services.ai import detector
    from app.services.persistence import storage
    from main import app
    db.init_db()
    client = TestClient(app)

    dataset = json.loads((HERE / "data" / "dataset.json").read_text(encoding="utf-8"))
    posts = [p.strip() for p in a.posts.split(",") if p.strip()] or \
        sorted({e["post"] for e in dataset if e.get("slot") == "product" and e.get("post")})
    rows = []
    for post in posts:
        photos = sorted((e for e in dataset if e.get("post") == post and e.get("slot") == "product"),
                        key=lambda e: e.get("post_index") or 0)
        files = [("files", (e["file"], (IMAGES / e["file"]).read_bytes(), "application/octet-stream")) for e in photos]
        r = client.post("/api/items", files=files)
        if r.status_code != 200:
            print(f"{post}: 묶기 실패 {r.status_code} {r.text[:200]}"); continue
        item = r.json()
        names = {p["file_id"]: e["file"] for p, e in zip(item["photos"], photos)}
        views = {p["file_id"]: p.get("view") for p in item["photos"]}
        for o in item["objects"]:
            if o["kind"] != "product" or not o.get("for_sale") or o.get("role") not in (None, "main"):
                continue
            row = {"post": post, "object": o["id"], "label": o.get("label"), "name": o.get("name"),
                   "category": o.get("category"), "role": o.get("role"),
                   "photos": [{"file": names[f], "view": views.get(f)} for f in o["photo_ids"]],
                   "others": [{"id": x["id"], "label": x.get("label"), "role": x.get("role"), "part_of": x.get("part_of")}
                              for x in item["objects"] if x["id"] != o["id"] and x["kind"] == "product"],
                   "pairs": []}
            for e in style_refs.candidates(o.get("name"), o.get("category"), None):
                for f in o["photo_ids"]:
                    if not style_refs.view_ok(e["view"][0], views.get(f)):
                        continue
                    try:
                        missing = detector.prep_check(storage.load_original(f), e["prep"])
                        err = None
                    except Exception as ex:
                        missing, err = None, str(ex)[:200]
                    row["pairs"].append({"ref": e["file"], "photo": names[f], "prep": e["prep"],
                                         "missing": missing, "error": err})
            planned = style_refs.plan(o.get("name"), o.get("category"),
                                      [{"file_id": f, "view": views.get(f)} for f in o["photo_ids"]],
                                      storage.load_original, lambda img, t: [])   # 대조 결과는 pairs 로 — 여기선 후보 순서만
            row["first_candidate"] = planned[0] if planned else None
            rows.append(row)
            print(json.dumps({k: row[k] for k in ("post", "label", "category")}, ensure_ascii=False),
                  [(p["ref"], p["photo"], p["missing"]) for p in row["pairs"]], flush=True)
    (run_dir / "prep.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
