#!/usr/bin/env python3
"""
Database Migration Manager

Этот модуль предоставляет высокоуровневый интерфейс для управления миграциями базы данных
с использованием Alembic. Обеспечивает безопасное выполнение миграций, откаты и
диагностику состояния базы данных.

Features:
- Автоматическая инициализация базы данных
- Проверка состояния миграций
- Безопасное выполнение и откаты миграций
- Интеграция с существующими моделями SQLAlchemy

Usage:
    python migrate.py upgrade head      # Применить все миграции
    python migrate.py downgrade -1      # Откатить последнюю миграцию
    python migrate.py check             # Проверить состояние миграций
    python migrate.py init              # Инициализировать базу данных
"""

import sys
from pathlib import Path

from loguru import logger
from sqlalchemy import create_engine, text

from alembic import command  # type: ignore[attr-defined]
from alembic.config import Config

# Добавляем корневую директорию в путь для импортов
root_dir = Path(__file__).parent
sys.path.insert(0, str(root_dir))

from config import DatabaseConfig  # noqa: E402
from database import Base  # noqa: E402


class MigrationManager:
    """Менеджер миграций базы данных."""

    def __init__(self, config: DatabaseConfig):
        """Инициализация менеджера миграций."""
        self.config = config
        self.alembic_cfg = self._create_alembic_config()

    def _create_alembic_config(self) -> Config:
        """Создание конфигурации Alembic."""
        alembic_cfg = Config("alembic.ini")
        alembic_cfg.set_main_option("sqlalchemy.url", self.config.url)
        return alembic_cfg

    def init_db(self) -> None:
        """Инициализация базы данных: создание таблиц по текущим моделям."""
        logger.info("Initializing database with current models...")

        engine = create_engine(self.config.url, echo=self.config.echo)
        Base.metadata.create_all(bind=engine)

        logger.info("Database initialized successfully")

    def check_migration_status(self) -> dict:
        """Проверка состояния миграций."""
        try:
            # Проверяем текущую версию
            from alembic.runtime.migration import MigrationContext

            engine = create_engine(self.config.url)
            conn = engine.connect()

            context = MigrationContext.configure(conn)
            current_rev = context.get_current_revision()

            conn.close()

            return {
                "current_revision": current_rev,
                "database_exists": True,
                "has_tables": self._check_tables_exist(engine),
            }

        except Exception as e:
            logger.error(f"Error checking migration status: {e}")
            return {
                "current_revision": None,
                "database_exists": False,
                "has_tables": False,
                "error": str(e),
            }

    def _check_tables_exist(self, engine) -> bool:
        """Проверка существования таблиц в базе данных."""
        try:
            with engine.connect() as conn:
                result = conn.execute(
                    text("""
                    SELECT name FROM sqlite_master
                    WHERE type='table' AND name NOT LIKE 'sqlite_%'
                """)
                )
                tables = [row[0] for row in result]
                return len(tables) > 0
        except Exception:
            return False

    def upgrade(self, revision: str = "head") -> None:
        """Выполнение миграций до указанной версии."""
        logger.info(f"Upgrading database to revision: {revision}")
        command.upgrade(self.alembic_cfg, revision)
        logger.info("Database upgraded successfully")

    def downgrade(self, revision: str = "-1") -> None:
        """Откат миграций до указанной версии."""
        logger.info(f"Downgrading database to revision: {revision}")
        command.downgrade(self.alembic_cfg, revision)
        logger.info("Database downgraded successfully")

    def create_revision(self, message: str, autogenerate: bool = True) -> None:
        """Создание новой ревизии миграции."""
        logger.info(f"Creating new migration: {message}")
        command.revision(self.alembic_cfg, message=message, autogenerate=autogenerate)
        logger.info("Migration revision created")

    def show_history(self) -> None:
        """Отображение истории миграций."""
        logger.info("Migration history:")
        command.history(self.alembic_cfg)

    def show_current(self) -> None:
        """Отображение текущей версии."""
        logger.info("Current revision:")
        command.current(self.alembic_cfg)

    def stamp_head(self) -> None:
        """Пометить базу данных как обновленную до последней версии."""
        logger.info("Stamping database with head revision")
        command.stamp(self.alembic_cfg, "head")
        logger.info("Database stamped successfully")


def main():
    """Основная функция CLI."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Database Migration Manager",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python migrate.py upgrade head      # Apply all migrations
  python migrate.py downgrade -1      # Rollback last migration
  python migrate.py check             # Check migration status
  python migrate.py init              # Initialize database
  python migrate.py create "Add new feature"  # Create new migration
        """,
    )

    parser.add_argument(
        "command",
        choices=["upgrade", "downgrade", "check", "init", "create", "history", "current", "stamp"],
        help="Migration command to execute",
    )

    parser.add_argument(
        "target", nargs="?", help="Target revision (for upgrade/downgrade) or message (for create)"
    )

    parser.add_argument(
        "--autogenerate",
        action="store_true",
        default=True,
        help="Autogenerate migration script (default: True)",
    )

    args = parser.parse_args()

    # Создание конфигурации базы данных
    db_config = DatabaseConfig()

    # Инициализация менеджера миграций
    migration_manager = MigrationManager(db_config)

    try:
        if args.command == "check":
            status = migration_manager.check_migration_status()
            logger.info("Migration Status:")
            for key, value in status.items():
                logger.info(f"  {key}: {value}")

        elif args.command == "init":
            migration_manager.init_db()

        elif args.command == "upgrade":
            target = args.target or "head"
            migration_manager.upgrade(target)

        elif args.command == "downgrade":
            target = args.target or "-1"
            migration_manager.downgrade(target)

        elif args.command == "create":
            if not args.target:
                logger.error("Migration message is required for create command")
                sys.exit(1)
            migration_manager.create_revision(args.target, args.autogenerate)

        elif args.command == "history":
            migration_manager.show_history()

        elif args.command == "current":
            migration_manager.show_current()

        elif args.command == "stamp":
            migration_manager.stamp_head()

        else:
            logger.error(f"Unknown command: {args.command}")
            sys.exit(1)

    except Exception as e:
        logger.error(f"Migration command failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
