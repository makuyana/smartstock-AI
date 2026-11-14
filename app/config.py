"""Settings come from environment variables only."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    meta_app_secret: str = ""
    webhook_verify_token: str = ""
    whatsapp_token: str = ""
    whatsapp_phone_number_id: str = ""
    instagram_token: str = ""
    instagram_account_id: str = ""
    database_url: str = ""
    llm_api_key: str = ""
    llm_base_url: str = "https://api.openai.com/v1"
    llm_model: str = "gpt-4o-mini"

    graph_api_version: str = "v21.0"
    graph_api_base_url: str = "https://graph.facebook.com"
    instagram_graph_base_url: str = "https://graph.instagram.com"
    media_dir: str = "/data/media"
    scheduler_enabled: bool = True
    scheduler_secret: str = ""
    app_env: str = "production"
    log_level: str = "INFO"
    seed_demo: bool = False


def normalize_database_url(url: str) -> str:
    """Accept postgres:// and require SSL for hosted databases."""
    cleaned = url.strip()
    if cleaned.startswith("postgres://"):
        cleaned = "postgresql://" + cleaned[len("postgres://") :]
    local = any(
        marker in cleaned
        for marker in ("localhost", "127.0.0.1", "host.docker.internal", "@db:", "@db/")
    )
    if cleaned and not local and "sslmode=" not in cleaned:
        joiner = "&" if "?" in cleaned else "?"
        cleaned = f"{cleaned}{joiner}sslmode=require"
    return cleaned
