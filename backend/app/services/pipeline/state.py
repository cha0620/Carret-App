"""파이프라인 상태(State)와 공용 도구 — 재시도 · 백그라운드 실행."""
import logging
import contextvars
import time
from concurrent.futures import ThreadPoolExecutor
from typing import TypedDict
from app.core.vlm import retryable
from app.prompts.presets import leave_out

logger = logging.getLogger("carret.pipeline")


class State(TypedDict, total=False):
    file_id: str
    preset_key: str            # 스타일 키 (무드, 한 줄이 있으면 무드-해시) — 저장 이름·DB 키
    style_note: str            # 사용자가 적은 무드 한 줄 (clean_note 통과) — 배경에만
    mains: list | None         # 판매자가 본품으로 고른 번호 (sell 안) — 없으면 분석 role (10-09)
    sell: list | None          # 사용자가 고른 팔 물건 (analyze objects 의 번호) — None 이면 analyze 판단대로
    answer_count: int | None   # eval 정답 개수 — 고르는 화면 없이 정답대로 (10-03)
    leave_out: list            # 팔지 않는다고 고른 물건 이름 — 생성 프롬프트에서 빼라고
    leave_out_boxes: list      # 그 물건들 박스 — 배경 교체에서 지운다
    sell_boxes: list           # 고른 물건 박스 — 지울 박스와 겹치는 곳은 남긴다
    preset: dict
    original: bytes
    item: str                 # ⭐ analyze 산출
    considered: list          # ⭐ analyze 산출 (verify 체크리스트 힌트)
    anchors: list              # 아이덴티티 마크 (category="print") — verify 대상
    photo_type: str | None     # analyze: document | inside_view | product (실패면 None)
    wear_level: str | None     # analyze: none | light | heavy (실패면 None)
    watermark: str | None      # analyze: none | background | on_item (관측만 — 합성 모드는 물건 위 워터마크를 못 지운다)
    detect_failed: bool        # analyze 가 재시도까지 실패 — "지킬 것 없음"과 구분 (검증 불가). 이름은 예전 detect 호환
    verify_failed: bool        # verify 호출이 재시도까지 실패 — "보존됨"과 구분 (검증 불가)
    item_texts: list           # 원본 물건 위 글자 [{text, x1..}] → generate 프롬프트
    text_level: str | None     # analyze 가 본 물건 위 글자 수준: none | simple | dense(잔글씨·원본에서도 못 읽음) (실패면 None)
    item_box: dict | None      # 원본에서 물건 위치 (0-1000) — 오리기 범위 (배경 교체 · item_dino)
    item_cut_off: bool         # analyze: 판매 물건이 사진 밖으로 잘림 — 기록만 (10-11: 배경 교체로 보내지 않는다)
    pieces_overlap: bool       # analyze: 세트 구성품끼리 겹쳐 가려짐 — 기록만 (10-11)
    item_count: int            # 팔려는 물건 개수 — analyze 판단, 사용자가 고르면 고른 수, eval 은 정답.
                               #   2개 이상이면 구도 문장을 붙이지 않는다 (10-03 CD 2장)
    gate_retried: bool         # verify 게이트 실패로 재생성을 이미 1회 했나
    gate_note: str             # 그 재생성 때 프롬프트에 붙인 "사라진 하자" 목록
    added_text: list           # 생성본에 새로 생긴 글자 · 로고 (원본에 없던 것) — 있으면 게이트 실패
    gate_added_text: list      # 게이트에서 떨어진 생성본의 added_text (재생성 · 배경 교체 뒤에도 남긴다)
    mode: str                  # "generate" | "composite"(원본 물건 + 배경만 교체) | "composite_failed"
                               #   | "original"(원본 그대로 — inside_view 등)
    composite_reason: str | None   # 생성하지 않은 이유: user_choice (판매자가 [배경만 바꾸기], 10-11 — 생성 전 판단은 지웠다)
    provided_result: bytes     # dev 그래프: generate 대신 쓸 결과 이미지
    composite_error: str | None
    result: bytes
    result_name: str
    gen_attempts: int          # ⭐ generate 시도 횟수 (재생성 게이트용)
    photo_check: dict          # ⭐ {"valid": bool, "reason": str} - 구도/자막 검사
    guard_report: list         # 기준 밖인 관측 신호 목록 (전부 soft) - asdict(GuardResult)
    visual_similarity: float | None   # ⭐ DINOv2 코사인 유사도 (이미지 전체)
    item_similarity: float | None     # 누끼 딴 물건끼리의 DINOv2 유사도 (item_dino 가드)
    item_patch_similarity: float | None   # 같은 누끼 쌍의 DINO 패치 하위 1% (item_patch 가드)
    ocr_local_recall: float | None    # 로컬 OCR 글자 recall (ocr_local 가드, 설정으로 켤 때만)
    checks: list
    gate_passed: bool | None
    prompt_used: str
    objects: list              # analyze 의 사진 속 물건 [{what, box, for_sale, role}] — 세트 구성품 (10-09 State 에 추가:
                               # 없으면 LangGraph 가 버려서 accessory 처리 · 세트 판단이 늘 빈 목록이었다)
    set_views: int             # 세트 생성에 실제로 넣은 참고 사진 수 — 개수 게이트가 같은 범위만 센다 (10-09)
    count_mismatch: list       # 세트 개수 게이트에서 다른 종류 [{what, want, got}] (10-09)
    gen_prompts: list          # 시도마다 실제 보낸 생성 프롬프트 — 배경 교체로 끝나면 prompt_used 가 덮여서 (10-09)
    rep_pieces: list           # 대표사진 조각 [(종류, 1)] — 본체 하나 (apply_selection, 10-11)
    detail_pieces: list        # 대표사진에서 뺀 구성품 [{what, count}] — 상세 · 인증용 목록 (10-10)
    user_mode: str | None      # 판매자가 고른 기능 — "generate" | "composite" | None(분석 판단대로) (10-11)
    fixed_pieces: list         # 정답에 미리 적어 둔 구성품 [(종류, 개수)] — 있으면 분석 objects 대신 (eval/data/posts, 10-10)
    extra_views_explicit: bool  # extra_view_ids 를 판매자가 화면에서 보냈다 (10-09) — multi_view 설정과 무관하게 쓴다
    extra_view_ids: list       # multi_view: 같은 물건의 다른 각도 원본 file_id (최대 2)
    ref_category: str | None   # style_ref: 참고 고르기용 종류 · 주 사진 각도 (묶음 화면에서 안다, 10-05)
    ref_view: str | None
    ref_file: str | None       # 게시글 단위로 미리 고른 정답 (style_refs.plan)
    style_ref: str | None      # 고른 참고 선 그림 파일 (없으면 None)
    ref_layout: str | None     # 정답 구도를 문장으로 (style_refs.plan_text, 10-08 실험)
    ref_sketch: bool           # 문장과 함께 정답 선 그림도 넣는다 (10-08 실험)
    clean_prompt: bool         # 정리한 프롬프트(CLEAN_PROMPT)로 통째로 (10-08 실험)
    ref_keep: str | None       # CLEAN_PROMPT 의 물건별 "지킬 것" 예시
    fixed_inputs: bool         # 사람이 정해 준 인풋 (eval --inputs, 10-12) — 다른 각도 사진을 버리지 않는다
    job_id: str | None         # 작업으로 돌 때 (progress.py, 10-12) — 노드가 진행 상황 · 미리보기를 알린다
    arrange: str               # 판매자 요청 한 줄 (clean_arrange 통과, 10-12) — 놓는 방법 · 구도만


