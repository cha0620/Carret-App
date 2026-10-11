"""칠한 곳 고치기 — 마스크 변환 · 칠한 곳만 붙이기 · 방식별 프롬프트 (fal 호출 없음)."""
import io

from PIL import Image, ImageDraw

from app.services.ai import inpainter


def _jpg(color, size=(64, 64)):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "JPEG", quality=95)
    return buf.getvalue()


def _mask_png(size=(32, 32), box=(0, 0, 16, 32)):
    """왼쪽 절반만 불투명(칠함)인 투명 PNG."""
    m = Image.new("RGBA", size, (0, 0, 0, 0))
    ImageDraw.Draw(m).rectangle((box[0], box[1], box[2] - 1, box[3] - 1), fill=(255, 0, 0, 255))
    buf = io.BytesIO()
    m.save(buf, "PNG")
    return buf.getvalue()


def test_mask_image_alpha_to_white_and_resized():
    m = inpainter.mask_image(_mask_png(), (64, 64))
    assert m.size == (64, 64) and m.mode == "L"
    assert m.getpixel((5, 30)) == 255 and m.getpixel((60, 30)) == 0


def test_paste_back_only_masked_area_changes():
    base, edited = _jpg((200, 30, 30)), _jpg((30, 30, 200), size=(128, 128))  # 출력 크기 달라도 맞춘다
    mask = inpainter.mask_image(_mask_png(), (64, 64))
    out = Image.open(io.BytesIO(inpainter.paste_back(base, edited, mask))).convert("RGB")
    ref = Image.open(io.BytesIO(base)).convert("RGB")
    assert out.size == (64, 64)
    edge = 32 + inpainter.FEATHER * 3
    for x in range(edge, 64):          # 칠하지 않은 곳: base 그대로 (경계 근처 제외)
        for y in range(64):
            assert all(abs(a - b) <= 3 for a, b in zip(out.getpixel((x, y)), ref.getpixel((x, y))))
    r, g, b = out.getpixel((5, 30))     # 칠한 곳: edited 색
    assert b > 150 and r < 80


def test_prompt_for_per_model():
    f = inpainter.prompt_for("flash", " 글자를 지워 줘. ")
    k = inpainter.prompt_for("kontext", "글자를 지워 줘.")
    assert "image 3" in f and "글자를 지워 줘:" not in f and "글자를 지워 줘." in f
    # "원본에 맞춰라" + 판매자 설명 (10-11 사용자)
    assert k.startswith("글자를 지워 줘. Match") and "image 3" not in k and "Match image 2" in f
