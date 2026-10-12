"""구도 예시 셋 관리 — 정석 구도를 말 대신 예시 사진으로 정하고, 그 구도에 필요한 부위를 VLM 에게 묻는다.

    cd backend
    python eval/refs.py status        # 종류 × 구도별 예시 수 · 라벨 오류 · 할 일 (라벨 오류가 있으면 종료 코드 1)

예시 사진은 refs/ (남의 상품 사진이라 git 밖), 라벨은 refs.json (git). 모으는 규칙은 REFS.md.
예시는 생성 모델에 넣지 않는다 — 구도 숫자(채움 · 여백 · 수평 · 그림자)를 뽑는 기준과 채점 기준으로만 쓴다
(참조 사진을 넣으면 그 물건의 색 · 소재 · 글자가 섞여 들어올 수 있다).

10-11: 빠진 각도를 규칙(coverage.REQUIRED · compositions.py 의 각도)으로 대 보던 것을 지웠다. 구도 키는 이름일 뿐이고,
그 구도를 만들려면 판매자 사진에 무엇이 보여야 하는지(needs — "게임판 앞면", "밑창 전체")는 style_refs.json 에 적힌 것을
그대로 보여 준다 — AI 사진 검토(photo_review)가 정답과 함께 VLM 에 넘기는 값이다. 여기서는 VLM 을 부르지 않는다.
"""
import argparse
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE.parent) not in sys.path:
    sys.path.insert(0, str(HERE.parent))

from app.services import coverage  # noqa: E402

REFS_DIR = HERE / "data" / "refs"
REFS_JSON = HERE / "data" / "refs.json"
STYLE_REFS_JSON = HERE / "data" / "style_refs.json"   # 정답마다 needs (파일 이름이 같다)
SVG_DIR = HERE.parent.parent / "frontend" / "img" / "compositions"
MIN_EXAMPLES = 3      # 구도 하나를 숫자로 정하려면 예시가 이만큼은 있어야 한다
NAME = re.compile(r"^[a-z0-9_]+$")   # 구도 키 — 영문 소문자 · 숫자 · _
NEW = "new:"          # 옛 라벨의 "아직 없는 구도" 표시 — 이제 구도 키는 다 이름이라 떼고 읽는다
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}


def load_refs(path: Path | None = None) -> list:
    """깨진 파일은 traceback 대신 한 줄 안내로 끝낸다."""
    path = path or REFS_JSON
    if not path.exists():
        return []
    try:
        refs = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
        raise SystemExit(f"{path.name} 를 읽지 못했다: {e}")
    if not isinstance(refs, list):
        raise SystemExit(f"{path.name} 는 [ ... ] 목록이어야 한다")
    return refs


def comp_key(r: dict) -> str:
    key = r.get("composition") if isinstance(r.get("composition"), str) else ""
    return key[len(NEW):] if key.startswith(NEW) else key


def entry_errors(r, refs_dir: Path | None = None) -> list[str]:
    """한 예시의 라벨 오류 — 원인 하나에 한 줄."""
    refs_dir = refs_dir or REFS_DIR
    if not isinstance(r, dict):
        return [f"항목이 {{...}} 가 아니다: {r!r}"]
    f = r.get("file")
    if not isinstance(f, str) or not f:
        return [f"file 이 없는 항목: {r}"]
    out = []
    if Path(f).name != f:
        out.append(f"{f}: 파일 이름만 적는다 (폴더 · ../ 없이)")
    elif not (refs_dir / f).is_file():
        out.append(f"{f}: refs/ 에 사진이 없다")
    if Path(f).suffix.lower() not in IMAGE_EXTS:
        out.append(f"{f}: 사진 파일이 아니다 (jpg · png · webp)")
    cat, key, view = r.get("category"), r.get("composition"), r.get("view")
    cat_ok = isinstance(cat, str) and cat in coverage.CATEGORIES
    view_ok = isinstance(view, str) and view in coverage.VIEWS
    if not cat_ok:
        out.append(f"{f}: 모르는 종류 {cat!r} ({' · '.join(coverage.CATEGORIES)})")
    if not view_ok:
        out.append(f"{f}: 모르는 각도 {view!r} ({' · '.join(coverage.VIEWS)})")
    if not isinstance(key, str) or not key:
        out.append(f"{f}: composition 칸이 비었다 (구도 키 — 영문 소문자 · 숫자 · _)")
    elif not NAME.fullmatch(comp_key(r)):
        out.append(f"{f}: 구도 키는 영문 소문자 · 숫자 · _ 로, 지금 {key!r}")
    return out


