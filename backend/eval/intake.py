"""모은 사진 정리 — data/images/inbox/ → images/ + dataset.json, 라벨은 CSV 로 주고받는다.

    cd backend
    python eval/intake.py status                              # 층별 현황 · 라벨 남은 수 · 이상한 파일
    python eval/intake.py add                                 # inbox 사진을 dataset.json 에 추가 (dev/test 나눔)
    python eval/intake.py add --pilot                         # 파일럿: 층 목표에 세지 않고 전부 dev
    python eval/intake.py add --supplement                    # 태그 보충 검색어로 모은 사진: 역시 층 목표 밖 · dev
    python eval/intake.py export                              # 라벨 빈 항목 → data/images/inbox/labels.csv
    python eval/intake.py merge data/images/inbox/labels.csv --by 이름   # 채운 라벨을 dataset.json 에

inbox 에는 게시글마다 폴더 하나 `<층 코드>_<아무거나>/` (코드는 STRATA), 그 안에 사진을 게시글 순서대로
1.jpg, 2.jpg … (사진 한 장짜리는 `<층 코드>_<아무거나>.jpg` 파일 하나도 된다). 게시글 주소는
data/images/inbox/urls.txt 에 `폴더이름 주소` 로 한 줄씩. 모으는 규칙은 COLLECT.md.

dev/test: 층마다 목표 수만큼 자리(dev·test)를 미리 섞어 두고, 그 층에 k 번째로 들어온 게시글이 k 번째 자리를
받는다 (게시글의 나머지 사진은 첫 사진을 따른다) — 조금씩 add 해도 모은 순서(=검색어 순서)와 무관하게 dev 가 흩어진다. 한 번 단 split 은
splits.jsonl 에 남기고, status 가 dataset.json 과 대 본다 (test 동결).
"""
import argparse
import contextlib
import csv
import fcntl
import hashlib
import json
import os
import random
import re
import shutil
import sys
import tempfile
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE.parent) not in sys.path:
    sys.path.insert(0, str(HERE.parent))

from app.services import coverage  # noqa: E402
IMAGES = HERE / "data" / "images"
INBOX = IMAGES / "inbox"
DATASET = HERE / "data" / "dataset.json"
SPLIT_LOG = HERE / "data" / "splits.jsonl"
LABELS_CSV = INBOX / "labels.csv"

# 층 코드 → (test 목표, dev 목표). COLLECT.md 의 표와 같게
# 10-04: light · heavy → wear (하자 수준 대신 "흔적이 있나"로 보기로 해서 둘을 나눌 이유가 없다)
STRATA = {"none": (25, 5), "wear": (35, 8), "dense": (10, 3),
          "doc": (15, 4), "inside": (8, 2), "edge": (7, 3)}
POST_MAX = 10        # 게시글 하나에서 받는 사진 수 상한 — 넘으면 앞에서부터 이만큼만 옮기고 원래 장수는 post_total 에
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
IGNORED_INBOX = {"urls.txt", "labels.csv"}
ENUMS = {"photo_type": ("document", "inside_view", "product"),
         "wear_level": ("none", "light", "heavy"),
         "text_level": ("none", "simple", "dense")}
