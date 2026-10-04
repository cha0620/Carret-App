"""eval/fetch.py main — 네트워크 없이 (urllib monkeypatch)."""
import json


class _Resp:
    def __init__(self, data, ctype="image/webp"):
        self._d = data
        self.headers = {"Content-Type": ctype}

    def read(self):
        return self._d


def _setup(fetch_mod, tmp_path, monkeypatch, entries):
    monkeypatch.setattr(fetch_mod, "HERE", tmp_path)
    monkeypatch.setattr(fetch_mod, "IMAGES", tmp_path / "data" / "images")
    (tmp_path / "data" / "dataset.json").write_text(json.dumps(entries), encoding="utf-8")
    calls = []

    def fake_urlopen(req, timeout=None):
        calls.append((req.full_url, req.get_header("User-agent"), timeout))
        if "fail" in req.full_url:
            raise OSError("boom")
        if "post" in req.full_url:                        # 게시글 주소 — 사진이 아니다
            return _Resp(b"<html>", "text/html; charset=utf-8")
        return _Resp(b"IMG:" + req.full_url.encode())
    monkeypatch.setattr(fetch_mod.urllib.request, "urlopen", fake_urlopen)
    return calls


def test_fetch_skips_existing_and_urlless(fetch_mod, tmp_path, monkeypatch, capsys):
    calls = _setup(fetch_mod, tmp_path, monkeypatch, [
        {"file": "have.webp", "url": "http://x/have"},
        {"file": "local.webp", "url": ""},
        {"file": "nourl.webp"},
        {"file": "new.webp", "url": "http://x/new"},
    ])
    (tmp_path / "data" / "images").mkdir(parents=True)
    (tmp_path / "data" / "images" / "have.webp").write_bytes(b"old")
    assert fetch_mod.main() == 1                         # url 없는 두 개가 없음으로 남는다
    assert [c[0] for c in calls] == ["http://x/new"]
    assert calls[0][1] == "Mozilla/5.0" and calls[0][2] == 20
    assert (tmp_path / "data" / "images" / "have.webp").read_bytes() == b"old"
    assert (tmp_path / "data" / "images" / "new.webp").read_bytes() == b"IMG:http://x/new"
    assert not (tmp_path / "data" / "images" / "local.webp").exists()
    out = capsys.readouterr().out
    assert "[없음] local.webp" in out and "[없음] nourl.webp" in out and "[ok]   new.webp" in out


def test_fetch_all_present_returns_0_without_network(fetch_mod, tmp_path, monkeypatch):
    calls = _setup(fetch_mod, tmp_path, monkeypatch, [{"file": "a.webp", "url": ""}, {"file": "b.webp", "url": "http://x/b"}])
    (tmp_path / "data" / "images").mkdir(parents=True)
    for n in ("a.webp", "b.webp"):
        (tmp_path / "data" / "images" / n).write_bytes(b"1")
    assert fetch_mod.main() == 0
    assert calls == []


def test_fetch_creates_images_dir_and_reports_failure(fetch_mod, tmp_path, monkeypatch, capsys):
    calls = _setup(fetch_mod, tmp_path, monkeypatch, [{"file": "a.webp", "url": "http://x/fail"},
                                                     {"file": "b.webp", "url": "http://x/b"}])
    assert fetch_mod.main() == 1
    assert len(calls) == 2
    assert not (tmp_path / "data" / "images" / "a.webp").exists()  # 실패 시 빈 파일을 남기지 않는다
    assert (tmp_path / "data" / "images" / "b.webp").exists()
    assert "[실패] a.webp: boom" in capsys.readouterr().out


def test_fetch_does_not_save_html_from_post_url(fetch_mod, tmp_path, monkeypatch, capsys):
    """COLLECT.md 는 url 에 게시글 주소를 적는다 — 그 HTML 을 사진 이름으로 저장하면 안 된다."""
    _setup(fetch_mod, tmp_path, monkeypatch, [{"file": "a.jpg", "url": "https://cafe/post/1"}])
    assert fetch_mod.main() == 1
    assert not (tmp_path / "data" / "images" / "a.jpg").exists()
    assert "사진이 아니다" in capsys.readouterr().out


# ── 게시글 주소 (10-04) — 게시글의 사진 여러 장이 같은 주소 ──
def test_fetch_post_url_requested_once_for_whole_post(fetch_mod, tmp_path, monkeypatch, capsys):
    calls = _setup(fetch_mod, tmp_path, monkeypatch, [
        {"file": "a_p01.jpg", "url": "https://cafe/post/1"},
        {"file": "a_p02.jpg", "url": "https://cafe/post/1"},
        {"file": "a_p03.jpg", "url": "https://cafe/post/1"},
        {"file": "b_p01.jpg", "url": "https://cafe/post/2"},              # 다른 게시글은 따로 확인
        {"file": "img.jpg", "url": "http://x/img"}])
    assert fetch_mod.main() == 1
    assert [c[0] for c in calls] == ["https://cafe/post/1", "https://cafe/post/2", "http://x/img"]
    out = capsys.readouterr().out
    assert "[없음] a_p01.jpg — url 이 사진이 아니다(text/html; charset=utf-8)" in out
    assert "[없음] a_p02.jpg — 게시글 주소, images/ 에 직접 넣어야 한다" in out
    assert "[없음] a_p03.jpg — 게시글 주소, images/ 에 직접 넣어야 한다" in out
    assert "[없음] b_p01.jpg — url 이 사진이 아니다" in out
    assert "[ok]   img.jpg" in out
    images = tmp_path / "data" / "images"
    assert sorted(p.name for p in images.iterdir()) == ["img.jpg"]


def test_fetch_post_url_skipped_photo_already_present_not_requested(fetch_mod, tmp_path, monkeypatch):
    """첫 사진이 이미 있으면 그 주소는 확인하지 않는다 — 다음 사진에서 한 번 요청하고, 그 뒤로는 안 한다."""
    calls = _setup(fetch_mod, tmp_path, monkeypatch, [
        {"file": "a_p01.jpg", "url": "https://cafe/post/1"},
        {"file": "a_p02.jpg", "url": "https://cafe/post/1"},
        {"file": "a_p03.jpg", "url": "https://cafe/post/1"}])
    (tmp_path / "data" / "images").mkdir(parents=True)
    (tmp_path / "data" / "images" / "a_p01.jpg").write_bytes(b"have")
    assert fetch_mod.main() == 1
    assert len(calls) == 1


def test_fetch_failed_url_is_retried_per_photo(fetch_mod, tmp_path, monkeypatch, capsys):
    """네트워크 실패는 '사진이 아닌 주소'로 확인된 게 아니다 — 같은 주소라도 다시 요청한다."""
    calls = _setup(fetch_mod, tmp_path, monkeypatch, [
        {"file": "a_p01.jpg", "url": "http://x/fail"}, {"file": "a_p02.jpg", "url": "http://x/fail"}])
    assert fetch_mod.main() == 1
    assert len(calls) == 2
    out = capsys.readouterr().out
    assert "[실패] a_p01.jpg: boom" in out and "[실패] a_p02.jpg: boom" in out


def test_fetch_same_image_url_fetched_for_each_file(fetch_mod, tmp_path, monkeypatch):
    calls = _setup(fetch_mod, tmp_path, monkeypatch, [
        {"file": "a.jpg", "url": "http://x/img"}, {"file": "b.jpg", "url": "http://x/img"}])
    assert fetch_mod.main() == 0
    assert len(calls) == 2
    images = tmp_path / "data" / "images"
    assert (images / "a.jpg").read_bytes() == (images / "b.jpg").read_bytes() == b"IMG:http://x/img"
