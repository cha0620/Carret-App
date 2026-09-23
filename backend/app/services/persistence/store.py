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


def save_feedback(file_id, preset_key, rating, comment=None, source="user"):
    """source="user"(기본, 실사용자 UI 제출)는 항상 덮어쓴다.
    source="agent"(자동 피드백 에이전트)는 이미 실사용자 피드백이 있으면
    건드리지 않는다 — 진짜 신호를 합성 신호가 지우면 안 되므로.

    체크(SELECT)와 쓰기(INSERT)를 분리하면 동시 요청(FastAPI 스레드풀)에서
    레이스가 난다 — 에이전트 스레드가 "user 없음"을 본 직후, user 쓰기가
    끼어들었다가 에이전트 쓰기가 뒤늦게 덮어쓸 수 있음. 그래서 판단까지
    전부 하나의 원자적 UPSERT 문 안(CASE WHEN)에 넣는다: 기존 행이 user고
    이번 쓰기가 user가 아니면 기존 값을 그대로 유지."""
    with db.get_conn() as c:
        c.execute("""
            INSERT INTO feedbacks(file_id, preset_key, rating, comment, source)
            VALUES (?,?,?,?,?)
            ON CONFLICT(file_id, preset_key) DO UPDATE SET
              rating = CASE WHEN feedbacks.source='user' AND excluded.source!='user'
                            THEN feedbacks.rating ELSE excluded.rating END,
              comment = CASE WHEN feedbacks.source='user' AND excluded.source!='user'
                             THEN feedbacks.comment ELSE excluded.comment END,
              source = CASE WHEN feedbacks.source='user' AND excluded.source!='user'
                            THEN feedbacks.source ELSE excluded.source END,
              updated_at = CASE WHEN feedbacks.source='user' AND excluded.source!='user'
                                THEN feedbacks.updated_at ELSE CURRENT_TIMESTAMP END
        """, (file_id, preset_key, rating, comment, source))


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