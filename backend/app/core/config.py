from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Literal


class Settings(BaseSettings):              # ⭐ 대문자 클래스
    model_config = SettingsConfigDict(env_file=".env")

    # 서버
    host: str = "0.0.0.0"
    port: int = 8000
    debug: bool = True

    # 저장소
    storage_dir: str = "./storage"
    max_upload_size_mb: int = 10
    image_max_side: int = 1600
    image_quality: int = 88

    # 생성 모델
    model_provider: str = "external_api"
    external_api_key: str = ""
    external_model_id: str = ""
    local_model_path: str = "./models"
    device: str = "cpu"
    fal_key: str = ""
    fal_model: str = "fal-ai/flux-2/flash/edit"

    # VLM (소문자 통일)
    VLM_KEY: str = ""
    VLM_MODEL: str = "gemini-3.5-flash"

    # 파이프라인 모드
    pipeline_mode:str = "real"  # ⭐ Literal 로 고정

    # 기타
    max_bytes: int = 10 * 1024 * 1024
    dev_tools: bool = True

    inspect_img: str | None = None


settings = Settings()          # ⭐ 인스턴스 = 소문자