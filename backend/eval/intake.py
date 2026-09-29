"""모은 사진 정리 — images/inbox/ → images/ + dataset.json, 라벨은 CSV 로 주고받는다.

    cd backend
    python eval/intake.py status                              # 층별 현황 · 라벨 남은 수 · 이상한 파일
    python eval/intake.py add                                 # inbox 사진을 dataset.json 에 추가 (dev/test 나눔)
    python eval/intake.py add --pilot                         # 파일럿: 층 목표에 세지 않고 전부 dev
    python eval/intake.py export                              # 라벨 빈 항목 → images/inbox/labels.csv
    python eval/intake.py merge images/inbox/labels.csv --by 이름   # 채운 라벨을 dataset.json 에

inbox 파일 이름은 `<층 코드>_<아무거나>.<확장자>` (코드는 STRATA). 게시글 주소는
images/inbox/urls.txt 에 `파일이름 주소` 로 한 줄씩. 모으는 규칙은 COLLECT.md.

dev/test: 층마다 목표 수만큼 자리(dev·test)를 미리 섞어 두고, 그 층에 k 번째로 들어온 사진이 k 번째 자리를
받는다 — 조금씩 add 해도 모은 순서(=검색어 순서)와 무관하게 dev 가 흩어진다. 한 번 단 split 은
splits.jsonl 에 남기고, status 가 dataset.json 과 대 본다 (test 동결).
"""
import argparse
import csv
import hashlib
import json
import os
import random
import re
import shutil
import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
IMAGES = HERE / "images"
INBOX = IMAGES / "inbox"
DATASET = HERE / "dataset.json"
SPLIT_LOG = HERE / "splits.jsonl"
LABELS_CSV = INBOX / "labels.csv"

# 층 코드 → (test 목표, dev 목표). COLLECT.md 의 표와 같게
STRATA = {"none": (25, 5), "light": (25, 5), "heavy": (10, 3), "dense": (10, 3),
          "doc": (15, 4), "inside": (8, 2), "edge": (7, 3)}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
IGNORED_INBOX = {"urls.txt", "labels.csv"}
ENUMS = {"photo_type": ("document", "inside_view", "product"),
         "wear_level": ("none", "light", "heavy"),
         "text_level": ("none", "simple", "dense")}
LABEL_COLS = ["file", "stratum", "item", "photo_type", "wear_level", "text_level",
              "key_texts", "ambiguous", "note"]
FILLED_COLS = ["item", "photo_type", "wear_level", "text_level", "key_texts", "ambiguous", "note"]
DRAFT = "claude-draft"
SEED = 20260929
# 엑셀이 글자를 날짜·지수로 바꾼 흔적 — key_texts 는 "그대로여야 할 글자"라 경고한다
_MANGLED = re.compile(r"^\d{4}-\d{2}-\d{2}( \d{2}:\d{2}(:\d{2})?)?$|^\d+(\.\d+)?E[+-]?\d+$", re.I)


# ── dataset.json · splits.jsonl ──
def load_dataset() -> list[dict]:
    return json.loads(DATASET.read_text(encoding="utf-8")) if DATASET.exists() else []


def save_dataset(entries: list[dict]) -> None:
    """한 항목 한 줄 — 라벨을 고친 diff 가 그 사진 줄만 바뀌게. 임시 파일에 쓰고 바꿔 끼운다
    (쓰는 도중 멈춰도 dataset.json 이 깨지지 않게)."""
    body = ",\n".join("  " + json.dumps(e, ensure_ascii=False) for e in entries)
    tmp = DATASET.with_suffix(".json.tmp")
    tmp.write_text(f"[\n{body}\n]\n" if entries else "[]\n", encoding="utf-8")
    os.replace(tmp, DATASET)


def load_split_log() -> dict[str, str]:
    if not SPLIT_LOG.exists():
        return {}
    log = {}
    for line in SPLIT_LOG.read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            log.setdefault(r["file"], r["split"])   # 처음 단 값이 기준
    return log


def append_split_log(new: list[dict]) -> None:
    with open(SPLIT_LOG, "a", encoding="utf-8") as f:
        for e in new:
            f.write(json.dumps({"file": e["file"], "stratum": e["stratum"] or e.get("legacy_stratum", ""),
                                "split": e["split"],
                                "at": e["collected_at"]}, ensure_ascii=False) + "\n")


