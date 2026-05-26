from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="WARP_")

    database_url: str = "sqlite:///./warp_orchestrator.db"
    redis_url: str = "redis://redis:6379/0"
    secret_key: str = "change-me-in-production"
    admin_username: str = "admin"
    admin_password: str = "admin"
    access_token_minutes: int = 720
    timezone: str = "Europe/Moscow"
    ansible_private_data_dir: str = "/tmp/warp-ansible-runner"


@lru_cache
def get_settings() -> Settings:
    return Settings()
