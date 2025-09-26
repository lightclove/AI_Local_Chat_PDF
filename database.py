import uuid
from datetime import datetime

from loguru import logger
from pydantic import BaseModel
from sqlalchemy import (
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, relationship, selectinload, sessionmaker

from config import DatabaseConfig


# Базовый класс для всех моделей SQLAlchemy (определяет общие поля и методы)
class Base(DeclarativeBase):
    pass


class User(Base):
    """Модель пользователя: хранит технический идентификатор и дату создания."""

    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String, unique=True, index=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    chats = relationship("Chat", back_populates="user")


class Chat(Base):
    """Модель чата: заголовок, связь с пользователем и метки времени."""

    __tablename__ = "chats"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String, ForeignKey("users.user_id"), nullable=False)
    chat_id = Column(String, unique=True, index=True, nullable=False)
    title = Column(String, default="New Chat")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = relationship("User", back_populates="chats")
    messages = relationship("Message", back_populates="chat", order_by="Message.timestamp")


class Message(Base):
    """Модель сообщения: роль, текст и отметка времени."""

    __tablename__ = "messages"

    id = Column(Integer, primary_key=True, index=True)
    chat_id = Column(String, ForeignKey("chats.chat_id"), nullable=False)
    role = Column(String, nullable=False)  # "user" or "assistant"
    content = Column(Text, nullable=False)
    timestamp = Column(DateTime, default=datetime.utcnow)
    thinking_time = Column(Float, nullable=True)

    chat = relationship("Chat", back_populates="messages")


class ChatCreate(BaseModel):
    """Схема создания нового чата (опциональный заголовок)."""

    title: str | None = "New Chat"


# Схема ответа о чате для API (включая счётчик сообщений)
class ChatResponse(BaseModel):
    """Схема ответа о чате для API (включая счётчик сообщений)."""

    id: int
    chat_id: str
    title: str
    created_at: datetime
    updated_at: datetime
    message_count: int

    class Config:
        from_attributes = True


class MessageCreate(BaseModel):
    """Схема создания сообщения (роль/контент)."""

    chat_id: str
    role: str
    content: str


class MessageResponse(BaseModel):
    """Схема ответа сообщения для API."""

    id: int
    chat_id: str
    role: str
    content: str
    timestamp: datetime

    class Config:
        from_attributes = True


class DatabaseManager:
    """Менеджер БД: инкапсулирует сессии и транзакционные операции."""

    def __init__(self, config: DatabaseConfig):
        logger.debug("Initializing DatabaseManager")
        engine_kwargs = {
            "echo": config.echo,
            "pool_size": config.pool_size,
            "max_overflow": config.max_overflow,
        }
        self.engine = create_engine(config.url, **engine_kwargs)
        logger.debug("Engine created")
        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        logger.debug("SessionLocal created")

    def create_user(self, user_id: str) -> User:
        logger.debug(f"Creating user {user_id}")
        db = self.SessionLocal()
        logger.debug("Session opened")
        try:
            user = User(user_id=user_id)
            logger.debug("User object created")
            db.add(user)
            logger.debug("User added to session")
            db.commit()
            logger.debug("Session committed")
            db.refresh(user)
            logger.debug("User refreshed")
            return user
        finally:
            db.close()

    #
    def get_user(self, user_id: str) -> User | None:
        """Получаем пользователя по `user_id`."""
        db = self.SessionLocal()
        try:
            return db.query(User).filter(User.user_id == user_id).first()
        finally:
            db.close()

    #
    def create_chat(self, user_id: str, title: str = "New Chat") -> Chat:
        """Создаём новый чат для пользователя с заданным заголовком или "Новый чат" по умолчанию (русский язык)."""
        db = self.SessionLocal()
        try:
            chat_id = str(uuid.uuid4())
            # Если заголовок пустой, используем значение по умолчанию
            if not title or title.strip() == "":
                title = "New Chat"
            chat = Chat(user_id=user_id, chat_id=chat_id, title=title)
            db.add(chat)
            db.commit()
            db.refresh(chat)
            return chat
        finally:
            db.close()

    #
    def get_chat(self, chat_id: str) -> Chat | None:
        """Получаем чат по `chat_id`   и  сообщения"""
        db = self.SessionLocal()
        try:
            return (
                db.query(Chat)
                .options(selectinload(Chat.messages))
                .filter(Chat.chat_id == chat_id)
                .first()
            )
        finally:
            db.close()

    #
    def get_user_chats(self, user_id: str) -> list[Chat]:
        """Получаем все чаты для пользователя (включая сообщения)"""
        db = self.SessionLocal()
        try:
            return (
                db.query(Chat)
                .options(selectinload(Chat.messages))
                .filter(Chat.user_id == user_id)
                .order_by(Chat.updated_at.desc())
                .all()
            )
        finally:
            db.close()

    # Добавляем сообщение в чат и обновляем время последнего обновления чата
    def add_message(
        self, chat_id: str, role: str, content: str, thinking_time: float | None = None
    ) -> Message:
        """Добавляем сообщение в чат и обновляем время последнего обновления чата"""
        db = self.SessionLocal()
        try:
            message = Message(
                chat_id=chat_id, role=role, content=content, thinking_time=thinking_time
            )
            db.add(message)
            # Важно: явно обновляем updated_at у чата при новом сообщении
            chat = db.query(Chat).filter(Chat.chat_id == chat_id).first()
            if chat:
                chat.updated_at = datetime.utcnow()
            db.commit()
            db.refresh(message)
            return message
        finally:
            db.close()

    #
    def update_chat_title(self, chat_id: str, title: str) -> Chat | None:
        """Обновляем заголовок чата и время последнего обновления ."""
        db = self.SessionLocal()
        try:
            chat = db.query(Chat).filter(Chat.chat_id == chat_id).first()
            if not chat:
                return None
            chat.title = title
            chat.updated_at = datetime.utcnow()
            db.commit()
            db.refresh(chat)
            return chat
        finally:
            db.close()

    def delete_chat(self, chat_id: str) -> bool:
        """Удаляем чат по `chat_id` и его сообщения"""
        db = self.SessionLocal()
        try:
            chat = db.query(Chat).filter(Chat.chat_id == chat_id).first()
            if not chat:
                return False
            # delete messages first (no ON DELETE CASCADE configured)
            db.query(Message).filter(Message.chat_id == chat_id).delete()
            db.delete(chat)
            db.commit()
            return True
        finally:
            db.close()

    def cleanup_user_chats(self, user_id: str, keep: int = 2) -> int:
        """Удаляем все, кроме последних `keep` чатов пользователя (и их сообщения)  ."""
        db = self.SessionLocal()
        try:
            chats = (
                db.query(Chat)
                .filter(Chat.user_id == user_id)
                .order_by(Chat.updated_at.desc())
                .all()
            )
            if len(chats) <= keep:
                return 0
            to_delete = chats[keep:]
            chat_ids = [c.chat_id for c in to_delete]

            if chat_ids:
                db.query(Message).filter(Message.chat_id.in_(chat_ids)).delete(
                    synchronize_session=False
                )
                db.query(Chat).filter(Chat.chat_id.in_(chat_ids)).delete(synchronize_session=False)
                db.commit()
            return len(chat_ids)
        finally:
            db.close()
