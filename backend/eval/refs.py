"""구도 예시 셋 관리 — 정석 구도를 말 대신 예시 사진으로 정하고, 빠진 각도가 없는지 대 본다.

    cd backend
    python eval/refs.py status        # 종류 × 구도별 예시 수 · 라벨 오류 · 할 일 (라벨 오류가 있으면 종료 코드 1)

예시 사진은 refs/ (남의 상품 사진이라 git 밖), 라벨은 refs.json (git). 모으는 규칙은 REFS.md.
예시는 생성 모델에 넣지 않는다 — 구도 숫자(채움 · 여백 · 수평 · 그림자)를 뽑는 기준과 채점 기준으로만 쓴다
(참조 사진을 넣으면 그 물건의 색 · 소재 · 글자가 섞여 들어올 수 있다).

빠진 각도는 두 방향으로 본다 (compositions.py 에 정의된 구도만 — new: 구도는 아직 앱에 없다):
- 구도가 쓰는 각도를 업로드 단계(coverage.REQUIRED)가 꼭 받아 주지 않는다 → 그 구도를 고를 때만 "찍어 주세요"
  REQUIRED 의 한 면이 받는 각도(대체 각도 포함)가 전부 그 구도의 각도여야 "꼭 받는다"로 친다
- 업로드 때 꼭 받는 면의 각도(대체 각도 포함)인데 그 각도로 만드는 구도가 없다 → 그 사진은 정리만 된다
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

from app.services import compositions, coverage  # noqa: E402

REFS_DIR = HERE / "refs"
REFS_JSON = HERE / "refs.json"
SVG_DIR = HERE.parent.parent / "frontend" / "img" / "compositions"
MIN_EXAMPLES = 3      # 구도 하나를 숫자로 정하려면 예시가 이만큼은 있어야 한다
NEW = "new:"          # compositions.py 에 아직 없는 구도 — "new:<이름>"
NEW_NAME = re.compile(r"^[a-z0-9_]+$")
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


def comp_category(key: str) -> str | None:
    for cat, cs in compositions.COMPOSITIONS.items():
        if any(c["key"] == key for c in cs):
            return cat
    return None


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
        out.append(f"{f}: composition 칸이 비었다 (구도 키, 새 구도면 new:<이름>)")
    elif key.startswith(NEW):
        name = key[len(NEW):]
        if not NEW_NAME.fullmatch(name):
            out.append(f"{f}: 새 구도 이름은 영문 소문자 · 숫자 · _ 로 (new:<이름>), 지금 {key!r}")
        elif name in compositions.BY_KEY:
            out.append(f"{f}: {name} 는 이미 있는 구도 — new: 를 빼고 적는다")
    elif key not in compositions.BY_KEY:
        out.append(f"{f}: 모르는 구도 {key!r} — 새 구도면 'new:<이름>'")
    else:
        if cat_ok and comp_category(key) != cat:
            out.append(f"{f}: 구도 {key} 는 {comp_category(key)} 것인데 종류가 {cat}")
        if view_ok and view not in compositions.BY_KEY[key]["views"]:
            ok = " · ".join(sorted(compositions.BY_KEY[key]["views"]))
            out.append(f"{f}: 구도 {key} 는 {ok} 각도로 만드는데 예시 각도가 {view}")
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


def table(refs: list, refs_dir: Path | None = None) -> dict[str, list[dict]]:
    """종류 → [{key, label, views, n, new}] — 정의된 구도는 예시가 0 장이어도 나온다."""
    good = valid(refs, refs_dir)
    out: dict[str, list[dict]] = defaultdict(list)
    count = Counter((r["category"], r["composition"]) for r in good)
    for cat, cs in compositions.COMPOSITIONS.items():
        for c in cs:
            out[cat].append({"key": c["key"], "label": c["label"], "views": set(c["views"]),
                             "n": count[(cat, c["key"])], "new": False})
    new_views: dict[tuple, set] = defaultdict(set)
    for r in good:
        if r["composition"].startswith(NEW):
            new_views[(r["category"], r["composition"])].add(r["view"])
    for (cat, key), views in sorted(new_views.items()):
        out[cat].append({"key": key, "label": "(새 구도)", "views": views, "n": count[(cat, key)], "new": True})
    return dict(out)


def asked_for(cat: str, views: set[str]) -> bool:
    """업로드 단계가 이 각도들 중 하나를 꼭 받아 주나 — 어떤 필수 면이 받는 각도가 전부 views 안에 있어야 한다
    (대체 각도 하나만 겹치면 사용자는 다른 각도로 그 면을 채울 수 있다)."""
    return any(ok <= views for _, ok, _ in coverage.REQUIRED.get(cat, []))


def gaps(refs: list, refs_dir: Path | None = None, svg_dir: Path | None = None) -> list[str]:
    """할 일 — 예시 부족 · 새 구도 · 빠진 각도 (두 방향) · 그림 없음. 종류는 coverage.CATEGORIES 전부."""
    svg_dir = svg_dir or SVG_DIR
    out = []
    t = table(refs, refs_dir)
    for cat in coverage.CATEGORIES:
        rows = t.get(cat, [])
        defined = [row for row in rows if not row["new"]]
        if not defined:
            out.append(f"[{cat}] compositions.py 에 정석 구도가 없다 — 사진은 정리만 된다 (예시가 모이면 구도를 정한다)")
        for row in rows:
            if row["n"] < MIN_EXAMPLES:
                out.append(f"[{cat}] {row['key']}: 예시 {row['n']}/{MIN_EXAMPLES}장")
            if row["new"]:
                out.append(f"[{cat}] {row['key']}: compositions.py 에 추가할 새 구도 "
                           f"(각도 {' · '.join(sorted(row['views']))})")
                continue
            if not (svg_dir / f"{row['key']}.svg").exists():
                out.append(f"[{cat}] {row['key']}: 선 그림 없음 (frontend/img/compositions/{row['key']}.svg)")
            if not asked_for(cat, row["views"]):
                out.append(f"[{cat}] {row['key']}: 각도 {' · '.join(sorted(row['views']))} 를 업로드 때 꼭 받지 않는다 "
                           f"— 이 구도를 고를 때만 찍어 달라고 한다 (꼭 받을 면이면 coverage.REQUIRED 에)")
        if not defined:
            continue
        made = set().union(*(row["views"] for row in defined))
        for view, ok, hint in coverage.REQUIRED.get(cat, []):
            lack = sorted(ok - made)
            if lack:
                out.append(f"[{cat}] 꼭 받는 면 {view}({hint}) 를 {' · '.join(lack)} 사진으로 채우면 "
                           f"그걸로 만드는 구도가 없다 — 그 사진은 정리만 된다")
    return out


def cmd_status(_a) -> int:
    refs = load_refs()
    print(f"예시 {len(refs)}장 · refs.json (장수는 라벨 오류 없는 것만)\n")
    for cat, rows in table(refs).items():
        print(f"## {cat}")
        for row in rows:
            print(f"  {row['key']:<22} {row['n']:>2}장  {row['label']}  [{' · '.join(sorted(row['views']))}]")
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
