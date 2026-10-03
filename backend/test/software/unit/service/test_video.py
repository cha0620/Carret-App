"""app.services.video — 동영상에서 선명하고 서로 다른 장면 고르기.

동영상은 cv2.VideoWriter(mp4v)로 테스트 안에서 만든다. 결과 장면은 PNG 바이트. 32x32 흑백으로 줄여 비교하므로
무작위 노이즈 대신 밝기·도형이 서로 다른 장면을 쓴다.
"""
import os

import cv2
import numpy as np
import pytest

from app.services import video
from app.services.video import (
    VideoError, _read_candidates, _shrink, _spread, extract_frames, looks_like_video, pick,
)


# ── 장면 만들기 ─────────────────────────────
def _scene(i: int, size=(96, 128)) -> np.ndarray:
    """i 번째 장면 — 배경 밝기와 도형 위치가 모두 달라 32x32 로 줄여도 차이가 크다."""
    h, w = size
    bg = 20 + 45 * (i % 5)
    f = np.full((h, w, 3), bg, np.uint8)
    x = 8 + (i * 23) % (w - 40)
    y = 8 + (i * 17) % (h - 40)
    color = (255, 255, 255) if bg < 128 else (0, 0, 0)
    if i % 2 == 0:
        cv2.rectangle(f, (x, y), (x + 30, y + 30), color, -1)
    else:
        cv2.circle(f, (x + 15, y + 15), 15, color, -1)
    # 가는 선을 그어 선명도(라플라시안 분산)를 장면마다 비슷하게 높인다
    for k in range(0, w, 8):
        cv2.line(f, (k, 0), (k, 6), color, 1)
    return f


def _blur(f: np.ndarray) -> np.ndarray:
    return cv2.GaussianBlur(f, (31, 31), 15)


def _write_video(path, frames, fps=10.0) -> bytes:
    h, w = frames[0].shape[:2]
    vw = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
    assert vw.isOpened(), "mp4v VideoWriter 를 열 수 없음 (OpenCV 빌드 확인)"
    for f in frames:
        vw.write(f)
    vw.release()
    return path.read_bytes()


def _decode(jpeg: bytes) -> np.ndarray:
    return cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)


# ── _spread ─────────────────────────────────
def test_spread_returns_same_list_when_short_enough():
    items = [1, 2, 3]
    assert _spread(items, 3) is items
    assert _spread(items, 10) is items


def test_spread_empty_list():
    assert _spread([], 5) == []


def test_spread_keeps_first_and_last_and_order():
    out = _spread(list(range(10)), 4)
    assert len(out) == 4
    assert out[0] == 0 and out[-1] == 9
    assert out == sorted(out)


def test_spread_k_one_takes_first():
    assert _spread(list(range(7)), 1) == [0]


@pytest.mark.parametrize("n,k", [(5, 4), (9, 8), (41, 40), (100, 7)])
def test_spread_returns_exactly_k_distinct(n, k):
    out = _spread(list(range(n)), k)
    assert len(out) == k and len(set(out)) == k


# ── pick ────────────────────────────────────
def test_pick_identical_frames_keep_one():
    f = _scene(0)
    out = pick([f.copy() for _ in range(6)], 8)
    assert len(out) == 1


def test_pick_distinct_scenes_all_kept_in_order():
    frames = [_scene(i) for i in range(5)]
    out = pick(frames, 8)
    assert len(out) == 5
    assert all(o is f for o, f in zip(out, frames))


def test_pick_drops_blurry_frame():
    sharp = [_scene(i) for i in range(4)]
    blurred = _blur(_scene(9))
    frames = sharp[:2] + [blurred] + sharp[2:]
    out = pick(frames, 8)
    assert not any(o is blurred for o in out)
    assert len(out) == 4


def test_pick_compares_with_last_kept_only():
    """A, B, A — 바로 앞에 고른 장면(B)과 다르니 A 가 다시 남는다 (시간 순서 비교)."""
    a, b = _scene(0), _scene(1)
    out = pick([a, a.copy(), b, a.copy()], 8)
    assert len(out) == 3


def test_pick_limits_to_max_frames_spread_over_time():
    frames = [_scene(i) for i in range(10)]
    out = pick(frames, 3)
    assert len(out) == 3
    assert out[0] is frames[0] and out[-1] is frames[-1]


def test_pick_all_flat_frames_does_not_crash():
    """선명도가 전부 0 (단색) 이어도 바닥값 0 — 버리지 않고 밝기로만 장면을 나눈다."""
    frames = [np.full((40, 40, 3), v, np.uint8) for v in (0, 0, 100, 100, 200)]
    out = pick(frames, 8)
    assert [int(f[0, 0, 0]) for f in out] == [0, 100, 200]


