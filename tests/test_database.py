"""
Комплексные тесты DatabaseManager и основных операций с базой данных.

Этот модуль содержит полные интеграционные тесты для проверки:
- Создания и получения пользователей
- Управления чатами и сообщениями
- Целостности данных и связей
- Производительности и корректности операций
- Обработки ошибок и edge-кейсов
"""

# Добавляем корневую директорию в путь для импортов
import sys
import uuid
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import DatabaseConfig
from database import DatabaseManager


class TestDatabaseManager:
    """Тесты DatabaseManager - основного интерфейса к базе данных."""

    @pytest.fixture
    def db_manager(self):
        """Фикстура для создания DatabaseManager."""
        config = DatabaseConfig()
        return DatabaseManager(config)

    @pytest.fixture
    def sample_user_id(self):
        """Генерация уникального ID пользователя для тестов."""
        return f"test_user_{uuid.uuid4().hex[:8]}"

    @pytest.fixture
    def sample_chat_title(self):
        """Генерация уникального заголовка чата."""
        return f"Test Chat {uuid.uuid4().hex[:8]}"

    def test_create_user_success(self, db_manager, sample_user_id):
        """Тест успешного создания пользователя."""
        # Given
        user_id = sample_user_id

        # When
        user = db_manager.create_user(user_id)

        # Then
        assert user is not None
        assert user.user_id == user_id
        assert user.id is not None
        assert isinstance(user.created_at, datetime)

    def test_create_user_duplicate(self, db_manager, sample_user_id):
        """Тест создания пользователя с существующим user_id."""
        # Given
        user_id = f"{sample_user_id}_duplicate"  # Уникальный ID для этого теста
        db_manager.create_user(user_id)  # Создаем первого пользователя

        # When/Then
        # Попытка создать дубликат должна вызвать IntegrityError
        import pytest
        from sqlalchemy.exc import IntegrityError

        with pytest.raises(IntegrityError):
            db_manager.create_user(user_id)

    def test_get_user_exists(self, db_manager, sample_user_id):
        """Тест получения существующего пользователя."""
        # Given
        user_id = sample_user_id
        created_user = db_manager.create_user(user_id)

        # When
        retrieved_user = db_manager.get_user(user_id)

        # Then
        assert retrieved_user is not None
        assert retrieved_user.user_id == user_id
        assert retrieved_user.id == created_user.id

    def test_get_user_not_exists(self, db_manager):
        """Тест получения несуществующего пользователя."""
        # When
        user = db_manager.get_user("nonexistent_user")

        # Then
        assert user is None

    def test_create_chat_success(self, db_manager, sample_user_id, sample_chat_title):
        """Тест успешного создания чата."""
        # Given
        user_id = sample_user_id
        chat_title = sample_chat_title
        db_manager.create_user(user_id)

        # When
        chat = db_manager.create_chat(user_id, chat_title)

        # Then
        assert chat is not None
        assert chat.user_id == user_id
        assert chat.title == chat_title
        assert chat.chat_id is not None
        assert len(chat.chat_id) > 0
        assert isinstance(chat.created_at, datetime)
        assert isinstance(chat.updated_at, datetime)

    def test_create_chat_default_title(self, db_manager, sample_user_id):
        """Тест создания чата с заголовком по умолчанию."""
        # Given
        user_id = sample_user_id
        db_manager.create_user(user_id)

        # When
        chat = db_manager.create_chat(user_id)  # Без заголовка

        # Then
        assert chat.title == "New Chat"  # Заголовок по умолчанию

    def test_create_chat_nonexistent_user(self, db_manager):
        """Тест создания чата для несуществующего пользователя."""
        # When/Then
        # SQLite не проверяет FK по умолчанию, так что чат создастся
        chat = db_manager.create_chat("nonexistent_user", "Test Chat")
        assert chat is not None
        assert chat.user_id == "nonexistent_user"

    def test_add_message_success(self, db_manager, sample_user_id, sample_chat_title):
        """Тест успешного добавления сообщения."""
        # Given
        user_id = sample_user_id
        chat_title = sample_chat_title
        db_manager.create_user(user_id)
        chat = db_manager.create_chat(user_id, chat_title)

        # When
        message = db_manager.add_message(chat.chat_id, "user", "Test message content")

        # Then
        assert message is not None
        assert message.chat_id == chat.chat_id
        assert message.role == "user"
        assert message.content == "Test message content"
        assert message.id is not None
        assert isinstance(message.timestamp, datetime)
        assert message.thinking_time is None

    def test_add_message_with_thinking_time(self, db_manager, sample_user_id):
        """Тест добавления сообщения с временем размышления."""
        # Given
        user_id = sample_user_id
        db_manager.create_user(user_id)
        chat = db_manager.create_chat(user_id)

        # When
        thinking_time = 2.5
        message = db_manager.add_message(chat.chat_id, "assistant", "Response", thinking_time)

        # Then
        assert message.thinking_time == thinking_time

    def test_get_chat_with_messages(self, db_manager, sample_user_id):
        """Тест получения чата с загруженными сообщениями."""
        # Given
        user_id = sample_user_id
        db_manager.create_user(user_id)
        chat = db_manager.create_chat(user_id, "Chat with messages")

        # Добавляем несколько сообщений
        message1 = db_manager.add_message(chat.chat_id, "user", "Hello")
        message2 = db_manager.add_message(chat.chat_id, "assistant", "Hi there!")

        # When
        retrieved_chat = db_manager.get_chat(chat.chat_id)

        # Then
        assert retrieved_chat is not None
        assert retrieved_chat.chat_id == chat.chat_id
        assert len(retrieved_chat.messages) == 2

        # Проверяем что сообщения отсортированы по времени
        assert retrieved_chat.messages[0].timestamp <= retrieved_chat.messages[1].timestamp

    def test_get_chat_not_exists(self, db_manager):
        """Тест получения несуществующего чата."""
        # When
        chat = db_manager.get_chat("nonexistent_chat")

        # Then
        assert chat is None

    def test_get_user_chats(self, db_manager, sample_user_id):
        """Тест получения всех чатов пользователя."""
        # Given
        user_id = sample_user_id
        db_manager.create_user(user_id)

        # Создаем несколько чатов
        chat1 = db_manager.create_chat(user_id, "Chat 1")
        chat2 = db_manager.create_chat(user_id, "Chat 2")
        chat3 = db_manager.create_chat(user_id, "Chat 3")

        # Обновляем время последних чатов (чтобы они не удалялись)
        db_manager.add_message(chat1.chat_id, "user", "Message 1")
        db_manager.add_message(chat2.chat_id, "user", "Message 2")

        # When
        user_chats = db_manager.get_user_chats(user_id)

        # Then
        assert len(user_chats) == 3

        # Проверяем что чаты отсортированы по времени обновления (сначала новые)
        assert user_chats[0].updated_at >= user_chats[1].updated_at
        assert user_chats[1].updated_at >= user_chats[2].updated_at

    def test_get_user_chats_no_chats(self, db_manager, sample_user_id):
        """Тест получения чатов пользователя без чатов."""
        # Given
        user_id = sample_user_id
        db_manager.create_user(user_id)

        # When
        user_chats = db_manager.get_user_chats(user_id)

        # Then
        assert len(user_chats) == 0

    def test_update_chat_title(self, db_manager, sample_user_id):
        """Тест обновления заголовка чата."""
        # Given
        user_id = sample_user_id
        db_manager.create_user(user_id)
        chat = db_manager.create_chat(user_id, "Original Title")

        # When
        updated_chat = db_manager.update_chat_title(chat.chat_id, "New Title")

        # Then
        assert updated_chat is not None
        assert updated_chat.title == "New Title"
        assert updated_chat.chat_id == chat.chat_id

    def test_update_chat_title_not_exists(self, db_manager):
        """Тест обновления заголовка несуществующего чата."""
        # When
        result = db_manager.update_chat_title("nonexistent", "New Title")

        # Then
        assert result is None

    def test_delete_chat_success(self, db_manager, sample_user_id):
        """Тест успешного удаления чата."""
        # Given
        user_id = sample_user_id
        db_manager.create_user(user_id)
        chat = db_manager.create_chat(user_id, "Chat to delete")

        # Добавляем сообщения
        message1 = db_manager.add_message(chat.chat_id, "user", "Message 1")
        message2 = db_manager.add_message(chat.chat_id, "assistant", "Response 1")

        # When
        result = db_manager.delete_chat(chat.chat_id)

        # Then
        assert result is True

        # Проверяем что чат и сообщения удалены
        deleted_chat = db_manager.get_chat(chat.chat_id)
        assert deleted_chat is None

    def test_delete_chat_not_exists(self, db_manager):
        """Тест удаления несуществующего чата."""
        # When
        result = db_manager.delete_chat("nonexistent_chat")

        # Then
        assert result is False

    def test_cleanup_user_chats(self, db_manager, sample_user_id):
        """Тест очистки старых чатов пользователя."""
        # Given
        user_id = sample_user_id
        db_manager.create_user(user_id)

        # Создаем много чатов
        chats = []
        for i in range(5):
            chat = db_manager.create_chat(user_id, f"Chat {i}")
            chats.append(chat)

        # Обновляем время последних чатов (чтобы они не удалялись)
        # Обновляем время 3 последних чатов, чтобы они были "новее"
        for chat in chats[-3:]:  # Последние 3 чата обновляем
            db_manager.add_message(chat.chat_id, "user", f"Keep {chat.chat_id}")

        # When - оставляем только 2 самых новых чата
        deleted_count = db_manager.cleanup_user_chats(user_id, keep=2)

        # Then - должны удалиться 3 самых старых чата (5 - 2 = 3)
        assert deleted_count == 3

        # Проверяем что остались только 2 самых новых чата
        remaining_chats = db_manager.get_user_chats(user_id)
        assert len(remaining_chats) == 2

        # Проверяем что остались именно те чаты, которые мы обновляли
        remaining_chat_ids = {chat.chat_id for chat in remaining_chats}
        expected_chat_ids = {chat.chat_id for chat in chats[-2:]}  # Последние 2
        assert remaining_chat_ids == expected_chat_ids

    def test_database_manager_initialization(self, db_manager):
        """Тест инициализации DatabaseManager."""
        # Given/When/Then
        assert db_manager.engine is not None
        assert db_manager.SessionLocal is not None

    def test_message_roles(self, db_manager, sample_user_id):
        """Тест сообщений с разными ролями."""
        # Given
        user_id = sample_user_id
        db_manager.create_user(user_id)
        chat = db_manager.create_chat(user_id)

        # When
        user_message = db_manager.add_message(chat.chat_id, "user", "User message")
        assistant_message = db_manager.add_message(chat.chat_id, "assistant", "Assistant response")

        # Then
        assert user_message.role == "user"
        assert assistant_message.role == "assistant"

        # Проверяем что сообщения сохранены
        retrieved_chat = db_manager.get_chat(chat.chat_id)
        assert len(retrieved_chat.messages) == 2  # noqa: S101
        assert retrieved_chat.messages[0].role == "user"  # noqa: S101
        assert retrieved_chat.messages[1].role == "assistant"  # noqa: S101