ANALYZE_ATTEMPTS = 2
DETECT_RETRY_DELAY_S = 1.0   # 429/일시 장애가 바로 또 나지 않게 잠깐 쉰다 (verify 도 같이 씀)
VERIFY_ATTEMPTS = 2


def _with_retry(fn, label: str, attempts: int):
    """VLM 호출 재시도 — 다시 해 볼 만한 실패(retryable)만, 사이에 잠깐 쉰다. 끝내 실패하면 마지막 예외를 올린다."""
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as e:
            logger.warning(f"[{label}] 실패 ({attempt}/{attempts}): {e}")
            if not retryable(e) or attempt == attempts:
                raise
            time.sleep(DETECT_RETRY_DELAY_S)


def _result_name(s: State) -> str:
    return f"{s['file_id']}_{s['preset_key']}.jpg"


_POOL = ThreadPoolExecutor(max_workers=32, thread_name_prefix="pipeline")
# 로컬 OCR(EasyOCR)은 워커 1개 — 누끼 자리를 뺏지 않고, Reader 를 여러 스레드가 동시에 쓰지 않게
# (EasyOCR 은 스레드 안전을 보장하지 않는다).
_OCR_POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ocr")
# 대기 상한은 작업을 넘긴 시점부터 — check_photo 를 기다리는 동안에도 흐른다 (직렬로 더해지지 않게).
LOCAL_OCR_WAIT_S = 60   # ocr_local — 첫 EasyOCR 로드(모델 다운로드) 포함 여유


def _in_background(fn, *args, pool=None):
    """fn 을 다른 스레드에서 시작하고 Future 를 돌려준다. 현재 컨텍스트(Langfuse/OTEL
    트레이스)를 복사해 넘긴다 — 안 그러면 그 스레드의 VLM 호출이 이번 변환 트레이스에서
    떨어져 별도 트레이스로 찍힌다."""
    ctx = contextvars.copy_context()
    return (pool or _POOL).submit(ctx.run, fn, *args)