def test_pick_small_difference_counts_as_same_scene():
    base = np.full((40, 40, 3), 100, np.uint8)
    near = np.full((40, 40, 3), 103, np.uint8)      # 평균 차이 3 < 6
    far = np.full((40, 40, 3), 110, np.uint8)       # 평균 차이 10 ≥ 6
    out = pick([base, near, far], 8)
    assert len(out) == 2 and out[1] is far


def test_pick_empty_list():
    assert pick([], 8) == []


# ── looks_like_video ────────────────────────
@pytest.mark.parametrize("data,ok", [
    (b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 8, True),     # mp4 · m4v
    (b"\x00\x00\x00\x14ftypqt  ", True),                    # mov
    (b"\x1a\x45\xdf\xa3" + b"\x00" * 12, True),              # webm · mkv (EBML)
    (b"", False),
    (b"ftyp", False),                                       # 위치가 틀림
    (b"\x89PNG\r\n\x1a\n" + b"\x00" * 8, False),
    (b"\xff\xd8\xff\xe0" + b"\x00" * 12, False),
    (b"RIFF\x00\x00\x00\x00AVI ", False),
])
def test_looks_like_video(data, ok):
    assert looks_like_video(data) is ok


# ── _shrink ─────────────────────────────────
def test_shrink_large_frame_to_max_side():
    out = _shrink(np.zeros((1000, 4000, 3), np.uint8))
    assert out.shape == (400, 1600, 3)


def test_shrink_small_frame_unchanged():
    f = np.zeros((100, 200, 3), np.uint8)
    assert _shrink(f) is f


def test_shrink_exactly_max_side_unchanged():
    f = np.zeros((10, video.MAX_SIDE, 3), np.uint8)
    assert _shrink(f) is f


def test_shrink_gray_frame_becomes_bgr():
    out = _shrink(np.full((20, 30), 77, np.uint8))
    assert out.shape == (20, 30, 3) and int(out[0, 0, 1]) == 77


# ── _read_candidates (가짜 cap) ──────────────
class _FakeCap:
    """grab/retrieve 로 차례로 읽는 cap 흉내. fail 에 든 위치는 retrieve 실패,
    msec_per_frame 으로 재생 시각(POS_MSEC)을 흉내 낸다."""
    def __init__(self, n, fail=(), msec_per_frame=40.0):
        self.n = n
        self.pos = -1
        self.fail = set(fail)
        self.msec = msec_per_frame
        self.grabs = 0
        self.retrieved = []

    def grab(self):
        if self.pos + 1 >= self.n:
            return False
        self.pos += 1
        self.grabs += 1
        return True

    def retrieve(self):
        if self.pos in self.fail:
            return False, None
        self.retrieved.append(self.pos)
        f = np.full((4, 4, 3), self.pos % 256, np.uint8)
        f[..., 1] = self.pos // 256                     # 256 넘는 위치도 구분되게 (_vals 가 합친다)
        return True, f

    def get(self, prop):
        assert prop == cv2.CAP_PROP_POS_MSEC
        return self.pos * self.msec

    def set(self, *a):
        raise AssertionError("위치 탐색(set)은 쓰지 않는다")


def _vals(frames):
    return [int(f[0, 0, 0]) + 256 * int(f[0, 0, 1]) for f in frames]


def test_read_candidates_known_count_samples_at_most_sample_max():
    cap = _FakeCap(200)
    out = _read_candidates(cap, 200)
    assert len(out) == video.SAMPLE_MAX
    assert cap.grabs == 200                          # 차례로 다 grab
    assert len(cap.retrieved) == video.SAMPLE_MAX     # 꺼내는 건 고른 위치만
    assert cap.retrieved[0] == 0 and cap.retrieved[-1] == 199


def test_read_candidates_known_count_small_video_reads_every_frame():
    cap = _FakeCap(5)
    assert _vals(_read_candidates(cap, 5)) == [0, 1, 2, 3, 4]


def test_read_candidates_skips_failed_retrieves():
    cap = _FakeCap(5, fail={1, 3})
    assert _vals(_read_candidates(cap, 5)) == [0, 2, 4]


def test_read_candidates_metadata_count_too_big_stops_at_end():
    """메타데이터가 프레임 수를 부풀려도 grab 이 끝나면 멈춘다."""
    cap = _FakeCap(4)
    out = _read_candidates(cap, 1000)
    assert cap.grabs == 4
    assert all(v < 4 for v in _vals(out))


def test_read_candidates_does_not_read_past_known_count():
    cap = _FakeCap(50)
    _read_candidates(cap, 10)
    assert cap.grabs == 10


