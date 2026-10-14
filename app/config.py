from __future__ import annotations
import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()

@dataclass(frozen=True)
class Settings:
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./data/app.db")
    bot_token: str = os.getenv("BOT_TOKEN", "")
    webhook_secret: str = os.getenv("WEBHOOK_SECRET", "")
    admin_token: str = os.getenv("ADMIN_TOKEN", "")
    bot_mode: str = os.getenv("BOT_MODE", "mock")
    llm_provider: str = os.getenv("LLM_PROVIDER", "none")
    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "")
    gemini_model: str = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite")
    crawl_allowed_hosts: str = os.getenv("CRAWL_ALLOWED_HOSTS", "")
    public_base_url: str = os.getenv("PUBLIC_BASE_URL", "")
    rag_backend: str = os.getenv("RAG_BACKEND", "keyword")
    embedding_model: str = os.getenv("GEMINI_EMBEDDING_MODEL", "gemini-embedding-001")
    llm_timeout: int = int(os.getenv("LLM_TIMEOUT_SECONDS", "10"))

    @property
    def allowed_hosts(self) -> set[str]:
        return {v.strip().lower() for v in self.crawl_allowed_hosts.split(",") if v.strip()}

settings = Settings()
