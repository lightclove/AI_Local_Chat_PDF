import asyncio
import contextvars
import json
import os
import sys
import tempfile
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import TypedDict, cast

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger
from pydantic import BaseModel

from config import AppConfig
from database import (
    ChatCreate,
    ChatResponse,
    DatabaseManager,
    MessageResponse,
)
from lmstudio_client import ChatHistoryManager, LMStudioClient


# Lifespan вместо on_event("startup")
@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        healthy = await lm_client.health_check()
        if healthy:
            logger.info("LM Studio health check: OK")
        else:
            logger.warning("LM Studio health check: FAILED")
    except Exception as e:
        logger.opt(exception=e).error(f"LM Studio health check error: {e}")

    # Глобальные хуки необработанных исключений
    def handle_exception(exc_type, exc, tb):
        logger.opt(exception=(exc_type, exc, tb)).error("Unhandled exception: {}", exc)

    sys.excepthook = handle_exception

    # Обработчик необработанных asyncio исключений
    def handle_asyncio_exception(loop, context):
        exception = context.get("exception")
        if exception:
            logger.opt(exception=exception).error("Unhandled asyncio exception: {}", exception)
        else:
            logger.error("Unhandled asyncio exception: {}", context)

    # Устанавливаем обработчик asyncio исключений
    try:
        asyncio.get_running_loop().set_exception_handler(handle_asyncio_exception)
    except RuntimeError:
        # Если нет запущенного цикла, создаем новый
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.set_exception_handler(handle_asyncio_exception)

    yield
    # Здесь можно добавить логику остановки при завершении приложения


# Инициализация приложения
app = FastAPI(
    title="ChatBot with LM Studio",
    description="Чат-бот с интеграцией LM Studio и возможностями RAG",
    lifespan=lifespan,
)
logger.debug("FastAPI app initialized")

# Добавляем CORS middleware (для обращений со статического фронтенда)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
logger.debug("CORS middleware added")

# Инициализируем основные компоненты
config = AppConfig.create_default()
logger.debug("Default config created")
config.ensure_directories()  # Ensure required directories exist
logger.debug("Directories ensured")

# Настраиваем логирование в файлы (бэкенд и отдельный канал фронтенда)
logs_dir = Path(config.data_dir) / "logs"
logs_dir.mkdir(parents=True, exist_ok=True)

"""Расширенная конфигурация Loguru: ротация, retention, JSON-sink и консольный вывод."""
# Сбрасываем все дефолтные синки и добавляем файловые и консольные синки

logger.remove()

# Убеждаемся, что дефолтные поля extra существуют, чтобы избежать KeyError в форматах
logger.configure(extra={"request_id": None, "user_id": None, "source": "backend"})

console_format = (
    "{time:YYYY-MM-DD HH:mm:ss.SSS} | "
    "{level:<8} | "
    "{name}:{function}:{line} | "
    "rid={extra[request_id]} uid={extra[user_id]} src={extra[source]} | "
    "{message}"
)

# Консольный вывод (с цветами)
logger.add(
    sys.stdout,
    level=config.logging.level,
    enqueue=True,
    colorize=True,
    backtrace=True,
    diagnose=True,
    format=console_format,
)

# Текстовый лог бэкенда (ротация по размеру)
logger.add(
    str(logs_dir / "backend.log"),
    rotation=config.logging.backend_rotation,
    retention=config.logging.backend_retention,
    compression="zip",
    level="DEBUG",
    enqueue=True,
    backtrace=True,
    diagnose=True,
    format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level:<8} | {message} | {name}:{function}:{line} | rid={extra[request_id]} uid={extra[user_id]} src={extra[source]}",
)

# JSON-лог бэкенда (ежедневная ротация)
logger.add(
    str(logs_dir / "backend.jsonl"),
    rotation=config.logging.json_rotation,
    retention=config.logging.json_retention,
    compression="zip",
    level="DEBUG",
    enqueue=True,
    serialize=True,
)

