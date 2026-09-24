"""원본/결과/피드백 저장소 (SQLite)."""
import json

from app.core import db


def record_original(file_id, ext, source, original_name=None, size_bytes=None):
    """원본이 storage에 저장될 때마다 같이 불러서 메타데이터를 남긴다.
    UNIQUE(file_id) — 같은 file_id로 두 번 불려도(재시도 등) 조용히 무시.
    original_name은 클라이언트가 주는 값(파일명/URL)이라 길이 상한을 둔다."""
    if original_name is not None:
        original_name = original_name[:500]
    with db.get_conn() as c:
        c.execute("""
            INSERT INTO originals(file_id, ext, source, original_name, size_bytes)
            VALUES (?,?,?,?,?)
            ON CONFLICT(file_id) DO NOTHING
        """, (file_id, ext, source, original_name, size_bytes))


def get_original(file_id):
    with db.get_conn() as c:
        row = c.execute(
            "SELECT * FROM originals WHERE file_id=?", (file_id,)).fetchone()
    return dict(row) if row else None


def record_result(file_id, preset_key, result_name, item,
                  considered, gate_passed, bubbles, elapsed_s=None):
    with db.get_conn() as c:
        c.execute("""
            INSERT INTO results(file_id, preset_key, result_name, item,
                                considered, gate_passed, bubbles, elapsed_s)
            VALUES (?,?,?,?,?,?,?,?)
            ON CONFLICT(file_id, preset_key) DO UPDATE SET
              result_name=excluded.result_name, item=excluded.item,
              considered=excluded.considered, gate_passed=excluded.gate_passed,
              bubbles=excluded.bubbles, elapsed_s=excluded.elapsed_s
        """, (file_id, preset_key, result_name, item,
              json.dumps(considered, ensure_ascii=False),
              None if gate_passed is None else int(gate_passed),
              json.dumps(bubbles, ensure_ascii=False), elapsed_s))


FEEDBACK_TAGS = ("defect_lost", "text_broken", "shape_changed", "color_changed",
                 "framing", "background", "bubble_wrong", "good")


def save_feedback(file_id, preset_key, rating, comment=None, source="user", tags=None):
    """(file_id, preset_key, source) 마다 한 줄 — 사람(user)과 에이전트(agent) 피드백이
    나란히 남는다. 같은 source 로 다시 쓰면 갱신(upsert).
    (예전엔 한 줄뿐이라 에이전트가 사람 것을 덮지 않도록 CASE WHEN 으로 막았는데,
    이제는 줄이 달라서 서로 덮을 일이 없다.)"""
    tags_json = json.dumps([t for t in (tags or []) if t in FEEDBACK_TAGS])
    with db.get_conn() as c:
        c.execute("""
            INSERT INTO feedbacks(file_id, preset_key, rating, comment, source, tags)
            VALUES (?,?,?,?,?,?)
            ON CONFLICT(file_id, preset_key, source) DO UPDATE SET
              rating=excluded.rating, comment=excluded.comment, tags=excluded.tags,
              updated_at=CURRENT_TIMESTAMP
        """, (file_id, preset_key, rating, comment, source, tags_json))


def _row(r):
    if r is None:
        return None
    d = dict(r)
    d["tags"] = json.loads(d["tags"]) if d.get("tags") else []
    return d


def get_feedbacks(file_id, preset_key) -> dict:
    """{"user": row|None, "agent": row|None} — 둘 다 보여줄 때."""
    with db.get_conn() as c:
        rows = c.execute(
            "SELECT * FROM feedbacks WHERE file_id=? AND preset_key=?",
            (file_id, preset_key)).fetchall()
    out = {"user": None, "agent": None}
    for r in rows:
        out[r["source"] if r["source"] in out else "user"] = _row(r)
    return out


def get_feedback(file_id, preset_key):
    """대표 피드백 한 줄 — 사람 것이 있으면 사람 것, 없으면 에이전트 것."""
    fb = get_feedbacks(file_id, preset_key)
    return fb["user"] or fb["agent"]


def get_result(file_id, preset_key):
    with db.get_conn() as c:
        row = c.execute(
            "SELECT * FROM results WHERE file_id=? AND preset_key=?",
            (file_id, preset_key)).fetchone()
    return dict(row) if row else None