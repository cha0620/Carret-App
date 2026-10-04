"""app.core.config.Settings 엣지 케이스.

- extra="ignore": .env / 환경변수에 모델이 모르는 키가 있어도 부팅 크래시가
  나면 안 된다.
- langfuse_public_key / langfuse_secret_key 는 코드상 기본값이 ""(빈 문자열)
  이어야 한다(과거에 실제 키 문자열이 하드코딩돼 있던 회귀 방지). 실행 중인
  backend/.env 에는 실제 값이 들어있을 수 있으므로, 인스턴스 값이 아니라
  클래스에 선언된 기본값(model_fields[...].default) 자체를 검사해서
  .env 내용과 무관하게 결정적으로 확인한다.
"""
from pydantic import SecretStr

from app.core.config import Settings, reveal


def test_settings_survives_unknown_env_var(monkeypatch):
    """모델에 없는 키가 환경변수로 들어와도 Settings() 생성이 크래시하면 안 된다."""
    monkeypatch.setenv("SOME_TOTALLY_UNKNOWN_CONFIG_KEY_XYZ", "whatever")
    s = Settings()  # extra="ignore" 가 없으면 pydantic ValidationError 로 죽는다
    assert not hasattr(s, "some_totally_unknown_config_key_xyz")


def test_langfuse_host_reads_langfuse_base_url_env(monkeypatch):
    """.env 관례가 LANGFUSE_BASE_URL 이라, 이 이름으로도 반영돼야 한다
    (과거엔 필드명이 LANGFUSE_HOST 로만 매핑돼서 .env의 LANGFUSE_BASE_URL이
    조용히 무시되던 회귀 방지 — 리전이 안 맞아도 에러 없이 기본 호스트로
    빠져서 발견이 늦었다)."""
    monkeypatch.setenv("LANGFUSE_BASE_URL", "https://us.cloud.langfuse.com")
    s = Settings()
    assert s.langfuse_host == "https://us.cloud.langfuse.com"


SECRET_FIELDS = ["VLM_KEY", "fal_key", "external_api_key", "langfuse_public_key",
                 "langfuse_secret_key", "aws_access_key_id", "aws_secret_access_key"]


def test_settings_repr_and_str_hide_secret_values(monkeypatch):
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "super-secret-value-123")
    monkeypatch.setenv("VLM_KEY", "vlm-secret-456")
    s = Settings()
    for text in (repr(s), str(s), str(s.model_dump())):
        assert "super-secret-value-123" not in text
        assert "vlm-secret-456" not in text
    assert reveal(s.aws_secret_access_key) == "super-secret-value-123"
    assert reveal(s.VLM_KEY) == "vlm-secret-456"


def test_reveal_accepts_plain_str_and_none():
    """테스트가 monkeypatch 로 평문을 넣어도, 값이 없어도 호출 지점이 깨지지 않는다."""
    assert reveal("plain") == "plain"
    assert reveal(None) == ""
    assert reveal(SecretStr("")) == ""


