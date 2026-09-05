import os
from functools import lru_cache
from urllib.parse import quote_plus

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass


class Settings:
    # ---- Database ----
    DB_USER: str
    DB_PASSWORD: str
    DB_HOST: str
    DB_PORT: str
    DB_NAME: str
    DATABASE_URL: str
    SQLALCHEMY_DATABASE_URL: str

    def __init__(self):
        self.DB_USER     = os.getenv("DB_USER", "gym")
        self.DB_PASSWORD = os.getenv("DB_PASSWORD", "abc123")
        self.DB_HOST     = os.getenv("DB_HOST", "localhost")
        self.DB_PORT     = os.getenv("DB_PORT", "3306")
        self.DB_NAME     = os.getenv("DB_NAME", "gym_tracker")

        # Full URL wins; else build from parts
        _built_url = (
            f"mysql+pymysql://{quote_plus(self.DB_USER)}:{quote_plus(self.DB_PASSWORD)}"
            f"@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"
        )
        self.DATABASE_URL = os.getenv("DATABASE_URL") or _built_url
        self.SQLALCHEMY_DATABASE_URL = os.getenv("SQLALCHEMY_DATABASE_URL") or self.DATABASE_URL

        # ---- Pool tuning ----
        self.DB_POOL_PRE_PING: bool = os.getenv("DB_POOL_PRE_PING", "true").lower() == "true"
        self.DB_POOL_SIZE: int     = int(os.getenv("DB_POOL_SIZE", "5"))
        self.DB_MAX_OVERFLOW: int  = int(os.getenv("DB_MAX_OVERFLOW", "10"))
        self.DB_POOL_TIMEOUT: int  = int(os.getenv("DB_POOL_TIMEOUT", "30"))
        self.DB_POOL_RECYCLE: int  = int(os.getenv("DB_POOL_RECYCLE", "1800"))

        # ---- App session/auth ----
        self.SESSION_SECRET: str      = os.getenv("SESSION_SECRET", "change-me-please")
        self.SESSION_COOKIE_NAME: str = os.getenv("SESSION_COOKIE_NAME", "gt_session")

        self.GOOGLE_CLIENT_ID: str     = os.getenv("GOOGLE_CLIENT_ID", "")
        self.GOOGLE_CLIENT_SECRET: str = os.getenv("GOOGLE_CLIENT_SECRET", "")
        self.OAUTH_REDIRECT_URI: str   = os.getenv("OAUTH_REDIRECT_URI", "http://localhost:8000/auth/callback")
        self.BASE_URL: str             = os.getenv("BASE_URL", "http://localhost:8000")
        self.ALLOWED_EMAILS: str       = os.getenv("ALLOWED_EMAILS", "")

        # ---- Outbound email (client invites) ----
        self.EMAIL_ENABLED: bool = (
            os.getenv("EMAIL_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
        )
        self.EMAIL_PROVIDER: str = os.getenv("EMAIL_PROVIDER", "resend")
        self.RESEND_API_KEY: str = os.getenv("RESEND_API_KEY", "")
        self.EMAIL_FROM: str     = os.getenv("EMAIL_FROM", "Gym Tracker <admin@gym.x-mas.ro>")
        self.EMAIL_REPLY_TO: str = os.getenv("EMAIL_REPLY_TO", "dumitru@x-mas.ro")
        # Base URL for building the /invite/confirm link; empty -> derive from the request.
        self.APP_BASE_URL: str   = os.getenv("APP_BASE_URL", "")

    @property
    def allowed_emails_set(self) -> set[str]:
        return {e.strip().lower() for e in self.ALLOWED_EMAILS.split(",") if e.strip()}

    @property
    def is_sqlite(self) -> bool:
        return self.SQLALCHEMY_DATABASE_URL.startswith("sqlite")

    @property
    def is_mysql(self) -> bool:
        return self.SQLALCHEMY_DATABASE_URL.startswith("mysql")

    @property
    def is_postgres(self) -> bool:
        return self.SQLALCHEMY_DATABASE_URL.startswith("postgres")


@lru_cache
def get_settings() -> Settings:
    return Settings()
