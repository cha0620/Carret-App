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


def picked_pieces(post: str, used: list[str], picks: list[str]) -> list[tuple[str, int]] | None:
    """판매자처럼 고른 물건만 (10-11 사용자: "물건 리스트 주고 선택된 걸 생성") — picks 는 objects.json 의 what.
    생성에 들어간 사진에 보이는 것만, 정답 개수 그대로. 첫 번째로 고른 것이 본체."""
    f = HERE / "data" / "posts" / post / "objects.json"
    objs = {o["what"]: o for o in json.loads(f.read_text(encoding="utf-8"))["objects"]}
    out = []
    for i, w in enumerate(picks):
        o = objs.get(w)
        if o is None:
            raise SystemExit(f"{post}: objects.json 에 없는 물건 '{w}'")
        if not set(o.get("photos") or []) & set(used):
            print(f"  {post}: '{w}' 은 생성에 들어간 사진에 안 보여 뺌")
            continue
        n = int(o.get("count") or 1)
        out.append((f"main piece ({w})" if i == 0 else w, n))
    return out or None


def gt_pieces(post: str, used: list[str]) -> list[tuple[str, int]] | None:
    """정답 구성품 (eval/data/posts/<post>/objects.json, 10-10) → [(종류, 개수)]. 생성에 들어간 사진(used)에 보이는
    판매 본품 · 구성품만 — 근거 사진에서만 보인 것은 모델이 못 본다. 정답이 없으면 None (분석에서)."""
    f = HERE / "data" / "posts" / post / "objects.json"
    if not f.exists():
        return None
    # 대표사진 = 본체 하나 (10-11 사용자, 10-10 엔 + 상자) — 나머지 구성품 개수는 상세 · 인증용. 앱의 pipeline.rep_split 과 같은 규칙
    objs = [o for o in json.loads(f.read_text(encoding="utf-8"))["objects"]
            if o.get("for_sale") and (int(o["count"]) if o.get("count") is not None else 1) > 0
            and set(o.get("photos") or []) & set(used)]
    from app.services import pipeline
    rep, _ = pipeline.rep_split(objs, list(range(len(objs))), [i for i, o in enumerate(objs) if o.get("role") == "main"][:1])
    if not rep:
        return None
    out = [(f"main piece ({objs[rep[0]].get('what') or 'item'})", int(objs[rep[0]].get("count") or 1))]
    out += [(objs[i].get("what") or "box", 1) for i in rep[1:]]
    return out


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
    ap.add_argument("--mode", choices=["generate", "composite", "auto"], default="generate",
                    help="generate: 생성만 — 실패할 것 같아도 돌린다 (기본, 10-11 사용자: 생성 · 배경 교체는 별개, 테스트일 뿐) · "
                         "composite: 배경 교체만 · auto: 예전처럼 분석이 정함")
    ap.add_argument("--view-check", action="store_true",
                    help="생성 전에 구도 검사(VLM 1회, detector.composition_check) — 모델이 정답 구도로 못 만든다고 하면 생성하지 않고 '구도 부족(어느 방면)' 으로 기록 (10-11)")
    ap.add_argument("--picks", help="고른 물건 JSON {post: [what, ...]} (10-11) — 첫 번째가 본체, 고른 것 전부를 생성")
    ap.add_argument("--no-gt-pieces", action="store_true", help="세트 구성품을 정답(data/posts/<post>/objects.json) 대신 분석에서")
    ap.add_argument("--force-generate", action="store_true", help="글자 많음(text_dense) · 잘림(cut_off) · 겹침(overlap) 이어도 배경 교체로 보내지 않고 생성 (10-10 실험)")
    ap.add_argument("--set-prompt", action="store_true", help="세트면 종류 · 개수 JSON, 아니면 최소 프롬프트 (10-09) + 개수 게이트")
    ap.add_argument("--ref-sketch", action="store_true", help="--ref-text 와 함께: 정답 선 그림도 같이 넣는다")
    a = ap.parse_args()
    picks = json.loads(Path(a.picks).read_text(encoding="utf-8")) if a.picks else {}

    run_dir = HERE / "results" / "runs" / a.run_id
    (run_dir / "files").mkdir(parents=True, exist_ok=True)
    os.environ.update(STORAGE_BACKEND="local", STORAGE_DIR=str(run_dir / "storage"),
                      DB_PATH=str(run_dir / "carret.db"), KEEP_ATTEMPTS="true",
                      STYLE_REF="false" if a.no_style_ref else "true",
                      MULTI_VIEW="false" if a.no_multi_view else "true",
                      # 실험은 저장소의 프롬프트로 — Langfuse 에 등록된 옛 프롬프트가 덮지 않게. 트레이스는 남긴다 (10-10)
                      LANGFUSE_PROMPTS="false")
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
    if a.force_generate:
        _first = pipeline._composite_first_reason
        # 그 두 판단만 지우고 다시 본다 — 뒤의 wear_heavy · overlap · multi_item 은 그대로 (10-10 리뷰).
        # dense 를 생성으로 보내면 글자 읽기 · 글자 잠금이 빠진다 — 결과를 "dense 도 된다"로 읽지 않는다
        pipeline._composite_first_reason = lambda s: _first({**s, "text_level": "simple", "item_cut_off": False, "pieces_overlap": False})
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
                if a.view_check and planned:
                    # 구도 부족이면 돌리지 않는다 — 모델이 판매자 사진 · 정답 사진을 보고 판단, 사용자가 나중에 평가 (10-11)
                    ids = o["photo_ids"][:4]
                    cc = detector.composition_check([storage.load_original(f) for f in ids], style_refs.answer_photo(planned[0]))
                    if not cc["ok"]:
                        out.append({"post": post, "object": o["id"], "label": o.get("label"), "composition": comp,
                                    "main": names[main_id], "planned_ref": planned[0], "mode": "skipped_view",
                                    "view_missing": [{"what": cc["need"], "hint": cc["why"]}], "result": None,
                                    "photos_checked": [names[f] for f in ids], "elapsed_s": round(time.time() - t0, 1)})
                        print(f"  {post}: 구도 부족 — 생성 안 함: {cc['need']} ({cc['why']})", flush=True)
                        continue
                used = [names[f] for f in [main_id, *extras]]
                gt = (picked_pieces(post, used, picks[post]) if post in picks
                      else None if a.no_gt_pieces else gt_pieces(post, used))   # 한 번만 — 기록과 실제가 같게
                res = pipeline.run_transform(main_id, a.preset, composition=comp, extra_view_ids=extras,
                                             ref_category=o.get("category"), ref_view=views.get(main_id),
                                             ref_file=planned[0] if planned else None, ref_layout=layout,
                                             ref_sketch=a.ref_sketch, clean_prompt=("text:" + Path(a.prompt_file).read_text(encoding="utf-8").strip()) if a.prompt_file else "set" if a.set_prompt else "min" if a.min_prompt else a.clean_prompt,
                                             ref_keep=keep,
                                             fixed_pieces=gt,
                                             user_mode=None if a.mode == "auto" else a.mode)
                tag = f"{post}__{o['id']}{'_' + comp if comp else ''}"
                src = storage.BASE / "result" / res["result_name"]
                if src.exists():
                    shutil.copy(src, run_dir / "files" / f"{tag}_result.jpg")
                ref = res.get("style_ref")
                row = {"post": post, "object": o["id"], "label": o.get("label"), "composition": comp,
                       "main": names[main_id], "main_view": views.get(main_id), "planned_ref": planned[0] if planned else None,
                       "extras": [names[f] for f in extras], "all": [names[f] for f in o["photo_ids"]],
                       "style_ref": ref[0] if ref else None, "ref_layout": layout, "model": os.environ.get("FAL_MODEL"), "prompt_used": res.get("prompt_used"), "gen_prompts": res.get("gen_prompts") or [], "count_mismatch": res.get("count_mismatch"), "gt_pieces": gt, "result": f"{tag}_result.jpg",
                       "mode": res.get("mode"), "composite_reason": res.get("composite_reason"), "risk_reason": res.get("risk_reason"),
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