def test_read_candidates_unknown_count_reads_sequentially():
    """프레임 수 0 (일부 webm) — 처음부터 전부 꺼낸다."""
    cap = _FakeCap(10)
    assert _vals(_read_candidates(cap, 0)) == list(range(10))


def test_read_candidates_unknown_count_long_video_bounded():
    """아주 긴 순차 읽기도 솎아서 결국 SAMPLE_MAX 장 — 앞부분이나 뒷부분에 몰리지 않고 고르게."""
    cap = _FakeCap(2000, msec_per_frame=1.0)
    out = _read_candidates(cap, 0)
    vals = _vals(out)
    assert len(out) == video.SAMPLE_MAX
    assert vals[0] == 0 and max(vals) > 1500
    gaps = [b - a for a, b in zip(vals, vals[1:])]
    assert max(gaps) <= 2 * min(gaps)              # 간격이 고르다 (반으로 솎을 때 간격도 두 배로)


def test_read_candidates_unknown_count_stops_at_max_read(monkeypatch):
    monkeypatch.setattr(video, "MAX_READ", 30)
    cap = _FakeCap(10_000, msec_per_frame=0.0)
    _read_candidates(cap, 0)
    assert cap.grabs == 30


def test_read_candidates_unknown_count_empty():
    assert _read_candidates(_FakeCap(0), 0) == []


def test_read_candidates_real_duration_too_long_raises():
    """메타데이터(프레임 수·fps)를 믿지 않고 재생 시각으로 다시 본다."""
    cap = _FakeCap(100, msec_per_frame=(video.MAX_SECONDS * 1000) / 50)
    with pytest.raises(VideoError, match="너무 길어요"):
        _read_candidates(cap, 0)


def test_read_candidates_duration_exactly_max_is_ok():
    cap = _FakeCap(11, msec_per_frame=(video.MAX_SECONDS * 1000) / 10)   # 마지막 프레임 = 정확히 상한
    assert len(_read_candidates(cap, 11)) == 11


def test_read_candidates_deadline(monkeypatch):
    monkeypatch.setattr(video, "DEADLINE_S", -1.0)
    with pytest.raises(VideoError, match="오래 걸려요"):
        _read_candidates(_FakeCap(5), 5)


def test_read_candidates_shrinks_big_frames():
    class Big(_FakeCap):
        def retrieve(self):
            return True, np.zeros((3200, 2000, 3), np.uint8)
    out = _read_candidates(Big(2), 2)
    assert all(max(f.shape[:2]) == video.MAX_SIDE for f in out)


# ── extract_frames (실제 mp4) ────────────────
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
FAKE_MP4 = b"\x00\x00\x00\x18ftypmp42" + b"junk" * 100     # 머리만 mp4, 내용은 엉터리


def _decode(png: bytes) -> np.ndarray:
    return cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)


def test_extract_frames_returns_distinct_scenes_as_png(tmp_path):
    frames = [_scene(i) for i in range(4) for _ in range(10)]
    data = _write_video(tmp_path / "v.mp4", frames)
    out = extract_frames(data, ".mp4", max_frames=8)
    assert len(out) == 4
    assert all(b[:8] == PNG_MAGIC for b in out)
    # 시간 순서 유지 — 장면 i 의 배경 밝기 20+45i 가 커지는 순서
    bgs = [int(_decode(b)[93, 125].mean()) for b in out]
    assert bgs == sorted(bgs)
    assert _decode(out[0]).shape == (96, 128, 3)


def test_extract_frames_respects_max_frames(tmp_path):
    frames = [_scene(i) for i in range(8) for _ in range(4)]
    data = _write_video(tmp_path / "v.mp4", frames)
    assert len(extract_frames(data, ".mp4", max_frames=2)) == 2


def test_extract_frames_static_video_gives_one(tmp_path):
    data = _write_video(tmp_path / "v.mp4", [_scene(3)] * 20)
    assert len(extract_frames(data, ".mp4")) == 1


def test_extract_frames_large_video_frames_shrunk(tmp_path, monkeypatch):
    monkeypatch.setattr(video, "MAX_SIDE", 64)
    data = _write_video(tmp_path / "v.mp4", [_scene(i) for i in range(3)])
    out = extract_frames(data, ".mp4")
    assert all(max(_decode(b).shape[:2]) == 64 for b in out)


