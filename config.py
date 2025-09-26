import logging
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

logger = logging.getLogger(__name__)


# Здесь определяются основные параметры конфигурации приложения, которые могут быть переопределены через переменные окружения.
def _get_bool(name: str, default: bool) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "y", "on"}


def _get_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except Exception:
        return default


def _get_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except Exception:
        return default


load_dotenv()


@dataclass
class LmStudioConfig:
    """Конфигурация подключения к LM Studio."""

    base_url: str = os.getenv("LMSTUDIO_BASE_URL", "http://localhost:1234/v1")
    model: str = os.getenv("LMSTUDIO_MODEL", "meta-llama-3.1-8b-instruct")
    temperature: float = _get_float("LMSTUDIO_TEMPERATURE", 0.7)
    max_tokens: int = _get_int("LMSTUDIO_MAX_TOKENS", 1000)
    timeout: int = _get_int("LMSTUDIO_TIMEOUT", 600)
    bad_keywords_csv: str = os.getenv("LMSTUDIO_BAD_KEYWORDS", "embed,embedding,rerank")

    def bad_keywords(self) -> list[str]:
        return [k.strip() for k in (self.bad_keywords_csv or "").split(",") if k.strip()]


@dataclass
class RAGConfig:
    """Конфигурация системы RAG (пути, модели, параметры чанкинга и поиска)."""

    chroma_db_path: str = os.getenv("CHROMA_DB_PATH", "./data/chroma_db")
    collection_name: str = os.getenv("RAG_COLLECTION", "documents")
    embedding_model: str = os.getenv("EMBEDDING_MODEL", "all-MiniLM-L6-v2")
    chunk_size: int = _get_int("RAG_CHUNK_SIZE", 1000)
    chunk_overlap: int = _get_int("RAG_CHUNK_OVERLAP", 200)
    max_documents: int = _get_int("RAG_MAX_DOCS", 100)
    similarity_threshold: float = _get_float("RAG_SIMILARITY_THRESHOLD", 0.7)
    search_results: int = _get_int("RAG_SEARCH_RESULTS", 3)
    ocr_dpi: int = _get_int("RAG_OCR_DPI", 200)
    max_upload_mb: int = _get_int("UPLOAD_MAX_MB", 20)


@dataclass
class DatabaseConfig:
    """Конфигурация базы данных (URL, пул, отладочные флаги)."""

    url: str = os.getenv("DATABASE_URL", "sqlite:///./data/chatbot.db")
    echo: bool = _get_bool("DATABASE_ECHO", False)
    pool_size: int = _get_int("DB_POOL_SIZE", 5)
    max_overflow: int = _get_int("DB_MAX_OVERFLOW", 10)


@dataclass
class LoggingConfig:
    """Конфигурация логирования (уровни, формат, ротация/retention и пр.)."""

    level: str = os.getenv("LOG_LEVEL", "INFO")
    format: str = os.getenv("LOG_FORMAT", "%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    disable_existing_loggers: bool = _get_bool("LOG_DISABLE_EXISTING", False)
    backend_rotation: str = os.getenv("LOG_BACKEND_ROTATION", "20 MB")
    backend_retention: str = os.getenv("LOG_BACKEND_RETENTION", "30 days")
    json_rotation: str = os.getenv("LOG_JSON_ROTATION", "00:00")
    json_retention: str = os.getenv("LOG_JSON_RETENTION", "14 days")
    frontend_rotation: str = os.getenv("LOG_FRONTEND_ROTATION", "10 MB")
    frontend_retention: str = os.getenv("LOG_FRONTEND_RETENTION", "30 days")
    errors_rotation: str = os.getenv("LOG_ERRORS_ROTATION", "00:00")
    errors_retention: str = os.getenv("LOG_ERRORS_RETENTION", "60 days")
    slow_rotation: str = os.getenv("LOG_SLOW_ROTATION", "20 MB")
    slow_retention: str = os.getenv("LOG_SLOW_RETENTION", "30 days")
    slow_request_ms: int = _get_int("SLOW_REQUEST_MS", 2000)
    frontend_entry_cap: int = _get_int("FRONTEND_LOG_ENTRY_CAP", 100)
    frontend_message_max_len: int = _get_int("FRONTEND_LOG_MESSAGE_MAX", 2000)

    def get_uvicorn_log_config(self) -> dict:
        """Вернуть словарь конфигурации логирования для Uvicorn."""
        return {
            "version": 1,
            "disable_existing_loggers": self.disable_existing_loggers,
            "formatters": {
                "default": {
                    "format": self.format,
                },
            },
            "handlers": {
                "default": {
                    "formatter": "default",
                    "class": "logging.StreamHandler",
                    "stream": "ext://sys.stdout",
                },
            },
            "root": {
                "level": self.level,
                "handlers": ["default"],
            },
            "loggers": {
                "uvicorn": {"level": self.level, "handlers": ["default"], "propagate": False},
                "uvicorn.error": {"level": self.level, "handlers": ["default"], "propagate": False},
                "uvicorn.access": {
                    "level": self.level,
                    "handlers": ["default"],
                    "propagate": False,
                },
            },
        }


@dataclass
class ServerConfig:
    """Конфигурация веб-сервера (хост, порт, режимы отладки/перезагрузки)."""

    host: str = os.getenv("SERVER_HOST", "127.0.0.1")
    port: int = _get_int("SERVER_PORT", 8001)
    debug: bool = _get_bool("SERVER_DEBUG", False)
    reload: bool = _get_bool("SERVER_RELOAD", False)  # Disabled by default on Windows


@dataclass
class ChatConfig:
    """Конфигурация поведения чата (ограничения истории и автозаголовки)."""

    history_max_messages: int = _get_int("CHAT_HISTORY_MAX", 10)
    keep_recent_chats: int = _get_int("CHAT_KEEP_RECENT", 2)
    title_preview_chars: int = _get_int("CHAT_TITLE_PREVIEW_CHARS", 40)
    title_snippet_chars: int = _get_int("CHAT_TITLE_SNIPPET_CHARS", 60)


@dataclass
class AppConfig:
    """Главная конфигурация приложения: агрегирует все секции."""

    lm_studio: LmStudioConfig
    rag: RAGConfig
    database: DatabaseConfig
    server: ServerConfig
    logging: LoggingConfig
    chat: ChatConfig
    data_dir: str = "./data"
    version: str = os.getenv("APP_VERSION", "1.0.0")

    @classmethod
    def create_default(cls) -> "AppConfig":
        logger.debug("Creating default AppConfig")
        config = cls(
            lm_studio=LmStudioConfig(),
            rag=RAGConfig(),
            database=DatabaseConfig(),
            server=ServerConfig(),
            logging=LoggingConfig(),
            chat=ChatConfig(),
        )
        logger.debug("Default config instance created")
        return config

    def ensure_directories(self) -> None:
        logger.debug("Ensuring directories")
        Path(self.data_dir).mkdir(parents=True, exist_ok=True)
        logger.debug(f"Data dir ensured: {self.data_dir}")
        Path(self.rag.chroma_db_path).mkdir(parents=True, exist_ok=True)
        logger.debug(f"Chroma DB path ensured: {self.rag.chroma_db_path}")
