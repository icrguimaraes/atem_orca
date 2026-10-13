from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "ATEM Orçamento"
    environment: str = "development"
    database_url: str = "postgresql+psycopg://postgres@localhost:5432/atem"
    secret_key: str = "change-me"
    access_token_minutes: int = 60 * 10
    upload_dir: str = "./data/uploads"
    max_upload_mb: int = 50
    run_import_worker: bool = True
    worker_poll_seconds: float = 2.0
    cors_origins: str = "*"
    admin_email: str = "admin@atem.com.br"
    admin_password: str | None = None
    admin_name: str = "Administrador"
    # integração com o Movimentação de Pessoal (leitura do quadro); vazio = desligada
    movpessoal_token: str | None = None
    # integração com o SAP HANA Cloud (KSB1 direto da view); vazio = desligada. Usuário e senha só por variável de
    # ambiente (Railway) e de preferência um usuário somente leitura.
    hana_host: str | None = None
    hana_port: int = 443
    hana_user: str | None = None
    hana_password: str | None = None
    hana_schema: str = "FD_S4HANA_PRD"
    hana_view: str = "VWG_FD_KSB1"

    @property
    def sqlalchemy_url(self) -> str:
        # Railway entrega DATABASE_URL como postgres:// ou postgresql://
        url = self.database_url
        for prefix in ("postgres://", "postgresql://"):
            if url.startswith(prefix):
                return "postgresql+psycopg://" + url[len(prefix) :]
        return url


@lru_cache
def get_settings() -> Settings:
    return Settings()
