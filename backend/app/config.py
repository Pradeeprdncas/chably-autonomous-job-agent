from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    app_env: str = "development"
    frontend_url: str = "http://localhost:5173"
    data_dir: str = "./data"
    database_url: str = ""
    chroma_path: str = ""
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash-lite"
    mistral_api_key: str = ""
    mistral_model: str = "mistral-small-latest"
    ai_mock_mode: bool = False
    search_provider: str = "searxng"
    search_provider_order: str = "serper,tavily,ddg,searxng"
    serper_api_key: str = ""
    tavily_api_key: str = ""
    searxng_url: str = ""
    search_mock_mode: bool = False
    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = ""
    google_login_redirect_uri: str = ""
    google_login_oauth_scopes: str = "openid email profile"
    google_oauth_scopes: str = "https://www.googleapis.com/auth/gmail.send,https://www.googleapis.com/auth/gmail.readonly"
    google_oauth_mock_mode: bool = False
    token_encryption_key: str = ""
    max_daily_outreach: int = 10
    max_auto_send_daily: int = 5
    max_auto_replies_per_day: int = 5
    auto_reply_enabled: bool = False
    http_timeout_seconds: int = 15
    max_page_size_mb: int = 5
    max_company_pages: int = 6
    max_concurrent_fetches: int = 5
    browser_fetch_enabled: bool = False
    max_browser_pages_per_company: int = 2
    browser_timeout_seconds: int = 20
    ai_timeout_seconds: int = 30
    cors_origins: str = "http://localhost:3000,http://localhost:5173"
    max_resume_size_mb: int = 10
    jwt_secret_key: str = ""
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 14
    max_search_queries_per_request: int = 8
    max_search_results_per_query: int = 10
    search_cache_ttl_seconds: int = 60
    rate_limit_window_seconds: int = 60
    rate_limit_default: int = 120
    rate_limit_sensitive: int = 10

    def validate_production(self) -> None:
        if self.app_env.lower() != "production":
            return
        required = {
            "JWT_SECRET_KEY": self.jwt_secret_key,
            "TOKEN_ENCRYPTION_KEY": self.token_encryption_key,
            "DATA_DIR": self.data_dir,
            "GEMINI_API_KEY": self.gemini_api_key,
            "MISTRAL_API_KEY": self.mistral_api_key,
            "GOOGLE_CLIENT_ID": self.google_client_id,
            "GOOGLE_CLIENT_SECRET": self.google_client_secret,
            "GOOGLE_REDIRECT_URI": self.google_redirect_uri,
            "FRONTEND_URL": self.frontend_url,
            "CORS_ORIGINS": self.cors_origins,
        }
        missing = [name for name, value in required.items() if not value]
        if any([self.ai_mock_mode, self.search_mock_mode, self.google_oauth_mock_mode]):
            raise RuntimeError("PRODUCTION_MOCK_MODE_FORBIDDEN")
        if self.jwt_algorithm != "HS256" or len(self.jwt_secret_key) < 32:
            missing.append("STRONG_JWT_SECRET_KEY")
        if not self.cors_origin_list or "*" in self.cors_origin_list:
            missing.append("EXPLICIT_CORS_ORIGINS")
        if missing:
            raise RuntimeError("MISSING_PRODUCTION_CONFIG:" + ",".join(sorted(set(missing))))

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def search_provider_order_list(self) -> list[str]:
        return [provider.strip().lower() for provider in self.search_provider_order.split(",") if provider.strip()]

    @property
    def resolved_database_url(self) -> str:
        return self.database_url or f"sqlite:///{self.data_dir.rstrip('/')}/chably.db"

    @property
    def resolved_chroma_path(self) -> str:
        return self.chroma_path or f"{self.data_dir.rstrip('/')}/chroma"


settings = Settings()
