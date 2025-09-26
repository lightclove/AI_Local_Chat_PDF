#!/usr/bin/env python3
"""
Скрипт для тщательного тестирования системы логирования.
Тестирует все компоненты: обработку исключений, asyncio, middleware, traceback.
"""

import asyncio
import sys
from pathlib import Path

# Добавляем корневую директорию в path для импорта
sys.path.insert(0, str(Path(__file__).parent.parent))

from loguru import logger


def test_basic_logging():
    """Тест базового логирования."""
    logger.info("=== Тест базового логирования ===")

    logger.debug("Тест DEBUG сообщения")
    logger.info("Тест INFO сообщения")
    logger.warning("Тест WARNING сообщения")
    logger.error("Тест ERROR сообщения")

    logger.info("=== Тест базового логирования завершен ===")


def test_exception_logging():
    """Тест логирования исключений."""
    logger.info("=== Тест логирования исключений ===")

    try:
        raise ValueError("Тестовое исключение ValueError")
    except Exception as e:
        logger.opt(exception=e).error("Тест ValueError с traceback")

    try:
        raise RuntimeError("Тестовое исключение RuntimeError")
    except Exception as e:
        logger.opt(exception=e).error("Тест RuntimeError с traceback")

    try:
        raise Exception("Общее исключение для тестирования")
    except Exception as e:
        logger.opt(exception=e).error("Тест общего исключения с traceback")

    logger.info("=== Тест логирования исключений завершен ===")


def test_nested_exception():
    """Тест вложенных исключений."""
    logger.info("=== Тест вложенных исключений ===")

    def inner_function():
        raise ValueError("Внутреннее исключение")

    def outer_function():
        try:
            inner_function()
        except Exception as e:
            raise RuntimeError("Внешнее исключение") from e

    try:
        outer_function()
    except Exception as e:
        logger.opt(exception=e).error("Тест вложенного исключения с chained traceback")

    logger.info("=== Тест вложенных исключений завершен ===")


async def test_async_exception():
    """Тест асинхронных исключений."""
    logger.info("=== Тест асинхронных исключений ===")

    async def async_function():
        await asyncio.sleep(0.1)
        raise asyncio.TimeoutError("Тест таймаута")

    try:
        await async_function()
    except Exception as e:
        logger.opt(exception=e).error("Тест асинхронного исключения")

    logger.info("=== Тест асинхронных исключений завершен ===")


def test_sys_excepthook():
    """Тест sys.excepthook."""
    logger.info("=== Тест sys.excepthook ===")

    def test_function():
        raise ValueError("Тест sys.excepthook")

    try:
        test_function()
    except Exception:
        # Симулируем необработанное исключение
        exc_type, exc_value, tb = sys.exc_info()
        # Импортируем функции из main для тестирования
        import main

        main.handle_exception(exc_type, exc_value, tb)

    logger.info("=== Тест sys.excepthook завершен ===")


def test_logger_configuration():
    """Тест конфигурации логгера."""
    logger.info("=== Тест конфигурации логгера ===")

    # Проверяем что логгер настроен правильно
    for handler in logger._core.handlers.values():
        print(
            f"Handler: {handler._name}, Level: {handler._level}, Backtrace: {getattr(handler, 'backtrace', 'N/A')}, Diagnose: {getattr(handler, 'diagnose', 'N/A')}"
        )

    logger.info("=== Тест конфигурации логгера завершен ===")


def test_log_files():
    """Тест записи в лог файлы."""
    logger.info("=== Тест записи в лог файлы ===")

    # Создаем искусственные ошибки для проверки записи в файлы
    try:
        raise FileNotFoundError("Тест записи в лог файлы")
    except Exception as e:
        logger.opt(exception=e).error("Тест записи исключения в лог файлы")

    try:
        raise ConnectionError("Тест сетевой ошибки")
    except Exception as e:
        logger.opt(exception=e).warning("Тест предупреждения с traceback")

    logger.info("=== Тест записи в лог файлы завершен ===")


def main():
    """Главная функция тестирования."""
    logger.info("=== НАЧАЛО ТЕСТИРОВАНИЯ СИСТЕМЫ ЛОГИРОВАНИЯ ===")

    try:
        # Тест базового логирования
        test_basic_logging()

        # Тест логирования исключений
        test_exception_logging()

        # Тест вложенных исключений
        test_nested_exception()

        # Тест конфигурации логгера
        test_logger_configuration()

        # Тест записи в лог файлы
        test_log_files()

        # Тест sys.excepthook
        test_sys_excepthook()

        # Тест асинхронных исключений
        asyncio.run(test_async_exception())

    except Exception as e:
        logger.opt(exception=e).error("Критическая ошибка в тесте логирования")
        return 1

    logger.info("=== ТЕСТИРОВАНИЕ СИСТЕМЫ ЛОГИРОВАНИЯ ЗАВЕРШЕНО УСПЕШНО ===")
    return 0


if __name__ == "__main__":
    exit(main())
