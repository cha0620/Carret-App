# backend/scripts/seed_langfuse_prompts.py
"""로컬 fallback 프롬프트를 Langfuse에 "production" 라벨로 등록(=시딩)한다.

최초 1회 실행하면 Langfuse 콘솔에서 프롬프트를 확인/수정할 수 있게 되고,
그 뒤로는 콘솔에서 새 버전을 만들고 "production" 라벨을 옮기는 방식으로
운영한다 — 이 스크립트를 다시 돌리면 매번 새 버전이 하나씩 생기므로,
"현재 코드 fallback 상태로 되돌리고 싶을 때"만 재실행한다.

실행:
    cd backend && python scripts/seed_langfuse_prompts.py

필요 환경변수(.env): LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY, LANGFUSE_HOST
"""
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.core.config import settings
from app.prompts import frag
from app.prompts.presets import PRESETS
from app.services.judge import _SYSTEM_TEMPLATE


def _detect_template() -> str:
    return "\n\n".join([
        frag("role_detect"),
        "Item-specific hints: {{hints}}",
        frag("categories"),
        frag("rules_detect"),
        frag("schema_detect"),
    ])


def _verify_template() -> str:
    return "\n\n".join([
        frag("role_verify"),
        frag("rules_verify"),
        frag("schema_verify"),
    ])


def prompts_to_seed() -> dict:
    seeds = {
        "classify": frag("role_classify"),
        "detect_box": frag("detect_box"),
        "detect": _detect_template(),
        "verify": _verify_template(),
        "match": frag("match"),
        "judge_system": _SYSTEM_TEMPLATE,  # {{rubric}} 는 호출 시점에 채워짐
    }
    for key, preset in PRESETS.items():
        seeds[f"preset_{key}"] = preset["fallback_prompt"]
    return seeds


def main():
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

    for name, text in prompts_to_seed().items():
        p = lf.create_prompt(
            name=name,
            prompt=text,
            labels=["production"],
            commit_message="seed: 코드 fallback 값으로 초기화",
        )
        print(f"[seed] {name} -> v{p.version} (production)")


if __name__ == "__main__":
    main()