@pytest.mark.parametrize("data", [b"", b"this is not a video" * 50, PNG_MAGIC + b"\x00" * 40])
def test_extract_frames_without_video_magic_raises_before_temp_file(monkeypatch, data):
    def no_temp(*a, **kw):
        raise AssertionError("매직 바이트가 틀리면 임시 파일을 만들지 않는다")
    monkeypatch.setattr(video.tempfile, "mkstemp", no_temp)
    with pytest.raises(VideoError, match="읽을 수 없어요"):
        extract_frames(data, ".mp4")


def test_extract_frames_magic_ok_but_garbage_raises_video_error():
    with pytest.raises(VideoError):
        extract_frames(FAKE_MP4, ".mp4")


def test_extract_frames_too_long_by_metadata_raises(tmp_path, monkeypatch):
    data = _write_video(tmp_path / "v.mp4", [_scene(i % 3) for i in range(30)], fps=10)  # 3초
    monkeypatch.setattr(video, "MAX_SECONDS", 2)
    with pytest.raises(VideoError, match="너무 길어요"):
        extract_frames(data, ".mp4")


def test_extract_frames_exactly_max_seconds_is_ok(tmp_path, monkeypatch):
    data = _write_video(tmp_path / "v.mp4", [_scene(i % 3) for i in range(20)], fps=10)  # 2초
    monkeypatch.setattr(video, "MAX_SECONDS", 2)
    assert extract_frames(data, ".mp4")


def test_extract_frames_resolution_limit(tmp_path, monkeypatch):
    data = _write_video(tmp_path / "v.mp4", [_scene(i) for i in range(3)])     # 128x96
    monkeypatch.setattr(video, "MAX_PIXELS", 128 * 96 - 1)
    with pytest.raises(VideoError, match="해상도"):
        extract_frames(data, ".mp4")


def test_extract_frames_cv2_error_becomes_video_error(tmp_path, monkeypatch):
    data = _write_video(tmp_path / "v.mp4", [_scene(i) for i in range(3)])

    def boom(cap, total):
        raise cv2.error("디코더 오류")
    monkeypatch.setattr(video, "_read_candidates", boom)
    with pytest.raises(VideoError, match="문제가 생겼어요"):
        extract_frames(data, ".mp4")


def test_extract_frames_opens_with_ffmpeg_backend(tmp_path, monkeypatch):
    data = _write_video(tmp_path / "v.mp4", [_scene(i) for i in range(3)])
    seen = []
    real = cv2.VideoCapture

    def spy(*a):
        seen.append(a)
        return real(*a)
    monkeypatch.setattr(video.cv2, "VideoCapture", spy)
    extract_frames(data, ".mp4")
    assert seen and seen[0][1] == cv2.CAP_FFMPEG


def test_extract_frames_webm_with_ebml_header(tmp_path):
    """.webm 실제 파일 — EBML 머리라 매직 검사를 통과한다 (VP8 인코더가 없는 빌드면 건너뜀)."""
    path = tmp_path / "v.webm"
    frames = [_scene(i) for i in range(3) for _ in range(5)]
    vw = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"VP80"), 10.0, (128, 96))
    if not vw.isOpened():
        pytest.skip("이 OpenCV 빌드는 webm(VP8) 쓰기를 못 한다")
    for f in frames:
        vw.write(f)
    vw.release()
    data = path.read_bytes()
    assert data[:4] == b"\x1a\x45\xdf\xa3"
    out = extract_frames(data, ".webm")
    assert 1 <= len(out) <= 3 and all(b[:8] == PNG_MAGIC for b in out)


def test_video_error_is_value_error():
    assert issubclass(VideoError, ValueError)


@pytest.mark.parametrize("ok", [True, False])
def test_extract_frames_removes_temp_file(tmp_path, monkeypatch, ok):
    """성공이든 실패든 임시 파일을 남기지 않는다."""
    import tempfile
    made = []
    real = tempfile.mkstemp

    def tracking(*a, **kw):
        fd, path = real(*a, dir=tmp_path, **kw)
        made.append(path)
        return fd, path
    monkeypatch.setattr(video.tempfile, "mkstemp", tracking)
    data = _write_video(tmp_path / "v.mp4", [_scene(i) for i in range(3)]) if ok else FAKE_MP4
    if ok:
        extract_frames(data, ".mp4")
    else:
        with pytest.raises(VideoError):
            extract_frames(data, ".mp4")
    assert made and not any(os.path.exists(p) for p in made)


def test_extract_frames_uses_given_suffix(monkeypatch):
    import tempfile
    seen = []
    real = tempfile.mkstemp

    def tracking(*a, **kw):
        seen.append(kw.get("suffix"))
        return real(*a, **kw)
    monkeypatch.setattr(video.tempfile, "mkstemp", tracking)
    with pytest.raises(VideoError):
        extract_frames(FAKE_MP4, ".mov")
    assert seen == [".mov"]