def is_labeled(e: dict) -> bool:
    return all(e.get(k) in ENUMS[k] for k in ENUMS)


def needs_label(e: dict) -> bool:
    """사람이 라벨을 달아야 하나 — 비었거나 초안(claude-draft)."""
    return not is_labeled(e) or e.get("labeled_by") == DRAFT


def stratum_of(name: str) -> str | None:
    code = name.split("_", 1)[0]
    return code if "_" in name and code in STRATA else None


# ── inbox ──
def inbox_files(inbox: Path) -> tuple[list[Path], list[str]]:
    """(사진, 형식이 안 맞는 파일 이름). urls.txt · labels.csv · 숨김 파일은 뺀다."""
    if not inbox.exists():
        return [], []
    photos, other = [], []
    for p in sorted(inbox.iterdir()):
        if p.is_dir() or p.name in IGNORED_INBOX or p.name.startswith("."):
            continue
        (photos if p.suffix.lower() in IMAGE_EXTS else other).append(p)
    return photos, [p.name for p in other]


def read_urls(path: Path) -> tuple[dict[str, str], list[str]]:
    """urls.txt → ({파일이름: 주소}, 경고). 한 줄 `파일이름 주소` — 마지막 칸이 주소다
    (파일 이름에 공백이 있어도 된다). 탭 구분 · BOM 도 받는다. 빈 줄 · # 주석은 무시."""
    if not path.exists():
        return {}, []
    urls, warnings = {}, []
    for i, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.rsplit(None, 1)
        if len(parts) < 2 or not parts[1].startswith(("http://", "https://")):
            warnings.append(f"urls.txt {i}번째 줄: `파일이름 주소` 모양이 아니다 — {line[:60]}")
            continue
        name, url = parts[0].strip(), parts[1]
        if name in urls and urls[name] != url:
            warnings.append(f"urls.txt: {name} 이 두 번 나온다 (뒤의 주소를 쓴다)")
        urls[name] = url
    return urls, warnings


def _digest(path: Path) -> str:
    return hashlib.sha1(path.read_bytes()).hexdigest()


def split_slots(stratum: str) -> list[str]:
    """층의 dev·test 자리를 목표 수만큼 만들어 층별 시드로 섞는다."""
    test_q, dev_q = STRATA[stratum]
    slots = ["dev"] * dev_q + ["test"] * test_q
    random.Random(f"{SEED}:{stratum}").shuffle(slots)
    return slots


def assign_splits(entries: list[dict], new: list[dict]) -> None:
    """새 항목에 split 을 단다 — 그 층에 k 번째로 들어온 사진이 split_slots 의 k 번째 자리.
    한 묶음 안의 순서도 섞는다 (파일 이름 순 = 모은 순). 목표를 넘는 층은 plan_add 가 먼저 막는다.
    이미 있는 split 은 건드리지 않는다."""
    by_stratum: dict[str, list[dict]] = {}
    for e in new:
        by_stratum.setdefault(e["stratum"], []).append(e)
    for stratum, items in sorted(by_stratum.items()):
        slots = split_slots(stratum)
        k = sum(e.get("stratum") == stratum for e in entries)
        items.sort(key=lambda e: e["file"])
        random.Random(f"{SEED}:{stratum}:{k}").shuffle(items)
        for e in items:
            e["split"] = slots[k]
            k += 1


