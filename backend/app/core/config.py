import json
import logging
from typing import Annotated

from pydantic import Field, AliasChoices, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict
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
    # 원본 물건 위 글자를 읽어 생성 프롬프트에 넣는다 (VLM 호출 1회 추가)
    text_lock: bool = True
    # 생성 전에 바로 배경 교체 모드로 보내는 기준 (0 = 끔). 생성 모델이 거의 확실히
    # 못 지키는 사진에 FLUX·재생성·verify 비용을 쓰지 않기 위함. 경험값 — eval 로 조정.
    #   글자 줄 수: 잔글씨 많은 물건(책·화장품)은 TEXT_LOCK 이 있어도 뭉개지기 쉽다
    composite_first_min_texts: int = Field(default=12, ge=0)
    #   하자 앵커 수: 기본은 끔 — 하자가 많다고 생성이 꼭 실패하지는 않는다
    composite_first_min_anchors: int = Field(default=0, ge=0)
    # 배경 교체 모드의 물건 오리기: "fal"(BiRefNet, 기본) | "local"(rembg)
    cutout_backend: str = "fal"
    # 로컬 OCR(EasyOCR) 로 글자 보존을 한 번 더 재는 soft 가드 (ocr_local). CPU 수 초·메모리
    # 수백 MB 라 기본은 끔 — eval 돌릴 때만 켠다. easyocr 는 requirements 에 없다 (따로 설치).
    local_ocr_guard: bool = False

    # VLM (소문자 통일)
    VLM_KEY: str = ""
    VLM_MODEL: str = "gemini-3.8-flash"   # 2026-09-26 3.5 → 3.8 (헛하자↓·단가 ½, app/core/vlm.py 참고)
    # VLM 호출 1회 타임아웃(초). verify(생각 2048 토큰)도 보통 10~20초 — 여유를 두되
    # 재시도(×2)까지 겹쳐 요청 전체가 수 분으로 늘지 않게. 1 미만은 ms 변환 시 0 = 무제한이 된다.
    vlm_timeout_s: float = Field(default=60, ge=1)
    # 호출 이름별 생각 수준 덮어쓰기 (기본값은 app/core/vlm.py DEFAULT_THINKING)
    vlm_thinking: Annotated[dict[str, str | int], NoDecode] = Field(default_factory=dict)
    # 호출 이름별 이미지 해상도 덮어쓰기 (기본값은 app/core/vlm.py DEFAULT_MEDIA_RESOLUTION)
    vlm_media_resolution: Annotated[dict[str, str], NoDecode] = Field(default_factory=dict)
    # 호출 이름별 모델 덮어쓰기 (없으면 VLM_MODEL). 예: {"classify": "gemini-3.5-flash-lite"}
    vlm_models: Annotated[dict[str, str], NoDecode] = Field(default_factory=dict)

    @field_validator("vlm_thinking", "vlm_media_resolution", "vlm_models", mode="before")
    @classmethod
    def _lenient_thinking(cls, v, info):
        """튜닝용 설정 하나가 틀렸다고 앱 전체가 안 뜨면 안 된다 — 깨진 JSON 은 무시."""
        if isinstance(v, str):
            try:
                v = json.loads(v) if v.strip() else {}
            except json.JSONDecodeError:
                logging.getLogger("carret.config").warning(
                    f"{info.field_name.upper()} JSON 파싱 실패 — 무시")
                return {}
        return v if isinstance(v, dict) else {}

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