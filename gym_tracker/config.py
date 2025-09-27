# gym_tracker/config.py
import os
from functools import lru_cache
from urllib.parse import quote_plus

try:
    from dotenv import load_dotenv  # pip install python-dotenv
    load_dotenv()
except Exception:
    pass

class Settings:
    # ---- Database (matches your database.py) ----
    DB_USER: str = os.getenv("DB_USER", "gym")
    DB_PASSWORD: str = os.getenv("DB_PASSWORD", "abc123")
    DB_HOST: str = os.getenv("DB_HOST", "localhost")
    DB_PORT: str = os.getenv("DB_PORT", "3306")
    DB_NAME: str = os.getenv("DB_NAME", "gym_tracker")

    # Full URL wins; else build from parts (defaults to MySQL/PyMySQL)
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL",
        f"mysql+pymysql://{quote_plus(DB_USER)}:{quote_plus(DB_PASSWORD)}@{DB_HOST}:{DB_PORT}/{DB_NAME}",
    )

    # Alias for libraries that expect this name
    SQLALCHEMY_DATABASE_URL: str = os.getenv(
        "SQLALCHEMY_DATABASE_URL",
        DATABASE_URL,
    )

    # Optional pool tuning (used only if your engine opts in)
    DB_POOL_PRE_PING: bool = os.getenv("DB_POOL_PRE_PING", "true").lower() == "true"
    DB_POOL_SIZE: int = int(os.getenv("DB_POOL_SIZE", "5"))
    DB_MAX_OVERFLOW: int = int(os.getenv("DB_MAX_OVERFLOW", "10"))
    DB_POOL_TIMEOUT: int = int(os.getenv("DB_POOL_TIMEOUT", "30"))   # seconds
    DB_POOL_RECYCLE: int = int(os.getenv("DB_POOL_RECYCLE", "1800")) # seconds

    # ---- App session/auth ----
    SESSION_SECRET: str = os.getenv("SESSION_SECRET", "change-me-please")
    SESSION_COOKIE_NAME: str = os.getenv("SESSION_COOKIE_NAME", "gt_session")

    GOOGLE_CLIENT_ID: str = os.getenv("GOOGLE_CLIENT_ID", "")
    GOOGLE_CLIENT_SECRET: str = os.getenv("GOOGLE_CLIENT_SECRET", "")
    OAUTH_REDIRECT_URI: str = os.getenv("OAUTH_REDIRECT_URI", "http://localhost:8000/auth/callback")
    BASE_URL: str = os.getenv("BASE_URL", "http://localhost:8000")
    ALLOWED_EMAILS: str = os.getenv("ALLOWED_EMAILS", "")

    @property
    def allowed_emails_set(self) -> set[str]:
        return {e.strip().lower() for e in self.ALLOWED_EMAILS.split(",") if e.strip()}

    # Helpers
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
