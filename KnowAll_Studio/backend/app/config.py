"""KnowAll Studio - Backend Configuration"""
import os
from pathlib import Path
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("KNOWALL_DATA_ROOT", str(BASE_DIR / "data"))).resolve()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "KnowAll Studio"
    app_version: str = "0.1.0"
    debug: bool = True
    # Enabled explicitly by local launchers; remote deployments keep login.
    local_auto_login: bool = False
    # Desktop products require account credentials even with a local backend.
    desktop_require_login: bool = Field(default=False, validation_alias="KNOWALL_DESKTOP_REQUIRE_LOGIN")

    # Local SQLite for the competition demo. DATABASE_URL can override it.
    database_url: str = "sqlite+aiosqlite:///" + (DATA_DIR / "demo.db").as_posix()

    # ChromaDB
    chroma_persist_dir: str = str(DATA_DIR / "vector_db")
    embedding_model: str = "BAAI/bge-small-zh-v1.5"

    # RAG Pipeline Provider ("chromadb" | "graphrag" | "llamaindex")
    rag_provider: str = "chromadb"

    # GraphRAG (hybrid vector + knowledge graph retrieval)
    graphrag_enabled: bool = True
    graphrag_max_hops: int = 2
    graphrag_top_k_vector: int = 8
    graphrag_top_k_graph: int = 20
    graphrag_max_context_chars: int = 8000

    # Deep Research
    research_max_subtopics: int = 8
    research_max_rounds: int = 5
    research_search_provider: str = "duckduckgo"  # duckduckgo / searxng

    # API Scheduler
    default_model: str = "claude-opus-4-6"
    default_temperature: float = 0.7
    default_max_tokens: int = 4096
    cache_ttl_days: int = 30
    max_retries: int = 3
    request_timeout: int = 120
    max_concurrent_requests: int = 5
    daily_token_limit: int = 10_000_000

    # File Storage
    document_dir: str = str(DATA_DIR / "documents")
    export_dir: str = str(DATA_DIR / "exports")
    max_upload_size_mb: int = 100
    chunk_size_tokens: int = 2000  # target tokens per chunk
    chunk_overlap_ratio: float = 0.1

    # KAF 加密资料发布管道
    kaf_publish_enabled: bool = False  # 文档处理完成自动生成 .kaf
    kaf_publish_enabled_tiers: str = "free,pro,enterprise"  # 默认全档位

    # CORS
    cors_origins: list[str] = ["http://localhost:5273", "http://localhost:5274", "http://localhost:3000", "app://.", "http://127.0.0.1:8765", "http://localhost:8765"]

    # JWT Authentication
    jwt_secret: str = ""
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 1440  # 24 hours

    # OAuth2 Third-party Login (leave empty to disable)
    oauth_qq_client_id: str = ""
    oauth_qq_client_secret: str = ""
    oauth_wechat_client_id: str = ""
    oauth_wechat_client_secret: str = ""
    oauth_github_client_id: str = ""
    oauth_github_client_secret: str = ""
    oauth_google_client_id: str = ""
    oauth_google_client_secret: str = ""
    # Override the auto-detected redirect URI base (e.g. "https://yourdomain.com")
    oauth_redirect_base: str = ""

    # SMS Verification (leave empty to disable)
    sms_provider: str = ""  # aliyun / tencent / custom
    sms_access_key: str = ""
    sms_secret_key: str = ""
    sms_sign_name: str = ""  # 短信签名
    sms_template_code: str = ""  # 验证码模板ID
    sms_code_length: int = 6
    sms_code_expire_seconds: int = 300  # 5 minutes
    sms_send_limit_per_hour: int = 5  # max SMS per phone per hour

    # Docling document parsing
    use_docling: bool = True
    docling_ocr: bool = False
    docling_ocr_lang: str = "chi_sim+eng"

    # Hybrid retrieval（移植自 WeKnora：向量 + BM25 双路召回，加权 RRF 融合）
    # 关掉它就回到改动前的纯向量检索 —— 线上排序出问题时的回退开关。
    hybrid_retrieval_enabled: bool = True
    # 候选池 = 目标条数 × 系数。RRF 靠"两路各自的名次"融合，池子太浅名次没有区分度。
    hybrid_pool_factor: int = 4
    hybrid_min_pool: int = 20
    # MMR 去冗余。默认关闭：它会在"相关性"之外引入"多样性"目标，属于额外的排序
    # 改动，要和 RRF 分开评估。想试就打开。
    hybrid_mmr_enabled: bool = False
    hybrid_mmr_lambda: float = 0.7

    # Native outline
    native_outline_enabled: bool = True
    native_outline_max_depth: int = 4

    # Vision analysis (image → text via vision-capable LLM)
    vision_analysis_enabled: bool = False
    vision_model: str = "gpt-4o"
    vision_max_images: int = 8
    vision_min_image_dimension: int = 100


# Cloud workers use process configuration rather than a checkout's development
# credentials. Local development retains its existing .env behavior.
settings = (
    Settings(_env_file=None)
    if os.environ.get("KNOWALL_IGNORE_ENV_FILE", "").strip().lower() in {"true", "1", "yes", "on"}
    else Settings()
)

# Auto-generate a persistent JWT secret if none is configured
if not settings.jwt_secret:
    _secret_path = DATA_DIR / ".jwt_secret"
    try:
        if _secret_path.exists():
            settings.jwt_secret = _secret_path.read_text(encoding="utf-8").strip()
        else:
            import secrets
            _secret = secrets.token_urlsafe(64)
            _secret_path.parent.mkdir(parents=True, exist_ok=True)
            _secret_path.write_text(_secret, encoding="utf-8")
            settings.jwt_secret = _secret
    except Exception:
        import secrets
        settings.jwt_secret = secrets.token_urlsafe(64)  # fallback: in-memory only

# Ensure data directories exist
for d in [settings.document_dir, settings.export_dir, settings.chroma_persist_dir]:
    Path(d).mkdir(parents=True, exist_ok=True)
