from pathlib import Path
from typing import Literal, Self
from urllib.parse import parse_qs, unquote, urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", extra="ignore", populate_by_name=True, hide_input_in_errors=True
    )

    database_url: SecretStr
    database_connect_timeout_seconds: int = Field(default=10, gt=0)
    database_io_timeout_seconds: int = Field(default=30, gt=0)
    database_pool_timeout_seconds: int = Field(default=10, gt=0)
    redis_url: SecretStr = Field(min_length=1)
    graceful_shutdown_timeout_seconds: int = Field(default=3700, gt=0)
    oidc_issuer_url: str
    oidc_client_id: str = Field(min_length=1)
    oidc_client_secret: SecretStr = Field(min_length=1)
    oidc_redirect_uri: str
    oidc_username_claim: str = "preferred_username"
    oidc_scopes: str = "openid profile"
    oidc_token_endpoint_auth_method: Literal["client_secret_basic", "client_secret_post"] = (
        "client_secret_basic"
    )
    oidc_allowed_usernames: list[str] = Field(default_factory=list)
    oidc_admin_usernames: list[str] = Field(min_length=1)
    frontend_url: str
    cors_allowed_origins: list[str] = Field(default_factory=list)
    cookie_domain: str | None = None
    cookie_name: str = Field(default="ars_session", pattern=r"^[A-Za-z0-9_-]{1,64}$")
    cookie_secure: bool = True
    cookie_samesite: Literal["lax", "none"] = "lax"
    session_ttl_seconds: int = Field(default=28800, gt=0)
    oidc_login_ttl_seconds: int = Field(default=600, gt=0, le=600)
    oidc_timeout_seconds: float = Field(default=15, gt=0)
    apple_api_key_file: Path = Field(validation_alias="APPLE_KEY_FILE")
    data_dir: Path = Path("/data")
    signing_certificate_file: Path = Path("/run/secrets/signing.p12")
    signing_password_file: Path = Path("/run/secrets/signing-password")
    rcodesign_bin: Path = Path("/usr/local/bin/rcodesign")
    rcodesign_work_dir: Path = Path("/app")
    rcodesign_config_file: Path | None = None
    command_timeout_seconds: float = Field(default=1800, gt=0)
    staple_timeout_seconds: float = Field(default=120, gt=0)
    apple_timeout_seconds: float = Field(default=30, gt=0)
    max_upload_bytes: int = Field(default=2 * 1024**3, gt=0)
    max_extracted_bytes: int = Field(default=4 * 1024**3, gt=0)
    max_archive_entries: int = Field(default=100_000, gt=0)
    s3_bucket: str = Field(min_length=1)
    s3_prefix: str = "app-dist/"
    s3_endpoint_url: str | None = None
    s3_region: str = Field(default="us-east-1", min_length=1)
    s3_addressing_style: Literal["auto", "virtual", "path"] = "auto"
    s3_connect_timeout_seconds: float = Field(default=10, gt=0)
    s3_read_timeout_seconds: float = Field(default=60, gt=0)
    aws_access_key_id: SecretStr | None = Field(default=None, min_length=1)
    aws_secret_access_key: SecretStr | None = Field(default=None, min_length=1)
    aws_session_token: SecretStr | None = Field(default=None, min_length=1)
    cdn_base_url: str
    cdn_sign_key: SecretStr = Field(min_length=1)
    cdn_sign_param: str = Field(default="eo_sign", pattern=r"^[A-Za-z0-9_-]{1,100}$")
    wecom_webhook_url: SecretStr | None = None
    wecom_timeout_seconds: float = Field(default=5, gt=0, allow_inf_nan=False)
    otel_service_name: str = "apple-remote-sign"
    otel_exporter_otlp_endpoint: str = ""
    otel_exporter_otlp_traces_endpoint: str = ""

    @property
    def allowed_usernames(self) -> set[str]:
        return set(self.oidc_allowed_usernames) | set(self.oidc_admin_usernames)

    @property
    def allowed_origins(self) -> list[str]:
        return sorted(
            {value.rstrip("/") for value in [self.frontend_url, *self.cors_allowed_origins]}
        )

    @property
    def distribution_secrets(self) -> list[str]:
        return [
            value.get_secret_value()
            for value in (
                self.cdn_sign_key,
                self.aws_access_key_id,
                self.aws_secret_access_key,
                self.aws_session_token,
            )
            if value is not None
        ]

    @property
    def redis_secrets(self) -> list[str]:
        value = self.redis_url.get_secret_value()
        return [value, unquote(urlsplit(value).password or "")]

    @property
    def wecom_secrets(self) -> list[str]:
        if self.wecom_webhook_url is None:
            return []
        value = self.wecom_webhook_url.get_secret_value()
        return [value, *parse_qs(urlsplit(value).query).get("key", [])]

    @field_validator("wecom_webhook_url")
    @classmethod
    def validate_wecom_webhook_url(cls, value: SecretStr | None) -> SecretStr | None:
        if value is None or not value.get_secret_value().strip():
            return None
        raw = value.get_secret_value().strip()
        try:
            url = urlsplit(raw)
            valid = (
                url.scheme == "https"
                and bool(url.hostname)
                and url.username is None
                and url.password is None
                and not url.fragment
                and not any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in raw)
            )
            _ = url.port
        except ValueError:
            valid = False
        if not valid:
            raise ValueError("WECOM_WEBHOOK_URL must be an absolute HTTPS URL without credentials")
        return SecretStr(raw)

    @field_validator("redis_url")
    @classmethod
    def validate_redis_url(cls, value: SecretStr) -> SecretStr:
        try:
            url = urlsplit(value.get_secret_value())
            valid = url.scheme in {"redis", "rediss"} and bool(url.hostname) and not url.fragment
            _ = url.port
        except ValueError:
            valid = False
        if not valid:
            raise ValueError("REDIS_URL must be an absolute redis:// or rediss:// URL")
        return value

    @field_validator("s3_prefix")
    @classmethod
    def normalize_s3_prefix(cls, value: str) -> str:
        prefix = value.strip("/")
        if any(part in {".", ".."} for part in prefix.split("/")):
            raise ValueError("S3_PREFIX cannot contain dot path segments")
        if "\\" in prefix or any(ord(char) < 32 or ord(char) == 127 for char in prefix):
            raise ValueError("S3_PREFIX cannot contain backslashes or control characters")
        return prefix + "/" if prefix else ""

    @field_validator("cdn_base_url")
    @classmethod
    def validate_cdn_base_url(cls, value: str) -> str:
        url = urlsplit(value)
        if (
            url.scheme not in {"http", "https"}
            or not url.hostname
            or url.username is not None
            or url.password is not None
            or url.path not in {"", "/"}
            or url.query
            or url.fragment
        ):
            raise ValueError("CDN_BASE_URL must be an HTTP(S) origin without credentials or path")
        if url.scheme == "http" and url.hostname not in {"localhost", "127.0.0.1", "testserver"}:
            raise ValueError("CDN_BASE_URL requires HTTPS outside local development")
        return value.rstrip("/")

    @field_validator("s3_endpoint_url")
    @classmethod
    def validate_s3_endpoint_url(cls, value: str | None) -> str | None:
        if not value:
            return None
        endpoint = urlsplit(value)
        if (
            endpoint.scheme not in {"http", "https"}
            or not endpoint.hostname
            or endpoint.username is not None
            or endpoint.password is not None
            or endpoint.query
            or endpoint.fragment
        ):
            raise ValueError("S3_ENDPOINT_URL must be an absolute HTTP(S) URL without credentials")
        return value

    @model_validator(mode="after")
    def validate_aws_credentials(self) -> Self:
        if bool(self.aws_access_key_id) != bool(self.aws_secret_access_key):
            raise ValueError(
                "AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY must be configured together"
            )
        return self

    @model_validator(mode="after")
    def validate_web_config(self) -> Self:
        if self.cookie_samesite == "none" and not self.cookie_secure:
            raise ValueError("COOKIE_SAMESITE=none requires COOKIE_SECURE=true")
        urls = [self.oidc_issuer_url, self.oidc_redirect_uri, *self.allowed_origins]
        for value in urls:
            url = urlsplit(value)
            if url.scheme not in {"http", "https"} or not url.hostname or url.username:
                raise ValueError("OIDC and frontend URLs must be absolute HTTP(S) URLs")
            if url.query or url.fragment or url.password:
                raise ValueError("Configured URLs cannot contain credentials, query or fragment")
            if url.scheme == "http" and url.hostname not in {
                "localhost",
                "127.0.0.1",
                "testserver",
            }:
                raise ValueError("HTTP is only allowed for local development")
        if any(urlsplit(origin).path not in {"", "/"} for origin in self.allowed_origins):
            raise ValueError("FRONTEND_URL and CORS_ALLOWED_ORIGINS must be origins without paths")
        if any(not name or len(name) > 255 for name in self.allowed_usernames):
            raise ValueError("Usernames must contain 1 to 255 characters")
        if "openid" not in self.oidc_scopes.split():
            raise ValueError("OIDC_SCOPES must include openid")
        return self

    @model_validator(mode="after")
    def validate_cookie_domain(self) -> Self:
        if self.cookie_domain:
            domain = self.cookie_domain.lstrip(".").lower()
            host = urlsplit(self.oidc_redirect_uri).hostname or ""
            if host != domain and not host.endswith("." + domain):
                raise ValueError("COOKIE_DOMAIN must be the API hostname or its parent domain")
        return self