class TestDatabaseIntegration:
    """Интеграционные тесты базы данных."""

    @pytest.fixture
    def temp_db(self):
        """Создание временной базы данных для тестов."""
        import os
        import tempfile

        from init_db import DatabaseInitializer

        # Создаем временный файл для тестовой БД
        temp_fd, temp_path = tempfile.mkstemp(suffix=".db")
        os.close(temp_fd)  # Закрываем файловый дескриптор

        # Создаем конфигурацию для временной БД
        temp_db_url = f"sqlite:///{temp_path}"
        temp_config = DatabaseConfig(url=temp_db_url)

        # Инициализируем базу данных с помощью DatabaseInitializer
        initializer = DatabaseInitializer(temp_config)
        initializer.initialize_database()

        yield temp_db_url

        # Очистка
        try:
            if os.path.exists(temp_path):
                os.unlink(temp_path)
        except PermissionError:
            pass

    def test_full_integration_scenario(self, temp_db):
        """Тест полного сценария работы с базой данных."""
        # Given
        config = DatabaseConfig(url=temp_db)
        db_manager = DatabaseManager(config)

        # When - Создаем пользователя
        user = db_manager.create_user("integration_user")
        assert user.user_id == "integration_user"

        # And - Создаем несколько чатов
        chat1 = db_manager.create_chat("integration_user", "First Chat")
        chat2 = db_manager.create_chat("integration_user", "Second Chat")

        # And - Добавляем сообщения в чаты
        msg1 = db_manager.add_message(chat1.chat_id, "user", "Hello from first chat")
        msg2 = db_manager.add_message(chat1.chat_id, "assistant", "Hello! How can I help?")
        msg3 = db_manager.add_message(chat2.chat_id, "user", "Hello from second chat")

        # And - Обновляем заголовок чата
        updated_chat = db_manager.update_chat_title(chat1.chat_id, "Updated First Chat")
        assert updated_chat.title == "Updated First Chat"

        # And - Получаем чаты пользователя
        user_chats = db_manager.get_user_chats("integration_user")
        assert len(user_chats) == 2

        # Then - Проверяем целостность данных
        retrieved_chat1 = db_manager.get_chat(chat1.chat_id)
        retrieved_chat2 = db_manager.get_chat(chat2.chat_id)

        assert len(retrieved_chat1.messages) == 2
        assert len(retrieved_chat2.messages) == 1
        assert retrieved_chat1.title == "Updated First Chat"

    def test_concurrent_users_scenario(self, temp_db):
        """Тест сценария с несколькими пользователями."""
        # Given
        config = DatabaseConfig(url=temp_db)
        db_manager = DatabaseManager(config)

        # When - Создаем нескольких пользователей
        user1 = db_manager.create_user("user1")
        user2 = db_manager.create_user("user2")
        user3 = db_manager.create_user("user3")

        # And - Каждый создает чаты
        chat1 = db_manager.create_chat("user1", "User1 Chat")
        chat2 = db_manager.create_chat("user2", "User2 Chat")
        chat3 = db_manager.create_chat("user3", "User3 Chat")

        # And - Добавляем сообщения
        db_manager.add_message(chat1.chat_id, "user", "Message from user1")
        db_manager.add_message(chat2.chat_id, "user", "Message from user2")
        db_manager.add_message(chat3.chat_id, "user", "Message from user3")

        # Then - Проверяем изоляцию данных
        user1_chats = db_manager.get_user_chats("user1")
        user2_chats = db_manager.get_user_chats("user2")
        user3_chats = db_manager.get_user_chats("user3")

        assert len(user1_chats) == 1
        assert len(user2_chats) == 1
        assert len(user3_chats) == 1

        # Проверяем что пользователи не видят чужие чаты
        all_chat_ids = [chat.chat_id for chat in user1_chats + user2_chats + user3_chats]
        assert len(all_chat_ids) == 3
        assert len(set(all_chat_ids)) == 3  # Все ID уникальны