# 물건 종류 — 앱 종류(coverage.CATEGORIES) + 앱에선 other 인 것 중 따로 세고 싶은 것
DATA_CATEGORIES = (*coverage.CATEGORIES, "watch", "media")
# 사진 성격 태그 (여러 개) — 어느 층 사진에든 단다. COLLECT.md "edge 태그" 표와 같게.
# 묶음: judge 판단이 애매(edge 층의 이유) · cut 오리기·검출 · gen 생성에서 망가지기 쉬움 · privacy 가릴 것
EDGE_TAGS = {
    "intended_damage": "judge", "text_is_item": "judge", "prop": "judge",
    "transparent": "cut", "glossy_material": "cut", "occluded": "cut", "hand_held": "cut", "cut_off": "cut",
    "watermark": "gen", "small_text": "gen", "pattern": "gen", "embroidery": "gen", "tag": "gen",
    "subtle_wear": "gen", "packaging": "gen",
    "private": "privacy",
}
# 최소 수는 검색어로 노릴 수 있는 태그만 (COLLECT.md 보충 검색어 표와 같게). 나머지는 자연히 나온 만큼 —
# judge 태그는 edge 층이 채우고, 가려짐 · 워터마크 같은 건 검색으로 노릴 수 없다. "multi" 는 item_count ≥ 2
TAG_MIN = {"transparent": 5, "glossy_material": 5, "packaging": 5, "pattern": 5, "multi": 5}
LABEL_COLS = ["file", "stratum", "item", "category", "photo_type", "wear_level", "text_level",
              "key_texts", "item_count", "edge_tags", "ambiguous", "note"]
FILLED_COLS = ["item", "category", "photo_type", "wear_level", "text_level", "key_texts",
               "item_count", "edge_tags", "ambiguous", "note"]
DRAFT = "claude-draft"
# test 라벨 동결에서 보는 칸 — 결과를 보고 바꾸면 집계가 움직이는 것 전부
FROZEN = (*ENUMS, "key_texts", "category", "item_count", "edge_tags", "ambiguous")
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
    fd, tmp = tempfile.mkstemp(dir=DATASET.parent, prefix=".dataset-", suffix=".tmp")   # 프로세스마다 다른 임시 파일
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(f"[\n{body}\n]\n" if entries else "[]\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, DATASET)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


@contextlib.contextmanager
def dataset_lock():
    """dataset.json 을 읽고 고쳐 쓰는 동안 다른 프로세스(board.py 등)가 끼어들지 못하게 — 파일 락."""
    with open(DATASET.with_suffix(".json.lock"), "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


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
                                "split": e["split"], "post": e.get("post", ""),
                                "at": e["collected_at"]}, ensure_ascii=False) + "\n")


def is_labeled(e: dict) -> bool:
    return all(e.get(k) in ENUMS[k] for k in ENUMS)


def needs_label(e: dict) -> bool:
    """사람이 라벨을 달아야 하나 — 비었거나 초안(claude-draft). 게시글 추가 사진은 아직 아니다."""
    return not is_extra(e) and (not is_labeled(e) or e.get("labeled_by") == DRAFT)


def is_extra(e: dict) -> bool:
    """게시글의 두 번째 사진부터 — 층 · 한 장 평가 · 라벨 대상이 아니다 (여러 장 라벨은 아직 없다)."""
    try:
        return int(e.get("post_index") or 1) > 1
    except (TypeError, ValueError):       # 손으로 고친 값이 숫자가 아니면 첫 사진으로 본다
        return False


def stratum_of(name: str) -> str | None:
    code = name.split("_", 1)[0]
    return code if "_" in name and code in STRATA else None


# ── inbox ──
def inbox_files(inbox: Path) -> tuple[list[Path], list[str]]:
    """(게시글, 형식이 안 맞는 파일 이름). 게시글 = 폴더 하나(사진 여러 장) 또는 사진 파일 하나.
    urls.txt · labels.csv · 숨김 파일은 뺀다."""
    if not inbox.exists():
        return [], []
    posts, other = [], []
    for p in sorted(inbox.iterdir()):
        if p.name in IGNORED_INBOX or p.name.startswith("."):
            continue
        if p.is_dir():
            posts.append(p)
            other += [f"{p.name}/{q.name}" for q in sorted(p.iterdir())
                      if not q.name.startswith(".") and (q.is_dir() or q.suffix.lower() not in IMAGE_EXTS)]
        elif p.suffix.lower() in IMAGE_EXTS:
            posts.append(p)
        else:
            other.append(p.name)
    return posts, other


