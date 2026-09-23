from pydantic import Field, AliasChoices
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Literal


class Settings(BaseSettings):              # ⭐ 대문자 클래스
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

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
    # 구도 잘림/자막 오버레이 감지 시 재생성 횟수 (최초 포함) — 상한 없으면
    # 오설정(.env 오타 등) 시 invalid 가 계속 나올 때 fal.ai/VLM 비용이 무한정 나간다.
    max_generate_attempts: int = Field(default=2, ge=1, le=5)

    # VLM (소문자 통일)
    VLM_KEY: str = ""
    VLM_MODEL: str = "gemini-3.5-flash"

    # 파이프라인 모드
    pipeline_mode:str = "real"  # ⭐ Literal 로 고정

    # 기타
    max_bytes: int = 10 * 1024 * 1024
    dev_tools: bool = True

    inspect_img: str | None = None

    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = Field(
        default="https://cloud.langfuse.com",
        validation_alias=AliasChoices("LANGFUSE_HOST", "LANGFUSE_BASE_URL"),
    )

    db_path: str = "data/carret.db"
    storage_backend: str = "local"      # "local" | "s3"
    s3_bucket: str = ""
    s3_prefix: str = "carret"
    aws_region: str = "ap-northeast-2"
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""

settings = Settings()          # ⭐ 인스턴스 = 소문자