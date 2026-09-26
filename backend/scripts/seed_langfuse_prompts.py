# backend/scripts/seed_langfuse_prompts.py
"""로컬 fallback 프롬프트를 Langfuse에 "production" 라벨로 등록(=시딩)한다.

그 뒤로는 콘솔에서 새 버전을 만들고 "production" 라벨을 옮기는 방식으로 운영한다.
create_prompt 는 매번 새 버전을 만들고 production 라벨을 그쪽으로 옮기므로,
기본 실행은 **Langfuse 에 아직 없는 이름만** 등록한다 (콘솔 수정분 보호).

실행:
    cd backend && python scripts/seed_langfuse_prompts.py              # 없는 것만
    cd backend && python scripts/seed_langfuse_prompts.py item_text    # 지정한 것 (덮어씀)
    cd backend && python scripts/seed_langfuse_prompts.py --all        # 전부 코드 값으로 덮어씀

⚠️ preset_* 는 잠금 문구(SECONDHAND_LOCK) 없이 배경 묘사만 올라간다 — 잠금을
get_preset() 이 코드에서 붙이기 때문. 이 코드가 배포되기 전에 preset_* 를
덮어쓰면 옛 서버가 잠금 없이 생성한다 → 배포 완료 후에만 실행할 것.

필요 환경변수(.env): LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY, LANGFUSE_HOST
"""
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.core.config import settings
from app.prompts import (check_photo_template, detect_template, frag,
                         verify_template)
from app.prompts.presets import PRESETS
from app.services.ai.auto_feedback import _SYSTEM_TEMPLATE as AUTO_FEEDBACK_TEMPLATE
from app.services.ai.judge import _SYSTEM_TEMPLATE


def prompts_to_seed() -> dict:
    """get_prompt_text() 를 부르는 모든 이름 — 새 프롬프트를 추가하면 여기에도 추가.
    (빠지면 Langfuse 미등록 → 호출마다 404 조회 후 fallback, 콘솔 수정 불가)"""
    seeds = {
        "classify": frag("role_classify"),
        "detect_box": frag("detect_box"),
        "detect": detect_template(),
        "verify": verify_template(),
        "item_text": frag("item_text"),
        "check_photo": check_photo_template(),
        "match": frag("match"),
        "judge_system": _SYSTEM_TEMPLATE,  # {{rubric}} 는 호출 시점에 채워짐
        "auto_feedback_system": AUTO_FEEDBACK_TEMPLATE,
    }
    # 배경 묘사만 — SECONDHAND_LOCK 은 get_preset() 이 코드에서 붙인다
    for key, preset in PRESETS.items():
        seeds[f"preset_{key}"] = preset["fallback_prompt"]
    return seeds


def _exists(lf, name: str) -> bool:
    from langfuse.api import NotFoundError
    try:
        lf.get_prompt(name, label="production", cache_ttl_seconds=0, max_retries=0)
        return True
    except NotFoundError:
        return False


def main():
    seeds = prompts_to_seed()
    args = sys.argv[1:]
    if "-h" in args or "--help" in args:
        print(__doc__)
        return
    overwrite_all = "--all" in args
    names = list(dict.fromkeys(a for a in args if a != "--all"))
    unknown = [n for n in names if n not in seeds]
    if unknown or (overwrite_all and names):
        print(f"알 수 없는 인자: {unknown or ['--all 과 이름은 같이 못 씀']}\n"
              f"가능한 이름: {list(seeds)}")
        sys.exit(1)

    if not (settings.langfuse_public_key and settings.langfuse_secret_key):
        print("LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY 가 없습니다. .env 확인.")
        sys.exit(1)

    from langfuse import Langfuse
    lf = Langfuse(
        public_key=settings.langfuse_public_key,
        secret_key=settings.langfuse_secret_key,
        host=settings.langfuse_host,
    )
    if not lf.auth_check():
        print("Langfuse 인증 실패 — 키/호스트를 확인하세요.")
        sys.exit(1)

    if overwrite_all:
        names = list(seeds)
    elif not names:
        names = [n for n in seeds if not _exists(lf, n)]
        if not names:
            print("[seed] 모든 프롬프트가 이미 등록돼 있음 — 할 일 없음")
            return

    for name in names:
        text = seeds[name]
        p = lf.create_prompt(
            name=name,
            prompt=text,
            labels=["production"],
            commit_message="seed: 코드 fallback 값으로 초기화",
        )
        print(f"[seed] {name} -> v{p.version} (production)")


if __name__ == "__main__":
    main()
