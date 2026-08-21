from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    supabase_url: str
    supabase_service_role_key: str
    supabase_jwt_secret: str

    r2_account_id: str
    r2_access_key_id: str
    r2_secret_access_key: str
    r2_bucket_name: str

    # Our own tokens (issued by FastAPI after proxying Supabase login), not
    # Supabase's own JWTs — see app/auth.py for why the browser never talks
    # to Supabase directly.
    app_jwt_secret: str = "dev-secret-change-me"
    app_jwt_access_ttl_seconds: int = 3600
    app_jwt_refresh_ttl_seconds: int = 60 * 60 * 24 * 30

    parquet_cache_dir: str = "/tmp/bbstore-parquet-cache"


@lru_cache
def get_settings() -> Settings:
    return Settings()
