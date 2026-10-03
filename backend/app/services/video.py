"""동영상 → 사진 몇 장. 판매자가 물건을 한 바퀴 돌려 찍은 영상에서 선명하고 서로 다른 장면만 고른다.

고르는 법 (VLM 없이 — 싸고 빠르게):
  1. 처음부터 차례로 읽으며(grab) 고른 간격의 후보만 꺼낸다(retrieve) — 최대 SAMPLE_MAX 개, 꺼내자마자 줄인다
  2. 흐린 프레임을 버린다 — 라플라시안 분산(선명도)이 후보 중앙값의 BLUR_RATIO 배보다 낮으면
  3. 시간 순서로 보며 마지막으로 고른 장면과 거의 같은 프레임을 버린다 (작게 줄인 흑백의 평균 차이)
  4. 남은 것 중 최대 max_frames 장 — 너무 많으면 영상 전체에 고르게 퍼지도록 솎는다

믿을 수 없는 입력이라 (10-01 리뷰): 매직 바이트 확인 · FFmpeg 은 로컬 파일만 · 해상도 상한 ·
읽는 프레임 수와 시간 상한 · 후보는 꺼내자마자 MAX_SIDE 로 줄여 메모리를 묶는다.
위치 탐색(set POS_FRAMES)은 키프레임이 드문 영상에서 매번 처음부터 다시 풀어 느려서 쓰지 않는다.
"""
import os
import tempfile
import time

# FFmpeg 이 플레이리스트(HLS·concat) 안의 다른 파일·주소를 따라 열지 않게 — cv2 import 전에
os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "protocol_whitelist;file")

import cv2  # noqa: E402
import numpy as np  # noqa: E402

SAMPLE_MAX = 40          # 후보 프레임 상한
BLUR_RATIO = 0.35        # 중앙값 대비 이보다 흐리면 버림
SAME_SCENE_DIFF = 6.0    # 32x32 흑백 평균 절대 차이(0~255) 가 이보다 작으면 같은 장면 (마지막으로 고른 장면과 비교)
MAX_SECONDS = 90
MAX_SIDE = 1600          # 후보는 꺼내자마자 이 크기로 (storage 의 image_max_side 와 같게)
MAX_PIXELS = 8192 * 8192
MAX_READ = MAX_SECONDS * 120     # 프레임 수를 모를 때 읽을 상한 (120fps × 90초)
DEADLINE_S = 30.0


class VideoError(ValueError):
    """읽을 수 없거나 너무 긴 영상 — 사용자에게 그대로 보여줄 메시지."""


def looks_like_video(data: bytes) -> bool:
    """mp4 · mov · m4v (ISO BMFF: 4~8 바이트가 ftyp) 또는 webm · mkv (EBML 머리)."""
    return data[4:8] == b"ftyp" or data[:4] == b"\x1a\x45\xdf\xa3"