def plan_add(photos: list[Path], urls: dict[str, str], entries: list[dict],
             images_dir: Path, *, pilot: bool = False) -> tuple[list[dict], list[str]]:
    """inbox 사진 → (새 항목, 오류). 오류가 하나라도 있으면 아무것도 옮기지 않는다.
    pilot: 규칙 전에 모았거나 결과를 먼저 볼 사진 — 층 목표에 세지 않고(stratum 빈 값,
    legacy_stratum 에 층 코드) 전부 dev. test 자리를 쓰지 않는다."""
    errors, new = [], []
    known = {e["file"] for e in entries}
    digests = {_digest(images_dir / e["file"]): e["file"]
               for e in entries if (images_dir / e["file"]).exists()}
    count = {s: sum(e.get("stratum") == s for e in entries) for s in STRATA}
    for p in sorted(photos):
        stratum = stratum_of(p.name)
        if stratum is None:
            errors.append(f"{p.name}: 이름이 <층 코드>_... 가 아니다 (코드: {', '.join(STRATA)})")
            continue
        if p.name in known or (images_dir / p.name).exists():
            errors.append(f"{p.name}: 같은 이름이 이미 있다")
            continue
        d = _digest(p)
        if d in digests:
            errors.append(f"{p.name}: {digests[d]} 와 같은 사진이다")
            continue
        count[stratum] += 0 if pilot else 1
        if not pilot and count[stratum] > sum(STRATA[stratum]):
            errors.append(f"{p.name}: {stratum} 층 목표({sum(STRATA[stratum])}장)를 넘는다 — 이 층은 다 모았다")
            continue
        digests[d] = p.name
        e = {"file": p.name, "url": urls.get(p.name, ""), "item": "",
             "photo_type": "", "wear_level": "", "text_level": "", "key_texts": [],
             "note": "", "labeled_by": "", "stratum": stratum,
             "collected_at": date.today().isoformat(), "ambiguous": False}
        if pilot:
            e.update(stratum="", legacy_stratum=stratum, split="dev", pilot=True)
        new.append(e)
    return new, errors


def cmd_add(a) -> int:
    pilot = bool(getattr(a, "pilot", False))
    INBOX.mkdir(parents=True, exist_ok=True)
    photos, other = inbox_files(INBOX)
    urls, url_warnings = read_urls(INBOX / "urls.txt")
    for m in url_warnings:
        print(f"경고: {m}")
    if other:
        print("아무것도 옮기지 않았다 — jpg · png · webp 로 다시 저장:")
        for n in other:
            print(f"  {n}")
        return 1
    if not photos:
        print(f"inbox 가 비었다: {INBOX}")
        return 0
    entries = load_dataset()
    new, errors = plan_add(photos, urls, entries, IMAGES, pilot=pilot)
    if errors:
        print("아무것도 옮기지 않았다 — 고친 뒤 다시:")
        for m in errors:
            print(f"  {m}")
        return 1
    stray = sorted(set(urls) - {p.name for p in photos} - {e["file"] for e in entries})
    if stray:
        print(f"경고: urls.txt 에만 있고 inbox 에 없는 이름 — {', '.join(stray)}")
    assign_splits(entries, [e for e in new if not e.get("pilot")])
    # 복사 → 저장 → inbox 원본 삭제. 저장 전에 멈추면 복사본을 지워 원래대로
    copied = []
    try:
        for e in new:
            shutil.copy2(INBOX / e["file"], IMAGES / e["file"])
            copied.append(IMAGES / e["file"])
        save_dataset(entries + new)
    except BaseException:
        for p in copied:
            p.unlink(missing_ok=True)
        raise
    append_split_log(new)
    for e in new:
        (INBOX / e["file"]).unlink(missing_ok=True)
    no_url = [e["file"] for e in new if not e["url"]]
    print(f"{len(new)}장 추가{' (파일럿 — 층 목표 밖)' if pilot else ''} (test {sum(e['split'] == 'test' for e in new)} · "
          f"dev {sum(e['split'] == 'dev' for e in new)})")
    if no_url:
        print(f"urls.txt 에 주소가 없는 사진 {len(no_url)}장: {', '.join(no_url)}")
    n = write_labels_csv(entries + new, LABELS_CSV)
    print(f"라벨 달 항목 {n}개 → {LABELS_CSV} (이미 채운 칸은 그대로 두었다)")
    return 0


# ── 라벨 CSV ──
def read_csv_rows(path: Path) -> list[dict]:
    """엑셀이 CSV 를 cp949 로 다시 저장해도 읽는다."""
    for enc in ("utf-8-sig", "cp949"):
        try:
            with open(path, newline="", encoding=enc) as f:
                return list(csv.DictReader(f))
        except UnicodeDecodeError:
            continue
    raise ValueError(f"{path}: UTF-8 도 cp949 도 아니다 — 엑셀에서 'CSV UTF-8' 로 다시 저장")


def _row_of(e: dict) -> dict:
    return {"file": e["file"], "stratum": e.get("stratum", ""), "item": e.get("item", ""),
            "photo_type": e.get("photo_type", ""), "wear_level": e.get("wear_level", ""),
            "text_level": e.get("text_level", ""), "key_texts": "|".join(e.get("key_texts") or []),
            "ambiguous": "y" if e.get("ambiguous") else "", "note": e.get("note", "")}