def errors(refs: list, refs_dir: Path | None = None) -> list[str]:
    """라벨 오류 전부 — 같은 파일 두 번 · 같은 사진 다른 이름 · refs/ 에만 있는 사진 포함."""
    refs_dir = refs_dir or REFS_DIR
    names = [r.get("file") for r in refs if isinstance(r, dict) and isinstance(r.get("file"), str)]
    out = [f"같은 파일이 두 번: {f}" for f, n in Counter(names).items() if n > 1]
    for r in refs:
        out += entry_errors(r, refs_dir)
    if refs_dir.is_dir():
        on_disk = sorted(p for p in refs_dir.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTS)
        out += [f"refs.json 에 없는 사진: {p.name}" for p in on_disk if p.name not in set(names)]
        by_hash = defaultdict(list)
        for p in on_disk:
            by_hash[hashlib.sha1(p.read_bytes()).hexdigest()].append(p.name)
        out += [f"같은 사진이 다른 이름으로: {' · '.join(ns)}" for ns in by_hash.values() if len(ns) > 1]
    return out


def valid(refs: list, refs_dir: Path | None = None) -> list[dict]:
    """라벨 오류가 없는 예시만 — 장수는 이것만 센다 (틀린 예시로 "예시 부족"이 사라지지 않게)."""
    return [r for r in refs if not entry_errors(r, refs_dir)]


def style_needs(path: Path | None = None) -> dict[str, list[str]]:
    """style_refs.json 의 파일 → needs. 없거나 깨졌으면 빈 dict (status 는 needs 없음으로 보여 준다)."""
    path = path or STYLE_REFS_JSON
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return {r["file"]: [x for x in r.get("needs") or [] if isinstance(x, str) and x]
            for r in rows if isinstance(r, dict) and isinstance(r.get("file"), str)} if isinstance(rows, list) else {}


def table(refs: list, refs_dir: Path | None = None, needs_by_file: dict | None = None) -> dict[str, list[dict]]:
    """종류 → [{key, views, n, needs}] — 라벨 오류 없는 예시만. needs 는 그 구도 예시들의 style_refs.json needs (처음 나온 순서)."""
    good = valid(refs, refs_dir)
    needs_by_file = style_needs() if needs_by_file is None else needs_by_file
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for r in good:
        groups[(r["category"], comp_key(r))].append(r)
    out: dict[str, list[dict]] = defaultdict(list)
    for (cat, key), rs in sorted(groups.items()):
        needs = list(dict.fromkeys(x for r in rs for x in needs_by_file.get(r["file"], [])))
        out[cat].append({"key": key, "views": {r["view"] for r in rs}, "n": len(rs), "needs": needs})
    return dict(out)


def gaps(refs: list, refs_dir: Path | None = None, svg_dir: Path | None = None,
         needs_by_file: dict | None = None) -> list[str]:
    """할 일 — 예시 부족 · 필요한 부위(needs) 없음 · 선 그림 없음. 종류는 coverage.CATEGORIES 전부."""
    svg_dir = svg_dir or SVG_DIR
    out = []
    t = table(refs, refs_dir, needs_by_file)
    for cat in coverage.CATEGORIES:
        rows = t.get(cat, [])
        if not rows:
            out.append(f"[{cat}] 구도 예시가 없다")
        for row in rows:
            if row["n"] < MIN_EXAMPLES:
                out.append(f"[{cat}] {row['key']}: 예시 {row['n']}/{MIN_EXAMPLES}장")
            if not row["needs"]:
                out.append(f"[{cat}] {row['key']}: 필요한 부위(needs) 없음 — style_refs.json 에 적는다")
            if not (svg_dir / f"{row['key']}.svg").exists():
                out.append(f"[{cat}] {row['key']}: 선 그림 없음 (frontend/img/compositions/{row['key']}.svg)")
    return out


def cmd_status(_a) -> int:
    refs = load_refs()
    print(f"예시 {len(refs)}장 · refs.json (장수는 라벨 오류 없는 것만)\n")
    for cat, rows in table(refs).items():
        print(f"## {cat}")
        for row in rows:
            print(f"  {row['key']:<22} {row['n']:>2}장  [{' · '.join(sorted(row['views']))}]  "
                  f"필요: {' · '.join(row['needs']) or '(없음)'}")
    errs = errors(refs)
    if errs:
        print("\n라벨 오류:")
        print("\n".join(f"  - {m}" for m in errs))
    todo = gaps(refs)
    if todo:
        print("\n할 일:")
        print("\n".join(f"  - {m}" for m in todo))
    return 1 if errs else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status").set_defaults(fn=cmd_status)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
