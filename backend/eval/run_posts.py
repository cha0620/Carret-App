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
    ap.add_argument("--ref-text", action="store_true", help="정답 구도를 선 그림 대신 문장으로 (style_refs.json 의 layout, 10-08)")
    ap.add_argument("--model", help="생성 모델 (FAL_MODEL) — 10-08 모델 비교")
    ap.add_argument("--clean-prompt", action="store_true", help="정리한 프롬프트로 통째로 (pipeline.CLEAN_PROMPT, 10-08)")
    ap.add_argument("--min-prompt", action="store_true", help="최소 프롬프트 (pipeline.MIN_PROMPT, 10-09) — 목록 · 이름 · 금지 없이")
    ap.add_argument("--prompt-file", help="생성 프롬프트를 이 파일 내용 그대로 (10-09 세트 JSON 실험)")
    ap.add_argument("--set-prompt", action="store_true", help="세트면 종류 · 개수 JSON, 아니면 최소 프롬프트 (10-09) + 개수 게이트")
    ap.add_argument("--ref-sketch", action="store_true", help="--ref-text 와 함께: 정답 선 그림도 같이 넣는다")
    a = ap.parse_args()

    run_dir = HERE / "results" / "runs" / a.run_id
    (run_dir / "files").mkdir(parents=True, exist_ok=True)
    os.environ.update(STORAGE_BACKEND="local", STORAGE_DIR=str(run_dir / "storage"),
                      DB_PATH=str(run_dir / "carret.db"), KEEP_ATTEMPTS="true",
                      STYLE_REF="false" if a.no_style_ref else "true",
                      MULTI_VIEW="false" if a.no_multi_view else "true",
                      # 실험은 저장소의 프롬프트로 — Langfuse 에 등록된 옛 프롬프트가 덮지 않게 (트레이스도 꺼진다)
                      LANGFUSE_PUBLIC_KEY="", LANGFUSE_SECRET_KEY="")
    if a.model:
        os.environ["FAL_MODEL"] = a.model
    if a.set_prompt:
        os.environ["COUNT_GATE"] = "true"
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
            layout = keep = None
            if a.ref_text:
                # 정답 구도를 문장으로 — 주 사진은 정답 각도와 맞는 첫 사진 (10-08)
                has_box = any(any(k in ((x.get("name") or "") + (x.get("label") or "")).lower() for k in ("box", "package", "상자", "박스"))
                              for x in item["objects"])
                lt = style_refs.plan_text(o.get("name"), o.get("category"), [views.get(f) for f in o["photo_ids"]], has_box)
                planned = None
                if lt:
                    want = next((e["view"][0] for e in style_refs.load() if e["file"] == lt[0]), None)
                    main = next((f for f in o["photo_ids"] if style_refs.view_ok(want, views.get(f))), o["photo_ids"][0])
                    planned, layout = (lt[0], main), lt[1]
                    keep = next((e.get("keep") for e in style_refs.load() if e["file"] == lt[0]), None)
            elif not a.no_style_ref:
                planned = style_refs.plan(
                    o.get("name"), o.get("category"),
                    [{"file_id": f, "view": views.get(f)} for f in o["photo_ids"]],
                    storage.load_original, detector.prep_check)
            else:
                planned = None
            if planned:
                jobs = [(None, planned[1])]
            else:
                jobs = [(c["key"], c["photo_ids"][0]) for c in o.get("compositions") or [] if c.get("available") and c.get("photo_ids")]
                jobs = jobs or [(None, o["photo_ids"][0])]
            for comp, main_id in jobs:
                rest = [f for f in o["photo_ids"] if f != main_id]
                rest.sort(key=lambda f: views.get(f) == views.get(main_id))   # 각도가 다른 것 먼저
                extras = rest[:2]
                if a.ref_sketch and o.get("category") == "clothing":
                    # 옷은 다른 각도를 넣지 않는다 — 각도마다 모양이 달라 선 그림체로 옆에 콜라주했다 (10-08)
                    extras = []
                t0 = time.time()
                res = pipeline.run_transform(main_id, a.preset, composition=comp, extra_view_ids=extras,
                                             ref_category=o.get("category"), ref_view=views.get(main_id),
                                             ref_file=planned[0] if planned else None, ref_layout=layout,
                                             ref_sketch=a.ref_sketch, clean_prompt=("text:" + Path(a.prompt_file).read_text(encoding="utf-8").strip()) if a.prompt_file else "set" if a.set_prompt else "min" if a.min_prompt else a.clean_prompt,
                                             ref_keep=keep)
                tag = f"{post}__{o['id']}{'_' + comp if comp else ''}"
                src = storage.BASE / "result" / res["result_name"]
                if src.exists():
                    shutil.copy(src, run_dir / "files" / f"{tag}_result.jpg")
                ref = res.get("style_ref")
                row = {"post": post, "object": o["id"], "label": o.get("label"), "composition": comp,
                       "main": names[main_id], "main_view": views.get(main_id), "planned_ref": planned[0] if planned else None,
                       "extras": [names[f] for f in extras], "all": [names[f] for f in o["photo_ids"]],
                       "style_ref": ref[0] if ref else None, "ref_layout": layout, "model": os.environ.get("FAL_MODEL"), "prompt_used": res.get("prompt_used"), "gen_prompts": res.get("gen_prompts") or [], "count_mismatch": res.get("count_mismatch"), "result": f"{tag}_result.jpg",
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
