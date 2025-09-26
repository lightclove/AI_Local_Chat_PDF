#!/usr/bin/env python3
"""
Database Diagnostics Tool

Этот инструмент предоставляет подробную диагностику состояния базы данных,
анализ производительности и рекомендации по оптимизации.

Features:
- Анализ структуры таблиц и индексов
- Проверка целостности данных
- Анализ производительности запросов
- Рекомендации по оптимизации
- Мониторинг размера базы данных

Usage:
    python db_diagnostics.py              # Полная диагностика
    python db_diagnostics.py --tables     # Только анализ таблиц
    python db_diagnostics.py --queries    # Только анализ запросов
    python db_diagnostics.py --optimize   # Рекомендации по оптимизации
"""

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger
from sqlalchemy import create_engine, func, inspect, text
from sqlalchemy.orm import sessionmaker

# Добавляем корневую директорию в путь для импортов
root_dir = Path(__file__).parent
sys.path.insert(0, str(root_dir))

from config import DatabaseConfig  # noqa: E402
from database import Chat, Message, User  # noqa: E402


class DatabaseDiagnostics:
    """Диагностика базы данных."""

    def __init__(self, config: DatabaseConfig):
        """Инициализация."""
        self.config = config
        self.engine = create_engine(config.url, echo=False)
        self.SessionLocal = sessionmaker(bind=self.engine)

    def get_database_info(self) -> dict[str, Any]:
        """Получение общей информации о базе данных."""
        try:
            with self.engine.connect() as conn:
                # SQLite specific queries
                if "sqlite" in self.config.url:
                    # Размер базы данных
                    result = conn.execute(text("PRAGMA database_list;"))
                    db_list = result.fetchall()

                    # Общая статистика
                    result = conn.execute(text("PRAGMA stats;"))
                    stats = result.fetchall()

                    return {
                        "database_type": "SQLite",
                        "database_path": self.config.url.replace("sqlite:///", ""),
                        "size_mb": self._get_database_size(),
                        "pragma_stats": stats,
                        "database_list": db_list,
                    }
                else:
                    return {"database_type": "Other", "url": self.config.url}

        except Exception as e:
            logger.error(f"Error getting database info: {e}")
            return {"error": str(e)}

    def _get_database_size(self) -> float:
        """Получение размера базы данных в МБ."""
        try:
            db_path = self.config.url.replace("sqlite:///", "")
            size_bytes = Path(db_path).stat().st_size
            return round(size_bytes / (1024 * 1024), 2)
        except Exception:
            return 0.0

    def analyze_tables(self) -> dict[str, Any]:
        """Анализ таблиц и их структуры."""
        try:
            inspector = inspect(self.engine)
            tables = inspector.get_table_names()

            table_analysis = {}

            for table_name in tables:
                if table_name.startswith("sqlite_"):
                    continue

                # Получаем информацию о колонках
                columns = inspector.get_columns(table_name)
                indexes = inspector.get_indexes(table_name)
                foreign_keys = inspector.get_foreign_keys(table_name)

                # Подсчитываем записи
                with self.engine.connect() as conn:
                    result = conn.execute(text(f"SELECT COUNT(*) FROM {table_name}"))  # noqa: S608
                    row_count = result.scalar()

                table_analysis[table_name] = {
                    "columns": columns,
                    "indexes": indexes,
                    "foreign_keys": foreign_keys,
                    "row_count": row_count,
                    "estimated_size_mb": self._estimate_table_size(table_name, row_count, columns),
                }

            return table_analysis

        except Exception as e:
            logger.error(f"Error analyzing tables: {e}")
            return {"error": str(e)}

    def _estimate_table_size(self, table_name: str, row_count: int, columns: list) -> float:
        """Оценка размера таблицы в МБ."""
        try:
            total_size: float = 0.0

            # Оценка размера данных (примитивная)
            for col in columns:
                col_type = str(col["type"]).lower()

                if "text" in col_type or "varchar" in col_type:
                    avg_length = 50  # Предполагаемая средняя длина
                    total_size += row_count * avg_length
                elif "int" in col_type:
                    total_size += row_count * 8  # 8 байт на int
                elif "float" in col_type or "real" in col_type:
                    total_size += row_count * 8  # 8 байт на float
                elif "datetime" in col_type:
                    total_size += row_count * 8  # 8 байт на datetime
                else:
                    total_size += row_count * 32  # Консервативная оценка

            # Добавляем накладные расходы SQLite
            total_size = total_size * 1.2  # 20% overhead

            return round(total_size / (1024 * 1024), 3)

        except Exception:
            return 0.0

    def analyze_data_integrity(self) -> dict[str, Any]:
        """Анализ целостности данных."""
        issues = {}

        try:
            with self.SessionLocal() as session:
                # Проверка пользователей без чатов
                users_without_chats = session.query(User).filter(~User.chats.any()).count()

                # Проверка чатов без сообщений
                chats_without_messages = session.query(Chat).filter(~Chat.messages.any()).count()

                # Проверка сообщений с несуществующими чатами
                # (Для SQLite это сложно проверить через ORM, пропустим)

                # Проверка дубликатов
                duplicate_user_ids = (
                    session.query(User.user_id)
                    .group_by(User.user_id)
                    .having(func.count() > 1)
                    .count()
                )

                duplicate_chat_ids = (
                    session.query(Chat.chat_id)
                    .group_by(Chat.chat_id)
                    .having(func.count() > 1)
                    .count()
                )

                issues = {
                    "orphaned_users": users_without_chats,
                    "empty_chats": chats_without_messages,
                    "duplicate_user_ids": duplicate_user_ids,
                    "duplicate_chat_ids": duplicate_chat_ids,
                }

        except Exception as e:
            logger.error(f"Error analyzing data integrity: {e}")
            issues["error"] = str(e)

        return issues

    def get_performance_stats(self) -> dict[str, Any]:
        """Получение статистики производительности."""
        stats = {}

        try:
            with self.SessionLocal() as session:
                # Общая статистика
                stats["total_users"] = session.query(User).count()
                stats["total_chats"] = session.query(Chat).count()
                stats["total_messages"] = session.query(Message).count()

                # Статистика по ролям сообщений
                user_messages = session.query(Message).filter(Message.role == "user").count()

                assistant_messages = (
                    session.query(Message).filter(Message.role == "assistant").count()
                )

                stats["messages_by_role"] = {"user": user_messages, "assistant": assistant_messages}

                # Среднее количество сообщений на чат
                if stats["total_chats"] > 0:
                    stats["avg_messages_per_chat"] = round(
                        stats["total_messages"] / stats["total_chats"], 2
                    )

                # Чаты по пользователям
                stats["chats_per_user"] = (
                    round(stats["total_chats"] / stats["total_users"], 2)
                    if stats["total_users"] > 0
                    else 0
                )

        except Exception as e:
            logger.error(f"Error getting performance stats: {e}")
            stats["error"] = str(e)

        return stats

    def generate_recommendations(self, analysis: dict[str, Any]) -> list[str]:
        """Генерация рекомендаций по оптимизации."""
        recommendations = []

        # Рекомендации на основе размера базы данных
        db_size = analysis.get("database_info", {}).get("size_mb", 0)
        if db_size > 100:
            recommendations.append(f"Database size is {db_size}MB. Consider archiving old data.")
        elif db_size > 50:
            recommendations.append(
                "Database size is growing. Monitor growth and consider optimization."
            )

        # Рекомендации по индексам
        tables = analysis.get("table_analysis", {})
        for table_name, info in tables.items():
            if info["row_count"] > 1000 and not info["indexes"]:
                recommendations.append(
                    f"Table '{table_name}' has {info['row_count']} rows but no indexes. "
                    "Consider adding indexes for better performance."
                )

        # Рекомендации по целостности данных
        integrity = analysis.get("data_integrity", {})
        if integrity.get("orphaned_users", 0) > 0:
            recommendations.append(
                f"Found {integrity['orphaned_users']} users without chats. "
                "Consider cleanup or review."
            )

        if integrity.get("empty_chats", 0) > 0:
            recommendations.append(
                f"Found {integrity['empty_chats']} empty chats. " "Consider cleanup."
            )

        # Рекомендации по производительности
        perf_stats = analysis.get("performance_stats", {})
        if perf_stats.get("avg_messages_per_chat", 0) > 100:
            recommendations.append(
                "Average messages per chat is high. Consider chat pagination for UI."
            )

        return recommendations

    def run_full_diagnostics(self) -> dict[str, Any]:
        """Полная диагностика базы данных."""
        logger.info("Running full database diagnostics...")

        results = {
            "timestamp": datetime.now().isoformat(),
            "database_info": self.get_database_info(),
            "table_analysis": self.analyze_tables(),
            "data_integrity": self.analyze_data_integrity(),
            "performance_stats": self.get_performance_stats(),
            "recommendations": [],
        }

        results["recommendations"] = self.generate_recommendations(results)

        return results

    def print_report(self, results: dict[str, Any]) -> None:
        """Вывод отчета в консоль."""
        print("\n" + "=" * 60)
        print("DATABASE DIAGNOSTICS REPORT")
        print("=" * 60)
        print(f"Generated: {results['timestamp']}")
        print()

        # Информация о базе данных
        db_info = results.get("database_info", {})
        print("DATABASE INFO:")
        print(f"  Type: {db_info.get('database_type', 'Unknown')}")
        if "size_mb" in db_info:
            print(f"  Size: {db_info['size_mb']} MB")
        print()

        # Статистика производительности
        perf_stats = results.get("performance_stats", {})
        print("PERFORMANCE STATS:")
        for key, value in perf_stats.items():
            if key != "error":
                print(f"  {key}: {value}")
        print()

        # Анализ таблиц
        table_analysis = results.get("table_analysis", {})
        print("TABLE ANALYSIS:")
        for table_name, info in table_analysis.items():
            if isinstance(info, dict) and "error" not in info:
                print(f"  {table_name}:")
                print(f"    Rows: {info['row_count']}")
                print(f"    Size: ~{info['estimated_size_mb']} MB")
                print(f"    Indexes: {len(info['indexes'])}")
        print()

        # Целостность данных
        integrity = results.get("data_integrity", {})
        print("DATA INTEGRITY:")
        for key, value in integrity.items():
            if key != "error":
                print(f"  {key}: {value}")
        print()

        # Рекомендации
        recommendations = results.get("recommendations", [])
        if recommendations:
            print("RECOMMENDATIONS:")
            for i, rec in enumerate(recommendations, 1):
                print(f"  {i}. {rec}")
        else:
            print("RECOMMENDATIONS:")
            print("  No specific recommendations at this time.")
        print()

        # Сохраняем отчет в файл
        report_file = f"db_diagnostics_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(report_file, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, ensure_ascii=False, default=str)

        print(f"Full report saved to: {report_file}")


