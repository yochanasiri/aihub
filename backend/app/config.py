import hashlib
import json
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://aihub:change-me@localhost:5432/aihub"
    crawler_config: str = "crawler/seeds.json"
    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: str | None = None
    qdrant_collection: str = "iiti_official"
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    embedding_cache_dir: str = ".cache/fastembed"
    embedding_threads: int = Field(2, ge=1, le=32)
    chunk_tokens: int = Field(700, ge=500, le=800)
    chunk_overlap: int = Field(64, ge=0, le=128)
    search_window_tokens: int = Field(460, ge=64, le=460)
    search_window_overlap: int = Field(32, ge=0, le=63)
    retrieval_top_k: int = Field(2, ge=1, le=10)
    retrieval_min_score: float = Field(0.55, ge=0, le=1)
    context_max_characters: int = Field(24000, ge=2000, le=50000)
    llm_base_url: str = "http://localhost:11434/v1"
    llm_model: str = "qwen2.5:3b-instruct"
    llm_api_key: str = "local"
    llm_max_tokens: int = Field(1600, ge=128, le=4096)
    llm_timeout_seconds: int = Field(180, ge=5, le=300)
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.8-flash"
    gemini_google_search: bool = True
    max_concurrent_chats: int = Field(4, ge=1, le=32)
    metrics_enabled: bool = False
    voice_enabled: bool = False
    voice_whisper_model: str = "base.en"
    voice_model_cache: str = ".cache/voice"
    voice_piper_model: str = ".cache/voice/en_US-lessac-medium.onnx"
    redis_url: str = ""
    answer_cache_enabled: bool = False
    answer_cache_ttl: int = Field(300, ge=30, le=3600)
    auth_enabled: bool = False
    auth_bridge_secret: str = ""
    google_client_id: str = ""
    institute_domain: str = "iiti.ac.in"
    session_hours: int = Field(8, ge=1, le=24)
    demo_login_enabled: bool = False
    smtp_host: str = ""
    smtp_port: int = Field(587, ge=1, le=65535)
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from: str = "AIHUB <noreply@iiti.ac.in>"
    smtp_starttls: bool = True
    smtp_ssl: bool = False
    # RAG activation is independent of authentication. No development-token bypass remains.
    rag_api_enabled: bool = False
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @model_validator(mode="after")
    def validate_rag(self):
        if self.auth_enabled and len(self.auth_bridge_secret) < 32:
            raise ValueError("AUTH_BRIDGE_SECRET must have at least 32 characters")
        if self.institute_domain != "iiti.ac.in":
            raise ValueError("This deployment is restricted to the exact iiti.ac.in domain")
        if self.smtp_ssl and self.smtp_starttls:
            raise ValueError("Use SMTP_SSL or SMTP_STARTTLS, not both")
        if self.embedding_model not in {"BAAI/bge-small-en-v1.5", "BAAI/bge-base-en-v1.5", "BAAI/bge-large-en-v1.5"}:
            raise ValueError("This tokenizer/window adapter supports BGE English v1.5 small/base/large")
        return self

    @property
    def index_signature(self):
        values = {"model": self.embedding_model, "chunk": self.chunk_tokens,
                  "overlap": self.chunk_overlap, "window": self.search_window_tokens,
                  "window_overlap": self.search_window_overlap, "pipeline": "heading-parent-child-v1"}
        return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()[:16]

    @property
    def collection_name(self):
        return f"{self.qdrant_collection}_{self.index_signature}"


settings = Settings()