# Отдельный приёмник для логов, присланных с фронтенда (фильтр по extra.source)
logger.add(
    str(logs_dir / "frontend.log"),
    rotation=config.logging.frontend_rotation,
    retention=config.logging.frontend_retention,
    compression="zip",
    level="DEBUG",
    enqueue=True,
    filter=lambda record: record["extra"].get("source") == "frontend",
    format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level:<8} | {message} | uid={extra[user_id]} src={extra[source]}",
)

# Отдельный приёмник для ошибок (уровень ERROR и выше)
logger.add(
    str(logs_dir / "errors.log"),
    rotation=config.logging.errors_rotation,  # daily rotation for errors
    retention=config.logging.errors_retention,
    compression="zip",
    level="DEBUG",  # Изменяем на DEBUG, чтобы захватывать все ошибки с traceback
    enqueue=True,
    backtrace=True,
    diagnose=True,
    format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level:<8} | {message} | {name}:{function}:{line} | rid={extra[request_id]} uid={extra[user_id]} src={extra[source]} | TRACEBACK: {exception}",
)

# Приёмник «медленных» запросов (пометка через extra.slow=True)
logger.add(
    str(logs_dir / "slow_requests.log"),
    rotation=config.logging.slow_rotation,
    retention=config.logging.slow_retention,
    compression="zip",
    level="WARNING",
    enqueue=True,
    filter=lambda record: record["extra"].get("slow") is True,
)

# Передача контекста через contextvars (request_id, user_id, source)
request_id_ctx: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "request_id", default=None
)
user_id_ctx: contextvars.ContextVar[str | None] = contextvars.ContextVar("user_id", default=None)
source_ctx: contextvars.ContextVar[str] = contextvars.ContextVar("source", default="backend")


# Расширение записей лога: добавляем request_id, user_id, source
def _patch_record(record):
    try:
        record["extra"].setdefault("request_id", request_id_ctx.get())
        record["extra"].setdefault("user_id", user_id_ctx.get())
        record["extra"].setdefault("source", source_ctx.get())
    except Exception as err:
        logger.opt(exception=err).warning("Failed to patch log record extras")
    return record


# Расширение записей лога
logger = logger.patch(_patch_record)
# Инициализация компонентов
db_manager = DatabaseManager(config.database)
logger.debug("DatabaseManager initialized")
lm_client = LMStudioClient(config.lm_studio)
logger.debug("LMStudioClient initialized")
chat_history_manager = ChatHistoryManager(max_history=config.chat.history_max_messages)
logger.debug("ChatHistoryManager initialized")

logger.info("Application initialized successfully")
logger.debug("Logged application initialization")

# Статические файлы (отдаём /static)
static_dir = Path(__file__).parent / "static"
static_dir.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

# HTML-шаблоны: сервер отдаёт готовый index.html из static


# Типизация словаря чата
class ChatDict(TypedDict):
    id: int
    chat_id: str
    title: str
    created_at: datetime
    updated_at: datetime
    message_count: int


# Отдаём основную HTML-страницу из папки static
@app.get("/", response_class=HTMLResponse)
async def read_root():
    """Отдать основную HTML-страницу из папки static."""
    return FileResponse("static/index.html")


# Отдаём страницу просмотра логов
@app.get("/logs", response_class=HTMLResponse)
async def read_logs():
    """Отдать HTML-страницу просмотра лог файлов."""
    return FileResponse("static/logs.html")


