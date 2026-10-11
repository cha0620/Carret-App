"""정직함 정답 라벨 페이지 (10-11) — "검사를 통과한 생성 결과가 정말 원본과 같은가"를 사람이 단다.
자동 지표 후보(원본 질문 검사 · 두 장 비교 · OCR · DINO)를 이 라벨과 맞춰 보고 가장 사람 같은 걸 고른다
(study/honesty-metric/11-carret-design.md).

    cd backend
    python eval/label_honesty.py              # http://localhost:8002 — 원본 · 결과 나란히, 키보드로 라벨
    python eval/label_honesty.py --all        # 게이트 탈락 · 검사 안 한 생성본까지

대상: results/runs/*/storage 의 inspect 중 mode=generate (기본은 gate_passed=True 만 — 보증 대상).
라벨: data/honesty_labels.json {case_id: {"same": bool, "added": bool, "missing": bool, "changed": bool,
"unsure": bool, "memo": str, "at": int}}. 사람이 판단을 흐리지 않게 게이트 · 유사도 같은 자동 신호는 보여 주지 않는다.
돈이 드는 호출 없음 — 저장된 파일만 읽는다.
"""
import argparse
import json
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUNS = HERE / "results" / "runs"
LABELS = HERE / "data" / "honesty_labels.json"
FLAGS = ("same", "added", "missing", "changed", "unsure")


def _source_names(run_dir: Path) -> dict:
    """file_id → 원본 파일 이름 (results.jsonl 이 있는 옛 실행만 — 같은 사진이 여러 실행에 있는지 보려고)."""
    f = run_dir / "results.jsonl"
    if not f.exists():
        return {}
    out = {}
    for line in f.read_text(encoding="utf-8").splitlines():
        try:
            x = json.loads(line)
        except ValueError:
            continue
        if x.get("file_id"):
            out[x["file_id"]] = x.get("file")
    return out


def cases(include_all: bool = False) -> list[dict]:
    out = []
    for q in sorted(RUNS.glob("*/storage/quality/*_inspect.json")):
        try:
            d = json.loads(q.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if d.get("mode") != "generate" or (not include_all and d.get("gate_passed") is not True):
            continue
        storage, run = q.parent.parent, q.parts[-4]
        stem = q.name[: -len("_inspect.json")]
        fid = stem[:32]
        result = storage / "result" / f"{stem}.jpg"
        original = next(iter(sorted((storage / "original").glob(fid + ".*"))), None)
        if not result.exists() or original is None:
            continue
        out.append({"id": f"{run}/{stem}", "run": run, "item": d.get("item") or "",
                    "source": _source_names(storage.parent).get(fid) or "",
                    "original": str(original.relative_to(RUNS)), "result": str(result.relative_to(RUNS))})
    return out


def load_labels() -> dict:
    try:
        return json.loads(LABELS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_label(case_id: str, body: dict) -> dict:
    labels = load_labels()
    label = {k: bool(body.get(k)) for k in FLAGS}
    label["memo"] = str(body.get("memo") or "")[:500]
    label["at"] = int(time.time())
    labels[case_id] = label
    LABELS.parent.mkdir(parents=True, exist_ok=True)
    tmp = LABELS.with_suffix(".tmp")
    tmp.write_text(json.dumps(labels, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(LABELS)   # 쓰다 끊겨도 옛 라벨이 깨지지 않게
    return label


PAGE = (HERE / "label_honesty.html")


class Handler(SimpleHTTPRequestHandler):
    """runs/ 의 이미지만 서빙 + 라벨 API. 127.0.0.1 전용."""
    case_list: list = []

    def _json(self, data, status=200):
        b = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            b = PAGE.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)
            return
        if self.path == "/api/cases":
            return self._json({"cases": self.case_list, "labels": load_labels()})
        if not self.path.startswith("/runs/"):
            return self.send_error(404)
        self.path = self.path[len("/runs"):]
        return super().do_GET()

    def do_POST(self):
        if self.path != "/api/label":
            return self.send_error(404)
        try:
            body = json.loads(self.rfile.read(min(int(self.headers.get("Content-Length") or 0), 10_000)))
        except ValueError:
            return self._json({"detail": "형식 오류"}, 422)
        cid = body.get("id")
        if cid not in {c["id"] for c in self.case_list}:
            return self._json({"detail": "없는 사진"}, 404)
        return self._json(save_label(cid, body))

    def log_message(self, *a):
        pass


def summary(case_list: list, labels: dict) -> dict:
    done = [labels[c["id"]] for c in case_list if c["id"] in labels]
    sure = [x for x in done if not x.get("unsure")]
    differ = [x for x in sure if not x.get("same")]
    return {"cases": len(case_list), "labeled": len(done), "unsure": len(done) - len(sure),
            "same": len(sure) - len(differ), "differ": len(differ),
            **{k: sum(x.get(k, False) for x in sure) for k in ("added", "missing", "changed")}}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8002)
    ap.add_argument("--all", action="store_true", help="게이트 탈락 · 검사 안 한 생성본까지")
    ap.add_argument("--summary", action="store_true", help="서버 없이 라벨 집계만")
    ap.add_argument("--labels", type=Path, default=None, help="라벨 파일 (기본 data/honesty_labels.json — 시험용으로 바꿀 때)")
    a = ap.parse_args()
    if a.labels:
        global LABELS
        LABELS = a.labels
    cl = cases(a.all)
    if a.summary:
        print(json.dumps(summary(cl, load_labels()), ensure_ascii=False, indent=1))
        return
    Handler.case_list = cl
    print(f"라벨 대상 {len(cl)}장 — http://localhost:{a.port}")
    ThreadingHTTPServer(("127.0.0.1", a.port), partial(Handler, directory=str(RUNS))).serve_forever()


if __name__ == "__main__":
    main()
