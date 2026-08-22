from pydantic_settings import BaseSettings, SettingsConfigDict


class settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env")

    # ===== 서버 =====
    host: str = "0.0.0.0"        # Codespaces 외부 접속용
    port: int = 8000
    debug: bool = True           # 개발 모드 (--reload 용)

    # ===== 저장소 =====
    storage_dir: str = "./storage"
    max_upload_size_mb: int = 10

    # ===== 생성 모델 =====
    model_provider: str = "external_api"   # "local" | "external_api"
    external_api_key: str = ""             # 비밀! .env에서만 주입
    external_model_id: str = ""
    local_model_path: str = "./models"
    device: str = "cpu"                    # "cuda" | "cpu"

    pipeline_mode:str = "real"

    fal_key: str = ""
    fal_model: str = "fal-ai/flux-2/flash/edit"   # FLUX 인페인팅

    VLM_KEY:str = ""
    VLM_MODEL:str   = "gemini-3.5-flash"

    max_bytes: int = 10 * 1024 * 1024
settings = settings()   # 전역 단일 인스턴스