def main():
    """Основная функция."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Database Diagnostics Tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python db_diagnostics.py              # Full diagnostics
  python db_diagnostics.py --tables     # Table analysis only
  python db_diagnostics.py --queries    # Performance stats only
  python db_diagnostics.py --optimize   # Recommendations only
  python db_diagnostics.py --json       # JSON output only
        """,
    )

    parser.add_argument("--tables", action="store_true", help="Show table analysis only")

    parser.add_argument("--queries", action="store_true", help="Show performance stats only")

    parser.add_argument("--integrity", action="store_true", help="Show data integrity check only")

    parser.add_argument(
        "--optimize", action="store_true", help="Show optimization recommendations only"
    )

    parser.add_argument("--json", action="store_true", help="Output results as JSON only")

    args = parser.parse_args()

    # Создание конфигурации
    db_config = DatabaseConfig()

    # Инициализация диагностики
    diagnostics = DatabaseDiagnostics(db_config)

    # Полная диагностика
    results = diagnostics.run_full_diagnostics()

    if args.json:
        # Только JSON вывод
        print(json.dumps(results, indent=2, ensure_ascii=False, default=str))
        return

    if args.tables:
        # Только анализ таблиц
        print("\nTABLE ANALYSIS:")
        table_analysis = results.get("table_analysis", {})
        for table_name, info in table_analysis.items():
            if isinstance(info, dict) and "error" not in info:
                print(f"{table_name}: {info['row_count']} rows, ~{info['estimated_size_mb']} MB")
        return

    if args.queries:
        # Только статистика производительности
        perf_stats = results.get("performance_stats", {})
        for key, value in perf_stats.items():
            if key != "error":
                print(f"{key}: {value}")
        return

    if args.integrity:
        # Только проверка целостности
        integrity = results.get("data_integrity", {})
        for key, value in integrity.items():
            if key != "error":
                print(f"{key}: {value}")
        return

    if args.optimize:
        # Только рекомендации
        recommendations = results.get("recommendations", [])
        for i, rec in enumerate(recommendations, 1):
            print(f"{i}. {rec}")
        return

    # Полный отчет
    diagnostics.print_report(results)


if __name__ == "__main__":
    main()
