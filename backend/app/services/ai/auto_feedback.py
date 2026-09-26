"""자동 피드백 에이전트 — 실사용자 피드백을 당장 모으기 어려운 동안 대신 채워주는 VLM.

`judge.py`(fidelity/realism/trust, QC 관점)와는 독립적인 VLM 호출 —
"최종 사용자(구매자/판매자)라면 이 결과를 보고 몇 점을 주고 무슨 말을 남길지"를
직접 묻는다. `store.save_feedback(..., source="agent")`로 저장되어 실사용자
피드백(source="user")과 항상 구분되고, 실사용자 피드백이 이미 있으면 덮어쓰지 않는다.
"""
import json

from google.genai import types

from app.core.config import settings
from app.core.prompt_registry import get_prompt_text
from app.core.vlm import get_client, image_part, thinking
from app.core.vlm import model as vlm_model
from app.core.tracing import gemini_usage, observe

_SYSTEM_TEMPLATE = """You are the SELLER on a secondhand marketplace, looking at your \
product photo after it was auto-edited (background replaced/staged).
React like a real end user would — the overall impression of the photo, not a QC checklist.
Would you actually use this photo in your listing?
Write the comment as 1 short sentence in KOREAN.

Output JSON only:
{"rating": 1-5, "comment": "..."}"""


def _system_prompt() -> str:
    return get_prompt_text("auto_feedback_system", fallback=_SYSTEM_TEMPLATE)


def generate_feedback(original: bytes, result: bytes) -> dict:
    client = get_client()   # 프로세스 공용 (연결 재사용 + 타임아웃)
    user_prompt = ("Image1: BEFORE (original), Image2: AFTER (result). "
                   "You are the seller who just got this AFTER photo back. "
                   "Rate 1-5 and leave one short comment. JSON only.")
    with observe("auto_feedback", as_type="generation", model=vlm_model("auto_feedback"),
                 input=user_prompt) as obs:
        resp = client.models.generate_content(
            model=vlm_model("auto_feedback"),
            contents=[
                image_part(original, "image/png", "auto_feedback"),
                image_part(result, "image/png", "auto_feedback"),
                user_prompt,
            ],
            config=types.GenerateContentConfig(
                system_instruction=_system_prompt(),
                temperature=0,
                response_mime_type="application/json",
                thinking_config=thinking("auto_feedback"),
            ),
        )
        out = _validate(json.loads(resp.text))
        if obs is not None:
            obs.update(output=out, usage_details=gemini_usage(resp))
        return out


def _validate(d: dict) -> dict:
    d["rating"] = min(5, max(1, int(d["rating"])))
    comment = str(d.get("comment") or "").strip()
    d["comment"] = comment or None
    return d
