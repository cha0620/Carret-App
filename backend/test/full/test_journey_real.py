from pathlib import Path

import pytest

pytestmark = pytest.mark.eval
FIX = Path(__file__).parents[1] / "fixtures" / "stain.png"


def test_journey_real(client, tmp_storage):
    fid = client.post("/api/images/upload",
                      files={"file": ("s.png", FIX.read_bytes(), "image/png")}).json()["file_id"]
    body = client.post("/api/transform",
                       json={"file_id": fid, "preset": "studio_white"}).json()
    assert body["bubbles"], "진짜 모델이 하자를 1개 이상 봐야 함"