"""내 키로 사용 가능한 Gemini 모델 목록."""
from google import genai

from app.core.config import settings   # ← .env 의 VLM_KEY 가 여기로

client = genai.Client(api_key=settings.VLM_KEY)

for m in client.models.list():
    print(m.name)