# Middleware для обработки исключений на уровне приложения
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Глобальный обработчик всех необработанных исключений."""
    logger.opt(exception=exc).error(
        "Global exception handler caught: {} for request {}", str(exc), request.url.path
    )
    # Возвращаем JSON ответ с ошибкой
    from fastapi.responses import JSONResponse

    return JSONResponse(
        status_code=500, content={"detail": "Internal server error", "path": request.url.path}
    )


# Логирование запросов/ответов с таймингом и уникальным request_id
@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Логировать каждый запрос/ответ с таймингом и уникальным request_id."""
    from time import perf_counter

    # Начинаем отсчёт времени
    start_time = perf_counter()
    request_id = str(uuid.uuid4())
    request.state.request_id = request_id
    request_id_ctx.set(request_id)
    source_ctx.set("backend")
    try:
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        duration_ms = (perf_counter() - start_time) * 1000
        bound = logger.bind(
            source="backend",
            request_id=request_id,
            client_ip=(request.client.host if request.client else None),
            user_agent=request.headers.get("user-agent"),
            http_method=request.method,
            http_path=request.url.path,
        )
        # Если запрос дольше порога — помечаем как «медленный»
        if duration_ms > config.logging.slow_request_ms:
            bound.bind(slow=True).warning(
                "SLOW {method} {path} -> {status} in {duration:.2f} ms",
                method=request.method,
                path=request.url.path,
                status=response.status_code,
                duration=duration_ms,
            )
        # Логируем запрос/ответ
        bound.info(
            "HTTP {method} {path} -> {status} in {duration:.2f} ms",
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            duration=duration_ms,
        )
        return response
    except Exception as exc:
        duration_ms = (perf_counter() - start_time) * 1000
        # Создаем bound logger с контекстом
        bound_logger = logger.bind(
            source="backend",
            request_id=request_id,
            client_ip=(request.client.host if request.client else None),
            user_agent=request.headers.get("user-agent"),
            http_method=request.method,
            http_path=request.url.path,
        )
        # Логируем исключение с полным traceback
        bound_logger.opt(exception=exc).error(
            "HTTP {method} {path} raised in {duration:.2f} ms: {error}",
            method=request.method,
            path=request.url.path,
            duration=duration_ms,
            error=str(exc),
        )
        # Также логируем в консоль для отладки
        bound_logger.exception(
            "HTTP {method} {path} raised in {duration:.2f} ms: {error}",
            method=request.method,
            path=request.url.path,
            duration=duration_ms,
            error=str(exc),
        )
        raise


# Типизация записи лога из фронтенда
class FrontendLogEntry(BaseModel):
    level: str
    message: str
    timestamp: str | None = None
    context: dict | None = None
    stack: str | None = None
    user_id: str | None = None


# Типизация запроса на выбор модели
class SetModelRequest(BaseModel):
    model_id: str


# Типизация запроса на обновление заголовка чата
class UpdateTitleRequest(BaseModel):
    title: str


