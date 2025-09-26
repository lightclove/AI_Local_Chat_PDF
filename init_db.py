#!/usr/bin/env python3
"""
Database Initialization Script

Этот скрипт обеспечивает безопасную инициализацию базы данных:
1. Проверяет существование таблиц
2. Если таблиц нет, применяет миграции
3. Если таблицы существуют, проверяет состояние миграций
4. Предоставляет опции для восстановления из различных состояний

Usage:
    python init_db.py              # Автоматическая инициализация
    python init_db.py --force      # Принудительная инициализация (пересоздание таблиц)
    python init_db.py --check-only # Только проверка состояния
"""

import sys
from pathlib import Path
from typing import Any

from loguru import logger
from sqlalchemy import create_engine, inspect, text

# Добавляем корневую директорию в путь для импортов
root_dir = Path(__file__).parent
sys.path.insert(0, str(root_dir))

from config import DatabaseConfig  # noqa: E402
from database import Base  # noqa: E402
from migrate import MigrationManager  # noqa: E402


class DatabaseInitializer:
    """Инициализатор базы данных."""

    def __init__(self, config: DatabaseConfig):
        """Инициализация."""
        self.config = config
        self.engine = create_engine(config.url, echo=config.echo)
        self.migration_manager = MigrationManager(config)

    def check_database_state(self) -> dict[str, Any]:
        """Проверка состояния базы данных."""
        try:
            # Проверяем подключение
            with self.engine.connect() as conn:
                conn.execute(text("SELECT 1"))

            # Проверяем таблицы
            inspector = inspect(self.engine)
            tables = inspector.get_table_names()

            # Исключаем системные таблицы SQLite
            user_tables = [t for t in tables if not t.startswith("sqlite_")]

            # Проверяем состояние миграций
            migration_status = self.migration_manager.check_migration_status()

            return {
                "connected": True,
                "tables": user_tables,
                "has_user_tables": len(user_tables) > 0,
                "migration_status": migration_status,
            }

        except Exception as e:
            logger.error(f"Database connection failed: {e}")
            return {
                "connected": False,
                "tables": [],
                "has_user_tables": False,
                "migration_status": {"error": str(e)},
            }

    def initialize_database(self, force: bool = False) -> bool:
        """Инициализация базы данных."""
        logger.info("Starting database initialization...")

        state = self.check_database_state()

        if not state["connected"]:
            logger.error("Cannot connect to database")
            return False

        # Если база данных пустая и нет таблиц
        if not state["has_user_tables"]:
            logger.info("Database is empty, applying migrations...")
            try:
                self.migration_manager.upgrade("head")
                logger.info("Database initialized successfully")
                return True
            except Exception as e:
                logger.error(f"Migration failed: {e}")
                return False

        # Если таблицы существуют
        logger.info(f"Found existing tables: {state['tables']}")

        if force:
            logger.warning("Force initialization requested - recreating all tables...")
            try:
                # Удаляем все таблицы
                Base.metadata.drop_all(bind=self.engine)
                # Создаем заново
                Base.metadata.create_all(bind=self.engine)
                logger.info("Database recreated successfully")
                return True
            except Exception as e:
                logger.error(f"Force initialization failed: {e}")
                return False

        # Проверяем состояние миграций
        migration_status = state["migration_status"]

        if migration_status.get("current_revision"):
            logger.info(
                f"Database is at migration revision: {migration_status['current_revision']}"
            )
            logger.info("Database is properly initialized")
            return True
        else:
            logger.warning("Database has tables but no migration tracking")
            logger.info("Consider running: python migrate.py stamp")
            return True

    def create_backup_tables(self) -> bool:
        """Создание резервных копий существующих таблиц."""
        logger.info("Creating backup tables...")

        state = self.check_database_state()
        if not state["has_user_tables"]:
            logger.info("No tables to backup")
            return True

        try:
            with self.engine.connect() as conn:
                for table_name in state["tables"]:
                    backup_name = f"{table_name}_backup_{int(__import__('time').time())}"

                    # Создаем резервную копию таблицы
                    conn.execute(text(f"CREATE TABLE {backup_name} AS SELECT * FROM {table_name}"))  # noqa: S608
                    logger.info(f"Created backup table: {backup_name}")

                conn.commit()
                logger.info("All tables backed up successfully")
                return True

        except Exception as e:
            logger.error(f"Backup creation failed: {e}")
            return False


def main():
    """Основная функция."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Database Initialization Script",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python init_db.py              # Automatic initialization
  python init_db.py --force      # Force initialization (recreate tables)
  python init_db.py --check-only # Check only (no changes)
  python init_db.py --backup     # Create backup before initialization
        """,
    )

    parser.add_argument("--force", action="store_true", help="Force recreate all tables")

    parser.add_argument(
        "--check-only", action="store_true", help="Only check database state, no changes"
    )

    parser.add_argument(
        "--backup",
        action="store_true",
        help="Create backup of existing tables before initialization",
    )

    args = parser.parse_args()

    # Создание конфигурации
    db_config = DatabaseConfig()

    # Инициализация
    initializer = DatabaseInitializer(db_config)

    # Проверка состояния
    logger.info("Checking database state...")
    state = initializer.check_database_state()

    logger.info("Database State:")
    logger.info(f"  Connected: {state['connected']}")
    logger.info(f"  Tables: {state['tables']}")
    logger.info(f"  Has user tables: {state['has_user_tables']}")
    logger.info(f"  Migration status: {state['migration_status']}")

    if args.check_only:
        return

    if args.backup:
        if not initializer.create_backup_tables():
            logger.error("Backup failed, aborting initialization")
            sys.exit(1)

    # Инициализация
    success = initializer.initialize_database(force=args.force)

    if success:
        logger.info("Database initialization completed successfully")
        sys.exit(0)
    else:
        logger.error("Database initialization failed")
        sys.exit(1)


if __name__ == "__main__":
    main()
