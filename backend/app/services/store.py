"""결과/피드백 저장소 (SQLite)."""
import json

from app.core import db


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


def save_feedback(file_id, preset_key, rating, comment=None):
    with db.get_conn() as c:
        c.execute("""
            INSERT INTO feedbacks(file_id, preset_key, rating, comment)
            VALUES (?,?,?,?)
            ON CONFLICT(file_id, preset_key) DO UPDATE SET
              rating=excluded.rating, comment=excluded.comment,
              updated_at=CURRENT_TIMESTAMP
        """, (file_id, preset_key, rating, comment))


def get_feedback(file_id, preset_key):
    with db.get_conn() as c:
        row = c.execute(
            "SELECT * FROM feedbacks WHERE file_id=? AND preset_key=?",
            (file_id, preset_key)).fetchone()
    return dict(row) if row else None


def get_result(file_id, preset_key):
    with db.get_conn() as c:
        row = c.execute(
            "SELECT * FROM results WHERE file_id=? AND preset_key=?",
            (file_id, preset_key)).fetchone()
    return dict(row) if row else None