def write_labels_csv(entries: list[dict], path: Path, *, include_labeled: bool = False) -> int:
    """라벨 달 항목(빈 것 · 초안)을 CSV 로. 파일이 이미 있으면 거기 채워 둔 칸을 살린다 —
    라벨을 반쯤 달고 사진을 더 모아 add 해도 작업이 날아가지 않게."""
    kept = {}
    if path.exists():
        for r in read_csv_rows(path):
            name = (r.get("file") or "").strip()
            if name and any((r.get(k) or "").strip() for k in FILLED_COLS):
                kept[name] = r
    rows = [e for e in entries if include_labeled or needs_label(e)]
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".csv.tmp")
    with open(tmp, "w", newline="", encoding="utf-8-sig") as f:   # 엑셀이 한글을 깨지 않게 BOM
        w = csv.DictWriter(f, fieldnames=LABEL_COLS, extrasaction="ignore")
        w.writeheader()
        for e in rows:
            row = _row_of(e)
            if e["file"] in kept:
                row.update({k: kept[e["file"]].get(k, "") or "" for k in FILLED_COLS})
            w.writerow(row)
    os.replace(tmp, path)
    return len(rows)


def parse_label_row(row: dict) -> tuple[dict | None, list[str], list[str]]:
    """CSV 한 줄 → (넣을 값, 오류, 경고). 라벨 칸이 전부 비었으면 (None, [], []) — 아직 안 단 줄."""
    get = lambda k: (row.get(k) or "").strip()   # noqa: E731
    if not any(get(k) for k in FILLED_COLS):
        return None, [], []
    vals = {k: get(k).lower() for k in ENUMS}
    if not any(vals.values()):
        return None, ["photo_type · wear_level · text_level 이 비었는데 다른 칸만 채웠다"], []
    errors = [f"{k}={get(k)!r} (가능: {'/'.join(v)})" for k, v in ENUMS.items() if vals[k] not in v]
    amb = get("ambiguous").lower()
    if amb not in ("", "y", "yes", "1"):
        errors.append(f"ambiguous={amb!r} (y 또는 빈칸)")
    if errors:
        return None, errors, []
    key_texts = [t.strip() for t in get("key_texts").split("|") if t.strip()]
    warnings = [f"key_texts {t!r} — 엑셀이 날짜·숫자로 바꾼 것 같다 (원래 글자인지 확인)"
                for t in key_texts if _MANGLED.match(t)]
    return {"item": get("item"), **vals, "key_texts": key_texts,
            "ambiguous": amb != "", "note": get("note")}, [], warnings


def _changes_human_label(e: dict, vals: dict) -> bool:
    """test 항목의 사람 라벨을 바꾸려 하나 — 결과를 보고 정답을 옮기는 걸 막는다."""
    if e.get("split") != "test" or e.get("labeled_by") in ("", DRAFT, None):
        return False
    return any(e.get(k) != vals[k] for k in (*ENUMS, "key_texts"))


def merge_labels(entries: list[dict], rows: list[dict], by: str, *,
                 relabel: bool = False) -> tuple[int, list[str], list[str]]:
    """CSV 줄들을 entries 에 반영 → (반영 수, 오류, 경고). 오류가 있으면 entries 를 바꾸지 않는다."""
    index = {e["file"]: e for e in entries}
    updates, errors, warnings, seen = [], [], [], set()
    for row in rows:
        name = (row.get("file") or "").strip()
        if not any((v or "").strip() for v in row.values() if isinstance(v, str)):
            continue                                   # 엑셀이 남긴 빈 줄
        if name not in index:
            errors.append(f"{name or '(빈 file)'}: dataset.json 에 없다")
            continue
        if name in seen:
            errors.append(f"{name}: 두 줄 있다")
            continue
        seen.add(name)
        vals, errs, warns = parse_label_row(row)
        errors += [f"{name}: {m}" for m in errs]
        warnings += [f"{name}: {m}" for m in warns]
        if vals is None:
            continue
        if not relabel and _changes_human_label(index[name], vals):
            errors.append(f"{name}: test 사진의 라벨({index[name]['labeled_by']})을 바꾸려 한다 — "
                          "정말 바꿀 거면 --relabel")
            continue
        updates.append((index[name], vals))
    if errors:
        return 0, errors, warnings
    for e, vals in updates:
        e.update(vals, labeled_by=by)
    return len(updates), [], warnings


