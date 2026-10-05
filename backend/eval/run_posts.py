"""게시글 단위 실행 (10-05 실험) — 앱처럼 게시글의 상품 사진을 한꺼번에 올리고(POST /api/items) 나온 물건마다
결과를 만든다. multi_view · style_ref 를 켜서: 주 사진 + 같은 물건 다른 각도(최대 2) + 스타일 참고.
근거 사진(slot=proof)은 넣지 않는다.

    cd backend
    python eval/run_posts.py --posts none_lego,none_iphone1,none_coat --run-id posts-1005

앱의 storage · DB 는 건드리지 않는다 — results/runs/<run_id>/ 를 쓴다 (run.py 와 같은 방식).
"""
import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
IMAGES = HERE / "data" / "images"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--posts", required=True, help="쉼표로 구분한 게시글 (dataset.json 의 post)")
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--preset", default="studio_white")
    ap.add_argument("--no-style-ref", action="store_true")
    ap.add_argument("--no-multi-view", action="store_true")
    a = ap.parse_args()

    run_dir = HERE / "results" / "runs" / a.run_id
    (run_dir / "files").mkdir(parents=True, exist_ok=True)
    os.environ.update(STORAGE_BACKEND="local", STORAGE_DIR=str(run_dir / "storage"),
                      DB_PATH=str(run_dir / "carret.db"), KEEP_ATTEMPTS="true",
                      STYLE_REF="false" if a.no_style_ref else "true",
                      MULTI_VIEW="false" if a.no_multi_view else "true",
                      # 실험은 저장소의 프롬프트로 — Langfuse 에 등록된 옛 프롬프트가 덮지 않게 (트레이스도 꺼진다)
                      LANGFUSE_PUBLIC_KEY="", LANGFUSE_SECRET_KEY="")
    sys.path.insert(0, str(HERE.parent))
    from fastapi.testclient import TestClient
    from app.core import db
    from app.services import pipeline, style_refs
    from app.services.ai import detector
    from app.services.persistence import storage
    from main import app
    db.init_db()
    client = TestClient(app)

    dataset = json.loads((HERE / "data" / "dataset.json").read_text(encoding="utf-8"))
    out = []
    for post in [p.strip() for p in a.posts.split(",")]:
        photos = sorted((e for e in dataset if e.get("post") == post and e.get("slot") == "product"),
                        key=lambda e: e.get("post_index") or 0)
        if not photos:
            print(f"{post}: 상품 사진 없음"); continue
        files = [("files", (e["file"], (IMAGES / e["file"]).read_bytes(), "application/octet-stream")) for e in photos]
        r = client.post("/api/items", files=files)
        r.raise_for_status()
        item = r.json()
        names = {p["file_id"]: e["file"] for p, e in zip(item["photos"], photos)}
        views = {p["file_id"]: p.get("view") for p in item["photos"]}
        for o in item["objects"]:
            # 본품만 만든다 — 부가품(충전기 · 케이스 · 상자)은 썸네일에서 뺀다 (10-05)
            if o["kind"] != "product" or not o.get("for_sale") or not o.get("photo_ids") or o.get("role") not in (None, "main"):
                continue
            # 정답 사진과 주 사진을 같이 고른다 — 정답의 각도 · 준비물(상자 등)이 한 장에 다 보이는 사진 (10-05)
            planned = None if a.no_style_ref else style_refs.plan(
                o.get("name"), o.get("category"),
                [{"file_id": f, "view": views.get(f)} for f in o["photo_ids"]],
                storage.load_original, detector.prep_check)
            if planned:
                jobs = [(None, planned[1])]
            else:
                jobs = [(c["key"], c["photo_ids"][0]) for c in o.get("compositions") or [] if c.get("available") and c.get("photo_ids")]
                jobs = jobs or [(None, o["photo_ids"][0])]
            for comp, main_id in jobs:
                rest = [f for f in o["photo_ids"] if f != main_id]
                rest.sort(key=lambda f: views.get(f) == views.get(main_id))   # 각도가 다른 것 먼저
                extras = rest[:2]
                t0 = time.time()
                res = pipeline.run_transform(main_id, a.preset, composition=comp, extra_view_ids=extras,
                                             ref_category=o.get("category"), ref_view=views.get(main_id),
                                             ref_file=planned[0] if planned else None)
                tag = f"{post}__{o['id']}{'_' + comp if comp else ''}"
                src = storage.BASE / "result" / res["result_name"]
                if src.exists():
                    shutil.copy(src, run_dir / "files" / f"{tag}_result.jpg")
                ref = res.get("style_ref")
                row = {"post": post, "object": o["id"], "label": o.get("label"), "composition": comp,
                       "main": names[main_id], "main_view": views.get(main_id), "planned_ref": planned[0] if planned else None,
                       "extras": [names[f] for f in extras], "all": [names[f] for f in o["photo_ids"]],
                       "style_ref": ref[0] if ref else None, "result": f"{tag}_result.jpg",
                       "mode": res.get("mode"), "composite_reason": res.get("composite_reason"),
                       "gate_passed": res.get("gate_passed"), "added_text": res.get("added_text"),
                       "checks": res.get("checks"), "gen_attempts": res.get("gen_attempts"),
                       "elapsed_s": round(time.time() - t0, 1)}
                # 생성 시도(KEEP_ATTEMPTS)도 옮겨 둔다
                for i, att in enumerate(sorted((storage.BASE / "attempts").glob(f"{main_id}_*")), 1):
                    shutil.copy(att, run_dir / "files" / f"{tag}_try{i}{att.suffix}")
                    row.setdefault("tries", []).append(f"{tag}_try{i}{att.suffix}")
                out.append(row)
                print(json.dumps(row, ensure_ascii=False)[:300], flush=True)
    (run_dir / "posts.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
