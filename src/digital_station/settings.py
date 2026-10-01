from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: SecretStr
    demo_viewer_password: SecretStr
    demo_operator_password: SecretStr
    demo_dispatcher_password: SecretStr
    demo_admin_password: SecretStr
    cookie_secure: bool = False
    allowed_origins: str = "http://127.0.0.1:8000,http://localhost:8000"

    @property
    def origins(self) -> set[str]:
        return {item.strip() for item in self.allowed_origins.split(",") if item.strip()}