def cmd_export(a) -> int:
    n = write_labels_csv(load_dataset(), LABELS_CSV, include_labeled=a.all)
    print(f"{n}개 → {LABELS_CSV}")
    return 0


def cmd_merge(a) -> int:
    try:
        rows = read_csv_rows(Path(a.csv))
    except ValueError as e:
        print(e)
        return 1
    entries = load_dataset()
    n, errors, warnings = merge_labels(entries, rows, a.by, relabel=a.relabel)
    for m in warnings:
        print(f"경고: {m}")
    if errors:
        print("반영하지 않았다 — 고친 뒤 다시:")
        for m in errors:
            print(f"  {m}")
        return 1
    save_dataset(entries)
    left = sum(needs_label(e) for e in entries)
    print(f"{n}개 반영 (라벨 남은 항목 {left}개)")
    return 0


# ── 현황 ──
def status_table(entries: list[dict], inbox_names: list[str]) -> str:
    lines = ["| 층 | test | dev | inbox | 라벨 남음 |", "|---|---|---|---|---|"]
    for code, (tq, dq) in STRATA.items():
        es = [e for e in entries if e.get("stratum") == code]
        t = sum(e.get("split") == "test" for e in es)
        d = sum(e.get("split") == "dev" for e in es)
        inbox = sum(stratum_of(n) == code for n in inbox_names)
        lines.append(f"| {code} | {t}/{tq} | {d}/{dq} | {inbox} | {sum(needs_label(e) for e in es)} |")
    other = [e for e in entries if e.get("stratum") not in STRATA]
    if other:
        lines.append(f"| (층 목표 밖 · 기존·파일럿) | {sum(e.get('split') == 'test' for e in other)} | "
                     f"{sum((e.get('split') or 'dev') == 'dev' for e in other)} | | "
                     f"{sum(needs_label(e) for e in other)} |")
    return "\n".join(lines)


def problems(entries: list[dict], images_dir: Path, split_log: dict[str, str]) -> list[str]:
    """dataset.json · images/ · splits.jsonl 이 서로 안 맞는 곳."""
    out = []
    files = {e["file"] for e in entries}
    on_disk = {p.name for p in images_dir.iterdir()
               if p.is_file() and p.suffix.lower() in IMAGE_EXTS} if images_dir.exists() else set()
    out += [f"images/ 에 없다: {n}" for n in sorted(files - on_disk)]
    out += [f"dataset.json 에 없는 사진: {n}" for n in sorted(on_disk - files)]
    for e in entries:
        logged = split_log.get(e["file"])
        if logged is not None and (e.get("split") or "dev") != logged:
            out.append(f"split 이 바뀌었다: {e['file']} ({logged} → {e.get('split')})")
    return out


def cmd_status(_a) -> int:
    photos, other = inbox_files(INBOX)
    names = [p.name for p in photos]
    entries = load_dataset()
    print(status_table(entries, names))
    bad = [n for n in names if stratum_of(n) is None]
    if bad:
        print(f"\n이름이 규칙에 안 맞는 inbox 사진: {', '.join(bad)}")
    if other:
        print(f"\ninbox 에 사진이 아닌 파일 (jpg · png · webp 만 받는다): {', '.join(other)}")
    for m in problems(entries, IMAGES, load_split_log()):
        print(f"\n확인 필요: {m}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status").set_defaults(fn=cmd_status)
    p = sub.add_parser("add")
    p.add_argument("--pilot", action="store_true", help="층 목표에 세지 않고 전부 dev (파일럿)")
    p.set_defaults(fn=cmd_add)
    p = sub.add_parser("export")
    p.add_argument("--all", action="store_true", help="라벨 단 항목도 포함")
    p.set_defaults(fn=cmd_export)
    p = sub.add_parser("merge")
    p.add_argument("csv")
    p.add_argument("--by", required=True, help="라벨 단 사람 이름")
    p.add_argument("--relabel", action="store_true", help="test 사진의 사람 라벨을 바꾸는 것도 허용")
    p.set_defaults(fn=cmd_merge)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
