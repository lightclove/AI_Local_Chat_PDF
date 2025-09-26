import asyncio
from typing import Any

import aiohttp
from loguru import logger

from config import LmStudioConfig


class LMStudioClient:
    """Клиент для взаимодействия с LM Studio API (инкапсулирует HTTP-детали)."""

    def __init__(self, config: LmStudioConfig):
        logger.debug("Инициализация LMStudioClient")
        self.config = config
        logger.debug("Конфигурация установлена")
        self.base_url = config.base_url.rstrip("/")
        logger.debug(f"Базовый URL: {self.base_url}")
        self.model = config.model
        logger.debug(f"Модель: {self.model}")
        self.temperature = config.temperature
        logger.debug(f"Temperature: {self.temperature}")
        self.max_tokens = config.max_tokens
        logger.debug(f"Max tokens: {self.max_tokens}")

    async def _make_request(self, endpoint: str, data: dict[str, Any]) -> dict[str, Any]:
        logger.debug(f"Запрос к эндпоинту {endpoint}")
        url = f"{self.base_url}/{endpoint}"
        logger.debug(f"URL: {url}")

        try:
            timeout = aiohttp.ClientTimeout(total=self.config.timeout)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                logger.debug("ClientSession открыта")
                async with session.post(url, json=data) as response:
                    logger.debug("POST отправлен")
                    try:
                        response.raise_for_status()
                    except aiohttp.ClientResponseError as cre:
                        text = await response.text()
                        logger.error(f"LM Studio HTTP ошибка {cre.status}: {text}")
                        raise
                    logger.debug("Статус ответа проверен")
                    return await response.json()
                    logger.debug("JSON ответа получен")
        except asyncio.TimeoutError:
            logger.error("Таймаут запроса к LM Studio")
            raise
        except aiohttp.ClientError as e:
            logger.error(f"LM Studio API ошибка: {e}")
            raise
        except Exception as e:
            logger.error(f"Неожиданная ошибка: {e}")
            raise

    async def generate_response(
        self,
        messages: list[dict[str, str]],
        temperature: float | None = None,
        max_tokens: int | None = None,
        stream: bool = False,
    ) -> dict[str, Any]:
        """Сгенерировать ответ модели по истории сообщений."""
        logger.debug(f"Generating response with {len(messages)} messages")
        data = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature or self.temperature,
            "max_tokens": max_tokens or self.max_tokens,
            "stream": stream,
        }

        return await self._make_request("chat/completions", data)

    async def list_models(self) -> list[dict[str, Any]]:
        """Получить список доступных моделей из LM Studio."""
        try:
            timeout = aiohttp.ClientTimeout(total=self.config.timeout)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(f"{self.base_url}/models") as response:
                    response.raise_for_status()
                    body = await response.json()
                    # Нормализуем вывод к списку {id, ...}
                    if isinstance(body, dict) and "data" in body:
                        return body.get("data", [])
                    if isinstance(body, list):
                        return body
                    return []
        except Exception as e:
            logger.error(f"Error listing models: {e}")
            return []

    def set_model(self, model_id: str) -> None:
        """Установить активную модель."""
        if model_id:
            logger.info(f"Switching LM Studio model to {model_id}")
            self.model = model_id

    async def chat_completion(
        self,
        messages: list[dict[str, str]],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        """Создать чат-комплишн (непотоковый)."""
        result = await self.generate_response(messages, temperature, max_tokens, stream=False)
        return result

    async def stream_chat_completion(
        self,
        messages: list[dict[str, str]],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        """Получить потоковый ответ чат-комплишна (собирается в строку)."""
        result = await self.generate_response(messages, temperature, max_tokens, stream=True)

        # Сбор потоковых чанков в одну строку (упрощённая обработка для UI)
        full_response = ""
        for chunk in result.get("choices", []):
            if chunk.get("delta", {}).get("content"):
                content = chunk["delta"]["content"]
                full_response += content
                # Можно стримить это обратно клиенту

        return full_response

    async def health_check(self) -> bool:
        """Проверить доступность LM Studio (health check)."""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{self.base_url}/models") as response:
                    return response.status == 200
        except Exception as e:
            logger.error(f"Health check failed: {e}")
            return False

    def create_system_prompt(
        self,
        context: str,
        role_description: str = "Ты эксперт в своей области и помогаешь пользователю с его вопросами.",  # Типизация ролевого описания ИИ
    ) -> str:
        """Сконструировать системный промпт с переданным контекстом."""
        base_prompt = f"""{role_description}

Контекст из документов:
{context}

Инструкции:
- Используйте контекст выше для предоставления точных и релевантных ответов
- Если контекст не содержит необходимой информации, скажите об этом ясно
- Будьте полезны, информативны и разговорчивы
- Цитируйте релевантные источники, когда это необходимо"""

        return base_prompt


class ChatHistoryManager:
    """Менеджер истории чата для формирования контекста диалога."""

    def __init__(self, max_history: int):
        self.max_history = max_history

    def format_messages_for_api(self, messages: list[dict[str, str]]) -> list[dict[str, str]]:
        """Обрезать историю до последних `max_history` и подготовить к API."""
        # Оставляем только самые последние сообщения
        return messages[-self.max_history :]

    def add_user_message(
        self, messages: list[dict[str, str]], content: str
    ) -> list[dict[str, str]]:
        """Добавить пользовательское сообщение и вернуть усечённую историю."""
        messages.append({"role": "user", "content": content})
        return self.format_messages_for_api(messages)
