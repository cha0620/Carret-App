import sys
from pathlib import Path

# ⭐ 이 파일 기준 두 칸 위(= backend/) 를 모듈 검색 경로에 추가
sys.path.append(str(Path(__file__).resolve().parents[1]))

"""Dataset 이미지 → 모델 변환 → after 폴더 저장."""
from app.prompts.presets import get_preset
from app.services import generator, storage

# ---- 경로 (대소문자/하위폴더 둘 다 허용) ----
BASE = storage.BASE / "Dataset"

SRC = BASE / "img" if (BASE / "img").exists() else BASE
DST = BASE / "after"

PRESET = get_preset("studio_white")     # 바꿀 프리셋 여기서
MODEL  = None                        # None = settings 기본 모델

EXTS = ("*.jpg", "*.jpeg", "*.png", "*.webp")


def main():
    DST.mkdir(parents=True, exist_ok=True)   # after 폴더 자동 생성
    files = [p for pat in EXTS for p in sorted(SRC.glob(pat))]
    print(f"[info] 이미지 {len(files)}장 발견 → {DST}")

    for img in files:
        print(f"[run ] {img.name}")
        try:
            result = generator._generate_ai(img.read_bytes(), PRESET)
        except Exception as e:
            print(f"[fail] {img.name}: {e}")   # 한 장 실패해도 나머지는 진행
            continue

        out = DST / f"{img.stem}.jpg"
        out.write_bytes(result)
        print(f"[save] {out.name}")

    print("[done] 🎉")


if __name__ == "__main__":
    main()