class TestDatabaseEdgeCases:
    """Тесты краевых случаев и обработки ошибок."""

    @pytest.fixture
    def db_manager(self):
        """Фикстура для создания DatabaseManager."""
        config = DatabaseConfig()
        return DatabaseManager(config)

    @pytest.fixture
    def sample_user_id(self):
        """Генерация уникального ID пользователя для тестов."""
        return f"edge_test_user_{uuid.uuid4().hex[:8]}"

    @pytest.fixture
    def sample_chat_title(self):
        """Генерация уникального заголовка чата."""
        return f"Edge Test Chat {uuid.uuid4().hex[:8]}"

    def test_long_content_message(self, db_manager, sample_user_id):
        """Тест сообщения с очень длинным содержимым."""
        # Given
        user_id = f"{sample_user_id}_long"
        db_manager.create_user(user_id)
        chat = db_manager.create_chat(user_id)

        # When
        long_content = "Very long message content " * 100  # ~3000 символов
        message = db_manager.add_message(chat.chat_id, "user", long_content)

        # Then
        assert message.content == long_content
        assert len(message.content) > 1000

    def test_special_characters_in_content(self, db_manager, sample_user_id):
        """Тест сообщения со специальными символами."""
        # Given
        user_id = f"{sample_user_id}_special"
        db_manager.create_user(user_id)
        chat = db_manager.create_chat(user_id)

        # When
        special_content = "Message with émojis 🎉, спецсимволы !@#$%^&*(), русский текст"
        message = db_manager.add_message(chat.chat_id, "user", special_content)

        # Then
        assert message.content == special_content

    def test_empty_chat_title(self, db_manager, sample_user_id):
        """Тест создания чата с пустым заголовком."""
        # Given
        user_id = f"{sample_user_id}_empty"
        db_manager.create_user(user_id)

        # When
        chat = db_manager.create_chat(user_id, "")

        # Then
        assert chat.title == "New Chat"  # Должно быть значение по умолчанию

    def test_unicode_user_id(self, db_manager, sample_user_id):
        """Тест пользователя с unicode ID."""
        # When
        unicode_user_id = f"{sample_user_id}_unicode_пользователь"
        user = db_manager.create_user(unicode_user_id)

        # Then
        assert user.user_id == unicode_user_id

        # Проверяем что можем получить пользователя
        retrieved_user = db_manager.get_user(unicode_user_id)
        assert retrieved_user.user_id == unicode_user_id


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
