"""VLM 저지 - Gemini 네이티브 SDK 버전."""
import json

from google import genai
from google.genai import types

from app.core.config import settings
from app.services.rubric import AXES, rubric_text

SYSTEM = f"""You are a STRICT QC inspector for a secondhand marketplace.
Grade ONLY with the rubric. Analyze first, then score.
Write the analysis as 1~2 sentences in KOREAN (user-facing summary).

RUBRIC:
{rubric_text()}

Output JSON only:
{{"analysis": "...", "fidelity": 1-5, "realism": 1-5, "trust": 1-5}}"""


def judge(original: bytes, result: bytes) -> dict:
    client = genai.Client(api_key=settings.VLM_KEY)   # .env 키 사용
    resp = client.models.generate_content(
        model=settings.VLM_MODEL,
        contents=[
            types.Part.from_bytes(data=original, mime_type="image/png"),  # 이미지1
            types.Part.from_bytes(data=result,   mime_type="image/png"),  # 이미지2
            "Image1: ORIGINAL, Image2: RESULT. "
            "Step1: list differences IN THE PRODUCT. "
            "Step2: scores. JSON only.",
        ],
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM,
            temperature=0,
            response_mime_type="application/json",   # ⭐ JSON 네이티브 강제
        ),
    )
    return _validate(json.loads(resp.text))


def _validate(d: dict) -> dict:
    for ax in AXES:
        d[ax] = min(5, max(1, int(d[ax])))
    return d