def post_photos(post: Path) -> tuple[list[Path], list[str]]:
    """게시글 폴더 속 사진을 게시글 순서로 → (사진, 오류). 파일 하나면 그것만.
    이름은 숫자만(1.jpg, 2.jpg, …), 1 부터 빠짐 · 겹침 없이 — 첫 사진이 층 · 라벨 · split 을 정하니 순서를 짐작하지 않는다."""
    if not post.is_dir():
        return [post], []
    photos = [q for q in post.iterdir() if q.is_file() and not q.name.startswith(".")
              and q.suffix.lower() in IMAGE_EXTS]
    bad = sorted(q.name for q in photos if not re.fullmatch(r"[0-9]+", q.stem))
    if bad:
        return [], [f"{post.name}/: 사진 이름은 게시글 순서 번호만 (1.jpg, 2.jpg …) — {', '.join(bad)}"]
    nums = sorted(int(q.stem) for q in photos)
    if nums != list(range(1, len(photos) + 1)):
        return [], [f"{post.name}/: 번호가 1 부터 빠짐 · 겹침 없이 이어져야 한다 — 지금 {nums}"]
    return sorted(photos, key=lambda q: int(q.stem)), []


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


def plan_add(posts: list[Path], urls: dict[str, str], entries: list[dict],
             images_dir: Path, *, pilot: bool = False,
             supplement: bool = False) -> tuple[list[dict], list[str]]:
    """inbox 게시글 → (새 항목, 오류). 오류가 하나라도 있으면 아무것도 옮기지 않는다.
    게시글 = 폴더(`<층>_<이름>/1.jpg, 2.jpg …`) 또는 사진 파일 하나. 폴더의 사진은 `<폴더 이름>_pNN.<확장자>` 로 저장.
    층 목표 · split 은 게시글 단위 — 첫 사진(post_index 1)이 층 · split 을 갖고, 나머지는 같은 split 을 따른다.
    pilot: 규칙 전에 모았거나 결과를 먼저 볼 사진 — 층 목표에 세지 않고(stratum 빈 값,
    legacy_stratum 에 층 코드) 전부 dev. test 자리를 쓰지 않는다.
    supplement: 태그를 채우려고 보충 검색어로 모은 사진 — 파일럿처럼 층 목표 밖 · 전부 dev
    (실패하기 쉬운 성격을 노려 모았으니 test 에 넣으면 통과율이 실제보다 낮게 나온다)."""
    errors, new = [], []
    known = {e["file"] for e in entries}
    known_posts = {e.get("post") or Path(e["file"]).stem for e in entries}
    seen_urls = {e["url"]: e.get("post") or e["file"] for e in entries if e.get("url")}
    digests = {_digest(images_dir / e["file"]): e["file"]
               for e in entries if (images_dir / e["file"]).exists()}
    outside = pilot or supplement                       # 층 목표 밖 · 전부 dev
    count = {s: sum(e.get("stratum") == s for e in entries) for s in STRATA}
    for post in sorted(posts):
        name = post.name if post.is_dir() else post.stem
        stratum = stratum_of(post.name)
        if stratum is None:
            errors.append(f"{post.name}: 이름이 <층 코드>_... 가 아니다 (코드: {', '.join(STRATA)})")
            continue
        photos, bad = post_photos(post)
        if bad:
            errors += bad
            continue
        if not photos:
            errors.append(f"{post.name}/: 사진이 없다")
            continue
        total = len(photos)
        photos = photos[:POST_MAX]                          # 나머지는 옮기지 않고 지운다 (post_total 에 원래 장수)
        url = urls.get(post.name, "") or urls.get(name, "")
        if url and url in seen_urls:
            errors.append(f"{post.name}: 같은 게시글 주소가 이미 있다 ({seen_urls[url]}) — 같은 판매자 · 같은 글은 하나만")
            continue
        files = ([post.name] if not post.is_dir()
                 else [f"{name}_p{i:02d}{q.suffix.lower()}" for i, q in enumerate(photos, 1)])
        clash = [f for f in files if f in known or (images_dir / f).exists()]
        if clash:
            errors.append(f"{post.name}: 같은 이름이 이미 있다" + (f" ({', '.join(clash)})" if post.is_dir() else ""))
            continue
        if name in known_posts:
            errors.append(f"{post.name}: 같은 게시글 이름이 이미 있다")
            continue
        # 첫 사진이 이미 있으면 같은 게시글을 두 번 넣는 것 — 오류. 두 번째부터는 판매자가 같은 사진을 또 올리거나
        # 다른 글과 같은 공식 이미지를 쓰는 게 흔하다 — 고르지 않고 그대로 받되 dup_of 로 남긴다
        stored, dup_of, first_dup = {}, {}, None
        for i, (q, f) in enumerate(zip(photos, files), 1):
            d = _digest(q)
            prev = digests.get(d) or stored.get(d)
            if prev and i == 1:
                first_dup = prev
                break
            if prev:
                dup_of[f] = prev
            stored.setdefault(d, f)
        if first_dup:
            errors.append(f"{post.name}: 첫 사진이 {first_dup} 와 같은 사진이다")
            continue
        count[stratum] += 0 if outside else 1
        if not outside and count[stratum] > sum(STRATA[stratum]):
            errors.append(f"{post.name}: {stratum} 층 목표({sum(STRATA[stratum])}개)를 넘는다 — 이 층은 다 모았다")
            continue
        known_posts.add(name)
        known.update(files)                                  # 이번 add 안에서 같은 이름이 또 나오면 잡게
        for d, f in stored.items():
            digests.setdefault(d, f)
        if url:
            seen_urls[url] = name
        for i, (q, f) in enumerate(zip(photos, files), 1):
            e = {"file": f, "url": url, "item": "",
                 "photo_type": "", "wear_level": "", "text_level": "", "key_texts": [],
                 "note": "", "labeled_by": "", "stratum": stratum if i == 1 else "",
                 "collected_at": date.today().isoformat(), "ambiguous": False,
                 "post": name, "post_index": i, "post_size": len(photos), "source": str(q)}
            if total > len(photos):
                e["post_total"] = total
            if f in dup_of:
                e["dup_of"] = dup_of[f]
            if outside:
                e.update(stratum="", split="dev")
                if i == 1:
                    e["legacy_stratum"] = stratum
                e["pilot" if pilot else "supplement"] = True
            new.append(e)
    return new, errors