# Логирование запросов/ответов с фронтенда
@app.post("/api/logs")
async def ingest_frontend_logs(entries: list[FrontendLogEntry], request: Request):
    """Ingest logs from the frontend and write them to a separate log file."""
    try:
        capped = entries[: config.logging.frontend_entry_cap]
        for entry in capped:
            level = (entry.level or "INFO").upper()
            bound = logger.bind(
                source="frontend",
                user_id=entry.user_id,
                client_ip=(request.client.host if request.client else None),
                user_agent=request.headers.get("user-agent"),
            )
            msg = (entry.message or "").strip()
            max_len = config.logging.frontend_message_max_len
            if len(msg) > max_len:
                msg = msg[:max_len] + "..."
            ctx = entry.context or {}
            if entry.timestamp:
                ctx["ts"] = entry.timestamp
            if entry.stack:
                ctx["stack"] = entry.stack
            # Используем logger.log для поддержки динамических уровней
            bound.log(level, msg + (" | {context}" if ctx else ""), context=ctx)
        return {"status": "ok", "count": len(capped)}
    except Exception as e:
        logger.opt(exception=e).error(f"Error ingesting frontend logs: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e)) from e


# Получаем список моделей
@app.get("/api/models")
async def get_models():
    try:
        models = await lm_client.list_models()
        return {"models": models}
    except Exception as e:
        logger.opt(exception=e).error(f"Error fetching models: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e


# Выбираем модель из списка
@app.post("/api/models/select")
async def select_model(req: SetModelRequest):
    try:
        # Не позволяем выбрать модель-эмбеддер/нерелевантные ID
        bad_keywords = config.lm_studio.bad_keywords()
        if any(k and (k in req.model_id.lower()) for k in bad_keywords):
            raise HTTPException(status_code=400, detail="Selected model is not a chat LLM")
        lm_client.set_model(req.model_id)
        return {"status": "ok", "model": req.model_id}
    except Exception as e:
        logger.opt(exception=e).error(f"Error setting model: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e


# Проверка состояния подсистем
@app.get("/health")
async def health() -> dict[str, object]:
    """Сводное состояние подсистем (БД, LM Studio, RAG)."""
    # Проверка БД (пробуем простую операцию)
    db_ok = False
    try:
        _ = db_manager.get_user("__health__")
        db_ok = True
    except Exception:
        db_ok = False

    # Доступность LM Studio
    lm_ok = await lm_client.health_check()

    return {
        "status": "ok" if (db_ok and lm_ok) else "degraded",
        "db": db_ok,
        "lm_studio": lm_ok,
        "version": config.version,
    }


# Создаём новый чат для пользователя с заданным заголовком или "Новый чат" по умолчанию (русский язык)
@app.post("/api/users/{user_id}/chats")
async def create_chat(user_id: str, chat: ChatCreate):
    """Создать новый чат для пользователя (автосоздание пользователя при отсутствии)."""
    logger.debug(f"Creating chat for user {user_id} with title {chat.title}")
    try:
        user = db_manager.get_user(user_id)
        logger.debug(f"Retrieved user: {user}")
        if not user:
            user = db_manager.create_user(user_id)
            logger.debug(f"Created new user: {user}")

        chat_obj = db_manager.create_chat(user_id, chat.title or "Новый чат")
        logger.debug(f"Created chat: {chat_obj}")
        chat_dict = ChatDict(
            id=cast(int, chat_obj.id),
            chat_id=cast(str, chat_obj.chat_id),
            title=cast(str, chat_obj.title),
            created_at=cast(datetime, chat_obj.created_at),
            updated_at=cast(datetime, chat_obj.updated_at),
            message_count=0,
        )
        return ChatResponse(**chat_dict)
        logger.debug("Returned ChatResponse")
    except Exception as e:
        logger.opt(exception=e).error(f"Error creating chat: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e)) from e


# Получаем все чаты пользователя (включая количество сообщений)
@app.get("/api/users/{user_id}/chats")
async def get_user_chats(user_id: str):
    """Получить все чаты пользователя (включая количество сообщений)."""
    try:
        chats = db_manager.get_user_chats(user_id)
        result = []
        for chat in chats:
            try:
                result.append(
                    ChatResponse(
                        **ChatDict(
                            id=cast(int, chat.id),
                            chat_id=cast(str, chat.chat_id),
                            title=cast(str, chat.title),
                            created_at=cast(datetime, chat.created_at),
                            updated_at=cast(datetime, chat.updated_at),
                            message_count=len(chat.messages or []),
                        )
                    )
                )
            except Exception as inner:
                logger.opt(exception=inner).error(
                    f"Error formatting chat {getattr(chat, 'chat_id', '?')}: {inner}"
                )
        return result
    except Exception as e:
        logger.exception(f"get_user_chats failed for user {user_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e


# Удаляем все, кроме последних `keep` чатов пользователя (и их сообщения)
@app.delete("/api/users/{user_id}/chats/cleanup")
async def cleanup_user_chats(user_id: str, keep: int = 2):
    """Удалить все, кроме последних `keep` чатов пользователя (и их сообщения)."""
    try:
        deleted = db_manager.cleanup_user_chats(
            user_id, max(0, keep if keep is not None else config.chat.keep_recent_chats)
        )
        logger.info(f"Cleaned up {deleted} chats for user {user_id}, kept {keep}")
        return {"deleted": deleted, "kept": keep}
    except Exception as e:
        logger.exception(f"cleanup_user_chats failed for user {user_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e


# Получаем все сообщения в чате по `chat_id` (отсортированы по времени)
@app.get("/api/chats/{chat_id}/messages")
async def get_chat_messages(chat_id: str):
    """Получить все сообщения в чате по `chat_id` (отсортированы по времени)."""
    try:
        chat = db_manager.get_chat(chat_id)
        if not chat:
            raise HTTPException(status_code=404, detail="Chat not found")
        # Сортируем сообщения по времени
        ordered = sorted(chat.messages, key=lambda m: m.timestamp or datetime.utcnow())
        return [
            MessageResponse(
                id=msg.id,
                chat_id=msg.chat_id,
                role=msg.role,
                content=msg.content,
                timestamp=msg.timestamp,
            )
            for msg in ordered
        ]
    except Exception as e:
        logger.exception(f"get_chat_messages failed for chat {chat_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e


# Типизация запроса на генерацию ответа ИИ с учётом RAG-контекста
class AskRequest(BaseModel):
    chat_id: str
    message: str
    user_id: str


# Генерация ответа ИИ с учётом RAG-контекста и сохранения истории переписки
@app.post("/api/chat/ask")
async def ask_question(request: AskRequest):  # noqa: C901
    """Задать вопрос и получить ответ ИИ с учётом RAG-контекста."""
    try:
        # Use model fields instead of dict.get()
        chat_id = request.chat_id
        message = request.message
        user_id = request.user_id

        if not chat_id or not message or not user_id:
            raise HTTPException(status_code=400, detail="Missing required fields")

        # Get chat and messages
        chat = db_manager.get_chat(chat_id)
        if not chat:
            raise HTTPException(status_code=404, detail="Chat not found")
        # Enforce that user owns the chat
        if getattr(chat, "user_id", None) != user_id:
            raise HTTPException(status_code=403, detail="Forbidden: chat ownership mismatch")

        # Получаем историю переписки (для контекста)
        messages = sorted(chat.messages, key=lambda m: m.timestamp or datetime.utcnow())
        api_messages = [{"role": msg.role, "content": msg.content} for msg in messages]

        # Добавляем текущее пользовательское сообщение в список
        api_messages = chat_history_manager.add_user_message(api_messages, message)

        # Create simple system prompt without RAG
        system_prompt = (
            "You are a helpful AI assistant. Answer the user's question based on your knowledge."
        )
        api_messages.insert(0, {"role": "system", "content": system_prompt})

        # Сохраняем сообщение пользователя до генерации (чтобы история не потерялась при сбое)
        db_manager.add_message(chat_id, "user", message)

        # Измеряем время мыслительного процесса ИИ
        import time

        start_time = time.time()
        response = await lm_client.chat_completion(api_messages)
        thinking_time = int(time.time() - start_time)

        try:
            # Get response from LM Studio
            # Получаем ответ от ИИ из LM Studio
            assistant_response = (
                response.get("choices", [{}])[0].get("message", {}).get("content", "")
            )
            if not assistant_response.strip():
                assistant_response = "Sorry, I couldn't generate a response. Please try again."
        except asyncio.TimeoutError:
            error_detail = "Request to AI model timed out after waiting too long."
            logger.error(f"Error generating response: {error_detail}")
            assistant_response = f"Error: Failed to generate response. Details: {error_detail}"
        except Exception as e:
            error_detail = str(e) or "An unexpected error occurred during response generation."
            logger.opt(exception=e).error(f"Error generating response: {error_detail}")
            assistant_response = f"Error: Failed to generate response. Details: {error_detail}"

        # Сохраняем ответ ассистента (или сообщение об ошибке)
        db_manager.add_message(chat_id, "assistant", assistant_response)

        # Если заголовок ещё дефолтный — сформировать превью по первым символам первого сообщения
        try:
            if (chat.title or "").strip().lower() == "новый чат" and message:
                max_chars = config.chat.title_preview_chars
                preview = message.strip()[:max_chars] + (
                    "…" if len(message.strip()) > max_chars else ""
                )
                db_manager.update_chat_title(chat_id, preview)
        except Exception as err:
            logger.opt(exception=err).warning(f"Auto-title preview update failed: {err}")

        # Ещё одна попытка авто-заголовка: короткий сниппет первой строки
        try:
            chat = db_manager.get_chat(chat_id)
            if chat and (chat.title or "").strip().lower() in ("новый чат", "новый чат"):
                max_snippet = config.chat.title_snippet_chars
                snippet = (message or "").strip().split("\n")[0][:max_snippet]
                if snippet:
                    db_manager.update_chat_title(chat_id, snippet)
        except Exception as err:
            logger.opt(exception=err).warning(f"Auto-title snippet update failed: {err}")

        return {"response": assistant_response, "thinking_time": thinking_time}
    except Exception as e:
        logger.opt(exception=e).error(f"Error in ask_question: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e)) from e


# Обновляем заголовок чата, время последнего обновления
@app.put("/api/chats/{chat_id}/title")
async def update_chat_title(chat_id: str, req: UpdateTitleRequest):
    try:
        chat = db_manager.get_chat(chat_id)
        if not chat:
            raise HTTPException(status_code=404, detail="Chat not found")
        updated = db_manager.update_chat_title(chat_id, req.title or "")
        if not updated:
            raise HTTPException(status_code=500, detail="Failed to update title")
        return {"status": "ok"}
    except HTTPException:
        raise
    except Exception as e:
        logger.opt(exception=e).error(f"update_chat_title failed: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e


# Удаляем чат по `chat_id`
@app.delete("/api/chats/{chat_id}")
async def delete_chat(chat_id: str):
    try:
        ok = db_manager.delete_chat(chat_id)
        if not ok:
            raise HTTPException(status_code=404, detail="Chat not found")
        return {"status": "deleted"}
    except HTTPException:
        raise
    except Exception as e:
        logger.opt(exception=e).error(f"delete_chat failed: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e


# Загружаем PDF-документ (базовая версия без RAG)
@app.post("/api/documents/upload")
async def upload_document(file: UploadFile = File(...)):  # noqa: B008
    """Upload a PDF document (basic version without RAG)."""
    try:
        MAX_BYTES = 10 * 1024 * 1024  # 10MB limit
        if not file.filename or not file.filename.lower().endswith(".pdf"):
            raise HTTPException(status_code=400, detail="Only PDF files are allowed")
        content_type = getattr(file, "content_type", "") or ""
        if content_type and ("pdf" not in content_type):
            raise HTTPException(status_code=400, detail="Invalid content type for PDF")

        # Save uploaded file temporarily
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_file:
            content = await file.read()
            if len(content) > MAX_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail="File too large (max 10MB)",
                )
            tmp_file.write(content)
            tmp_file_path = tmp_file.name

        # Basic PDF processing (just log the upload)
        logger.info(f"PDF {file.filename} uploaded successfully. Size: {len(content)} bytes")

        # Clean up temporary file
        os.unlink(tmp_file_path)

        return {"message": f"Document {file.filename} uploaded successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


# Получаем список документов (заглушка)
@app.get("/api/documents")
async def list_documents():
    """List all documents (placeholder)."""
    return {"documents": [], "message": "RAG system disabled"}


# Удаляем документ (заглушка)
@app.delete("/api/documents/{document_id}")
async def delete_document(document_id: str):
    """Delete a document (placeholder)."""
    return {"message": f"Document {document_id} deleted successfully (placeholder)"}


# Получаем список доступных лог файлов
@app.get("/api/logs/files")
async def list_log_files():
    """Get list of available log files."""
    try:
        logs_dir = Path(config.data_dir) / "logs"
        log_files = []

        if logs_dir.exists():
            for log_file in logs_dir.iterdir():
                if log_file.is_file() and log_file.suffix in [".log", ".jsonl"]:
                    stat = log_file.stat()
                    log_files.append(
                        {
                            "filename": log_file.name,
                            "size": stat.st_size,
                            "modified": stat.st_mtime,
                            "path": str(log_file),
                        }
                    )

        # Сортируем по времени изменения (новые первыми)
        log_files.sort(key=lambda x: x["modified"], reverse=True)

        return {"logs": log_files}
    except Exception as e:
        logger.opt(exception=e).error(f"Error listing log files: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e


def _validate_log_file(log_file: Path, logs_dir: Path) -> None:
    """Validate log file path and permissions."""
    if not log_file.exists() or not log_file.is_file():
        raise HTTPException(status_code=404, detail="Log file not found")

    if log_file.parent != logs_dir:
        raise HTTPException(status_code=403, detail="Access denied")

    if log_file.suffix not in [".log", ".jsonl"]:
        raise HTTPException(status_code=400, detail="Invalid log file type")


def _filter_lines_by_level(lines: list[str], level: str | None) -> list[str]:
    """Filter lines by log level."""
    if not level:
        return lines

    level = level.upper()
    if level not in ["DEBUG", "INFO", "WARNING", "ERROR"]:
        return lines

    return [line for line in lines if f" | {level:<8} | " in line or f" | {level} | " in line]


def _filter_jsonl_by_time(lines: list[str], since: str | None) -> list[str]:
    """Filter JSONL lines by timestamp."""
    if not since:
        return lines

    filtered_lines = []
    for line in lines:
        try:
            log_entry = json.loads(line.strip())
            if log_entry.get("timestamp", "") >= since:
                filtered_lines.append(line)
        except json.JSONDecodeError:
            continue
    return filtered_lines


# Получаем содержимое лог файла
@app.get("/api/logs/{filename}")
async def get_log_content(  # noqa: C901
    filename: str, lines: int = 100, level: str | None = None, since: str | None = None
):
    """Get content of a specific log file with filtering options."""  # noqa: C901
    try:
        logs_dir = Path(config.data_dir) / "logs"
        log_file = logs_dir / filename

        # Проверяем безопасность - только файлы из папки logs
        if not log_file.exists() or not log_file.is_file():
            raise HTTPException(status_code=404, detail="Log file not found")

        if log_file.parent != logs_dir:
            raise HTTPException(status_code=403, detail="Access denied")

        # Проверяем расширение файла
        if log_file.suffix not in [".log", ".jsonl"]:
            raise HTTPException(status_code=400, detail="Invalid log file type")

        # Читаем файл
        with open(log_file, encoding="utf-8", errors="replace") as f:
            content_lines = f.readlines()

        # Применяем фильтры
        filtered_lines = content_lines

        # Фильтр по уровню логирования
        if level:
            level = level.upper()
            if level in ["DEBUG", "INFO", "WARNING", "ERROR"]:
                filtered_lines = [
                    line
                    for line in filtered_lines
                    if f" | {level:<8} | " in line or f" | {level} | " in line
                ]

        # Фильтр по времени (для JSONL файлов)
        if since and log_file.suffix == ".jsonl":
            filtered_lines = []
            for line in content_lines:
                try:
                    log_entry = json.loads(line.strip())
                    if log_entry.get("timestamp", "") >= since:
                        filtered_lines.append(line)
                except json.JSONDecodeError:
                    continue

        # Ограничиваем количество строк (последние N строк)
        if lines > 0:
            filtered_lines = filtered_lines[-lines:]

        # Получаем статистику файла
        stat = log_file.stat()
        total_lines = len(content_lines)
        returned_lines = len(filtered_lines)

        return {
            "filename": filename,
            "total_lines": total_lines,
            "returned_lines": returned_lines,
            "size": stat.st_size,
            "modified": stat.st_mtime,
            "level": level,
            "lines": lines,
            "since": since,
            "content": "".join(filtered_lines),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.opt(exception=e).error(f"Error reading log file {filename}: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e


# Тестовый endpoint для проверки логирования исключений
@app.get("/api/test/error")
async def test_error_logging():
    """Тестовый endpoint для проверки логирования исключений с traceback."""
    try:
        # Генерируем искусственную ошибку для тестирования
        raise ValueError("This is a test exception for logging verification")
    except Exception as e:
        logger.opt(exception=e).error("Test exception logged with full traceback")
        raise HTTPException(status_code=500, detail=f"Test error: {str(e)}") from e


# Функция для тестирования логирования
def test_logging():
    """Тестируем различные типы логирования."""
    logger.info("=== Тестирование улучшенного логирования ===")

    # Тест обычного сообщения
    logger.info("Тест обычного информационного сообщения")

    # Тест предупреждения
    logger.warning("Тест предупреждения")

    # Тест ошибки
    logger.error("Тест ошибки")

    # Тест исключения
    try:
        raise ValueError("Тестовое исключение для проверки traceback")
    except Exception as e:
        logger.opt(exception=e).error("Тест исключения с traceback")

    # Тест отладочного сообщения
    logger.debug("Тест отладочного сообщения")

    logger.info("=== Тестирование завершено ===")


# Запускаем сервер
if __name__ == "__main__":
    import uvicorn

    # Тестируем логирование перед запуском сервера
    test_logging()

    # Получаем конфигурацию логирования из config.py
    log_config = config.logging.get_uvicorn_log_config()

    # Force disable reload to avoid Windows issues
    uvicorn.run(
        app,
        host=config.server.host,
        port=config.server.port,
        reload=False,
        log_config=log_config,
    )
