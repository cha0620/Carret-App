"""VLM 저지 - Gemini 네이티브 SDK 버전."""
import json

from google.genai import types

from app.core.config import settings
from app.core.prompt_registry import get_prompt_text
from app.core.vlm import get_client, thinking
from app.core.tracing import gemini_usage, observe
from app.prompts.rubric import AXES, rubric_text

_SYSTEM_TEMPLATE = """You are a STRICT QC inspector for a secondhand marketplace.
Grade ONLY with the rubric. Analyze first, then score.
Write the analysis as 1~2 sentences in KOREAN (user-facing summary).

RUBRIC:
{{rubric}}

Output JSON only:
{"analysis": "...", "fidelity": 1-5, "realism": 1-5, "trust": 1-5}"""


def _system_prompt() -> str:
    return get_prompt_text("judge_system", fallback=_SYSTEM_TEMPLATE, rubric=rubric_text())


def judge(original: bytes, result: bytes) -> dict:
    client = get_client()   # 프로세스 공용 (연결 재사용 + 타임아웃)
    user_prompt = ("Image1: ORIGINAL, Image2: RESULT. "
                   "Step1: list differences IN THE PRODUCT. "
                   "Step2: scores. JSON only.")
    with observe("judge", as_type="generation", model=settings.VLM_MODEL,
                 input=user_prompt) as obs:
        resp = client.models.generate_content(
            model=settings.VLM_MODEL,
            contents=[
                types.Part.from_bytes(data=original, mime_type="image/png"),  # 이미지1
                types.Part.from_bytes(data=result,   mime_type="image/png"),  # 이미지2
                user_prompt,
            ],
            config=types.GenerateContentConfig(
                system_instruction=_system_prompt(),
                temperature=0,
                response_mime_type="application/json",   # ⭐ JSON 네이티브 강제
                thinking_config=thinking("judge"),
            ),
        )
        report = _validate(json.loads(resp.text))
        if obs is not None:
            obs.update(output=report, usage_details=gemini_usage(resp))
        return report


def _validate(d: dict) -> dict:
    for ax in AXES:
        d[ax] = min(5, max(1, int(d[ax])))
    return d