def follow_post_splits(new: list[dict]) -> None:
    """게시글의 나머지 사진이 첫 사진의 split 을 따른다 (같은 게시글이 test · dev 로 갈리면 새어 나간다)."""
    head = {e["post"]: e.get("split") for e in new if not is_extra(e)}
    for e in new:
        if is_extra(e):
            e["split"] = head[e["post"]]


def cmd_add(a) -> int:
    pilot = bool(getattr(a, "pilot", False))
    supplement = bool(getattr(a, "supplement", False))
    if pilot and supplement:
        print("--pilot 과 --supplement 는 같이 못 쓴다")
        return 1
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
    new, errors = plan_add(photos, urls, entries, IMAGES, pilot=pilot, supplement=supplement)
    if errors:
        print("아무것도 옮기지 않았다 — 고친 뒤 다시:")
        for m in errors:
            print(f"  {m}")
        return 1
    stray = sorted(set(urls) - {p.name for p in photos} - {p.stem for p in photos}
                   - {e["file"] for e in entries} - {e.get("post", "") for e in entries})
    if stray:
        print(f"경고: urls.txt 에만 있고 inbox 에 없는 이름 — {', '.join(stray)}")
    assign_splits(entries, [e for e in new if e["stratum"]])          # 파일럿 · 보충은 이미 dev
    follow_post_splits(new)
    sources = {e["file"]: Path(e.pop("source")) for e in new}
    # 복사 → 저장 → inbox 원본 삭제. 저장 전에 멈추면 복사본을 지워 원래대로
    copied = []
    try:
        for e in new:
            shutil.copy2(sources[e["file"]], IMAGES / e["file"])
            copied.append(IMAGES / e["file"])
        save_dataset(entries + new)
    except BaseException:
        for p in copied:
            p.unlink(missing_ok=True)
        raise
    append_split_log(new)
    for src in sources.values():
        src.unlink(missing_ok=True)
    for post in photos:
        if post.is_dir():                             # 다 옮겼으면 폴더째 지운다 (숨김 파일 · 10장 넘는 나머지 포함)
            shutil.rmtree(post, ignore_errors=True)
    heads = [e for e in new if not is_extra(e)]
    no_url = [e["post"] if e.get("post_size", 1) > 1 else e["file"] for e in heads if not e["url"]]   # urls.txt 에 적는 이름
    kind = " (파일럿 — 층 목표 밖)" if pilot else " (보충 — 층 목표 밖)" if supplement else ""
    print(f"게시글 {len(heads)}개 · 사진 {len(new)}장 추가{kind} (test {sum(e['split'] == 'test' for e in heads)} · "
          f"dev {sum(e['split'] == 'dev' for e in heads)})")
    if no_url:
        print(f"urls.txt 에 주소가 없는 게시글 {len(no_url)}개: {', '.join(no_url)}")
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
            "category": e.get("category", ""),
            "photo_type": e.get("photo_type", ""), "wear_level": e.get("wear_level", ""),
            "text_level": e.get("text_level", ""), "key_texts": "|".join(e.get("key_texts") or []),
            "item_count": e.get("item_count") or "", "edge_tags": "|".join(e.get("edge_tags") or []),
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
    rows = [e for e in entries if not is_extra(e) and (include_labeled or needs_label(e))]
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".csv.tmp")
    with open(tmp, "w", newline="", encoding="utf-8-sig") as f:   # 엑셀이 한글을 깨지 않게 BOM
        w = csv.DictWriter(f, fieldnames=LABEL_COLS, extrasaction="ignore")
        w.writeheader()
        for e in rows:
            row = _row_of(e)
            if e["file"] in kept:
                old = kept[e["file"]]                  # 옛 CSV 에 없는 열은 dataset 값을 둔다
                row.update({k: old.get(k) or "" for k in FILLED_COLS if k in old})
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
    category = get("category").lower()
    if category not in DATA_CATEGORIES:
        errors.append(f"category={get('category')!r} (가능: {'/'.join(DATA_CATEGORIES)})")
    count = get("item_count") or "1"
    if not re.fullmatch(r"[0-9]+", count) or int(count) < 1:
        errors.append(f"item_count={count!r} (1 이상 정수, 빈칸이면 1 — 팔 물건 개수)")
    tags = list(dict.fromkeys(t.strip().lower() for t in get("edge_tags").split("|") if t.strip()))
    unknown = [t for t in tags if t not in EDGE_TAGS]
    if unknown:
        errors.append(f"edge_tags {', '.join(unknown)} — 없는 태그 (구분자는 `|`. "
                      "새 태그는 COLLECT.md 표와 EDGE_TAGS 에 먼저)")
    if errors:
        return None, errors, []
    key_texts = [t.strip() for t in get("key_texts").split("|") if t.strip()]
    warnings = [f"key_texts {t!r} — 엑셀이 날짜·숫자로 바꾼 것 같다 (원래 글자인지 확인)"
                for t in key_texts if _MANGLED.match(t)]
    return {"item": get("item"), "category": category, **vals, "key_texts": key_texts,
            "item_count": int(count), "edge_tags": tags,
            "ambiguous": amb != "", "note": get("note")}, [], warnings