def _sharpness(frame: np.ndarray) -> float:
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def _thumb(frame: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    return cv2.resize(gray, (32, 32), interpolation=cv2.INTER_AREA).astype(np.float32)


def _shrink(frame: np.ndarray) -> np.ndarray:
    if frame.ndim == 2:
        frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
    h, w = frame.shape[:2]
    s = MAX_SIDE / max(h, w)
    if s < 1:
        frame = cv2.resize(frame, (round(w * s), round(h * s)), interpolation=cv2.INTER_AREA)
    return frame


def _spread(items: list, k: int) -> list:
    """시간 순서 목록에서 고르게 k 개."""
    if len(items) <= k:
        return items
    idx = np.linspace(0, len(items) - 1, k).round().astype(int)
    return [items[i] for i in sorted(set(idx.tolist()))]


def extract_frames(data: bytes, suffix: str = ".mp4", max_frames: int = 8) -> list[bytes]:
    """영상 바이트 → PNG 바이트 목록 (시간 순서, 무손실 — 저장할 때 한 번만 JPEG 로). 실패하면 VideoError."""
    if not looks_like_video(data):
        raise VideoError("동영상을 읽을 수 없어요 — mp4 · mov · webm 으로 올려 주세요")
    fd, path = tempfile.mkstemp(suffix=suffix)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        cap = cv2.VideoCapture(path, cv2.CAP_FFMPEG)
        try:
            if not cap.isOpened():
                raise VideoError("동영상을 읽을 수 없어요 — mp4 · mov · webm 으로 올려 주세요")
            w, h = cap.get(cv2.CAP_PROP_FRAME_WIDTH), cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
            if w * h > MAX_PIXELS:
                raise VideoError("동영상 해상도가 너무 커요")
            total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            fps = float(cap.get(cv2.CAP_PROP_FPS) or 0)
            if fps > 0 and total / fps > MAX_SECONDS:
                raise VideoError(f"동영상이 너무 길어요 — {MAX_SECONDS}초 안으로 찍어 주세요")
            frames = _read_candidates(cap, total)
            picked = pick(frames, max_frames)
            out = [_png(f) for f in picked]
        except cv2.error:
            raise VideoError("동영상을 읽는 중 문제가 생겼어요 — 다른 형식으로 올려 주세요")
        finally:
            cap.release()
    finally:
        os.unlink(path)
    if not out:
        raise VideoError("동영상에서 장면을 꺼내지 못했어요")
    return out


def _read_candidates(cap, total: int) -> list[np.ndarray]:
    """처음부터 차례로 grab 하고, 고른 위치만 retrieve. 프레임 수를 모르면(일부 webm) 읽으며 솎는다.
    실제 재생 시각(POS_MSEC)으로 길이를 다시 본다 — 메타데이터의 프레임 수는 믿을 수 없다."""
    start = time.monotonic()
    want = set(np.linspace(0, total - 1, min(total, SAMPLE_MAX)).round().astype(int).tolist()) if total > 0 else None
    out, i, stride = [], 0, 1          # stride: 프레임 수를 모를 때 몇 장에 하나씩 꺼내나
    while i < (total if total > 0 else MAX_READ):
        if time.monotonic() - start > DEADLINE_S:
            raise VideoError("동영상을 읽는 데 너무 오래 걸려요 — 더 짧게 찍어 주세요")
        if not cap.grab():
            break
        if cap.get(cv2.CAP_PROP_POS_MSEC) > MAX_SECONDS * 1000:
            raise VideoError(f"동영상이 너무 길어요 — {MAX_SECONDS}초 안으로 찍어 주세요")
        if (i in want) if want is not None else (i % stride == 0):
            ok, frame = cap.retrieve()
            if ok and frame is not None:
                out.append(_shrink(frame))
                if want is None and len(out) > SAMPLE_MAX * 2:   # 모를 때 — 넘으면 반으로 솎고 간격을 두 배로
                    out, stride = out[::2], stride * 2           # (간격을 안 늘리면 뒤쪽 장면만 쌓인다)
        i += 1
    return _spread(out, SAMPLE_MAX)


def pick(frames: list[np.ndarray], max_frames: int) -> list[np.ndarray]:
    """흐린 것 · 같은 장면을 빼고 고르게 max_frames 장 (시간 순서 유지)."""
    if not frames:
        return []
    scores = [_sharpness(f) for f in frames]
    floor = float(np.median(scores)) * BLUR_RATIO
    sharp = [f for f, s in zip(frames, scores) if s >= floor]
    kept, last = [], None
    for f in sharp:
        t = _thumb(f)
        if last is not None and float(np.abs(t - last).mean()) < SAME_SCENE_DIFF:
            continue
        kept.append(f)
        last = t
    return _spread(kept, max_frames)


def _png(frame: np.ndarray) -> bytes:
    ok, buf = cv2.imencode(".png", frame)
    if not ok:
        raise VideoError("장면을 이미지로 바꾸지 못했어요")
    return buf.tobytes()