def _changes_human_label(e: dict, vals: dict) -> bool:
    """test 항목의 사람 라벨을 바꾸려 하나 — 결과를 보고 정답을 옮기는 걸 막는다."""
    if e.get("split") != "test" or e.get("labeled_by") in ("", DRAFT, None):
        return False
    # 옛 항목에 없던(또는 None · "") 칸을 처음 채우는 건 바꾸는 게 아니다 — 빈 태그 목록 [] 은 "태그 없음"이라는 라벨.
    # 태그는 순서를 안 본다
    def same(k):
        old, new = e[k], vals[k]
        return sorted(_tags(e)) == sorted(new) if k == "edge_tags" else old == new
    return any(e.get(k) not in (None, "") and not same(k) for k in FROZEN)


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
    extras = [e for e in entries if is_extra(e)]
    if extras:
        lines.append(f"| (게시글 2번째 사진부터 · 장수) | {sum(e.get('split') == 'test' for e in extras)} | "
                     f"{sum((e.get('split') or 'dev') == 'dev' for e in extras)} | | — |")
    other = [e for e in entries if e.get("stratum") not in STRATA and not is_extra(e)]
    if other:
        lines.append(f"| (층 목표 밖 · 기존·파일럿) | {sum(e.get('split') == 'test' for e in other)} | "
                     f"{sum((e.get('split') or 'dev') == 'dev' for e in other)} | | "
                     f"{sum(needs_label(e) for e in other)} |")
    return "\n".join(lines)


def _tags(e: dict) -> list[str]:
    t = e.get("edge_tags") or []
    return [t] if isinstance(t, str) else list(t)          # 손으로 고친 JSON 이 문자열이어도 통째 한 태그로


def _is_multi(e: dict) -> bool:
    try:
        return int(e.get("item_count") or 1) >= 2
    except (TypeError, ValueError):
        return False


def tag_table(entries: list[dict]) -> str:
    """edge 태그별 장수. 최소 수(TAG_MIN)는 규칙대로 + 보충 사진으로 채우고, 파일럿 · 기존은 참고로만
    (기존 30장은 실패 모음에만 태그가 붙어 있어 이 열로 태그와 실패를 엮어 읽으면 안 된다)."""
    ruled = [e for e in entries if e.get("stratum") in STRATA]
    supp = [e for e in entries if e.get("stratum") not in STRATA and e.get("supplement") and not is_extra(e)]
    rest = [e for e in entries if e.get("stratum") not in STRATA and not e.get("supplement") and not is_extra(e)]
    has = {tag: (lambda e, t=tag: t in _tags(e)) for tag in EDGE_TAGS}
    has["multi"] = _is_multi
    groups = {**EDGE_TAGS, "multi": "cut"}
    lines = ["| 태그 | 묶음 | 규칙대로 | 보충 | 최소 | 파일럿·기존 |", "|---|---|---|---|---|---|"]
    for tag, f in has.items():
        n, m = sum(map(f, ruled)), sum(map(f, supp))
        need = TAG_MIN.get(tag)
        mark = "" if need is None else f"{need}{' ⚠' if n + m < need else ''}"
        name = "multi (item_count ≥ 2)" if tag == "multi" else tag
        lines.append(f"| {name} | {groups[tag]} | {n} | {m} | {mark} | {sum(map(f, rest))} |")
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
    by_post: dict[str, set] = {}
    for e in entries:
        if e.get("post"):
            by_post.setdefault(e["post"], set()).add(e.get("split") or "dev")
    out += [f"게시글 안에서 split 이 갈렸다: {p} ({' · '.join(sorted(v))})" for p, v in sorted(by_post.items()) if len(v) > 1]
    return out


def cmd_status(_a) -> int:
    photos, other = inbox_files(INBOX)
    names = [p.name for p in photos]
    entries = load_dataset()
    print(status_table(entries, names))
    print()
    print(tag_table(entries))
    unknown = sorted({t for e in entries for t in _tags(e) if t not in EDGE_TAGS})
    if unknown:
        print(f"\nEDGE_TAGS 에 없는 태그: {', '.join(unknown)}")
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
    p.add_argument("--supplement", action="store_true",
                   help="태그 보충 검색어로 모은 사진 — 층 목표에 세지 않고 전부 dev")
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
    if a.cmd in ("add", "merge"):          # dataset.json 을 고쳐 쓰는 명령 — 결과판(board.py) 체크와 겹치지 않게
        with dataset_lock():
            return a.fn(a)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
