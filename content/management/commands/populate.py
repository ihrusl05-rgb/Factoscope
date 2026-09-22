"""Импорт контента из XML/JSON/Excel/CSV-файла.

Примеры:
    python manage.py populate content_2026-09-16.json --dry-run
    python manage.py populate content_2026-09-16.json
    python manage.py populate content_2026-09-16.xml --format xml
    python manage.py populate horoskop.xlsx --draft
"""

import os
from django.core.management.base import BaseCommand

from content.services import ContentImporter, ImportParseError

_FORMAT_BY_EXT = {
    ".json": "json",
    ".xml": "xml",
    ".xlsx": "xlsx",
    ".csv": "csv",
}


class Command(BaseCommand):
    help = "Импорт гороскопов и фактов из XML/JSON/Excel/CSV-файла"

    def add_arguments(self, parser):
        """Задаёт аргументы командной строки.

        Args:
            parser: Парсер аргументов команды.
        """
        parser.add_argument("path", help="Путь к файлу (.json/.xml/.xlsx/.csv)")
        parser.add_argument(
            "--format",
            choices=["json", "xml", "xlsx", "csv"],
            help="Формат (по умолчанию определяется по расширению)",
        )
        parser.add_argument("--dry-run", action="store_true", help="Только показать, что изменится")
        parser.add_argument(
            "--draft",
            action="store_true",
            help="Новые гороскопы помечать черновиком (не показывать на экранах до вычитки)",
        )

    def handle(self, *args, **options):
        """Показывает разницу файла с базой и применяет импорт."""
        path = options["path"]
        fmt = options["format"]
        if fmt is None:
            fmt = _FORMAT_BY_EXT.get(os.path.splitext(path)[1].lower())
        if fmt is None:
            self.stderr.write(self.style.ERROR("Не удалось определить формат по расширению файла."))
            return

        self.stdout.write(f"Чтение {path} (формат: {fmt})...")
        try:
            with open(path, "rb") as fh:
                importer = ContentImporter(fh.read(), fmt, mark_draft=options["draft"])
        except (ImportParseError, FileNotFoundError) as exc:
            self.stderr.write(self.style.ERROR(f"Не удалось импортировать: {exc}"))
            return

        rows = importer.preview_rows()
        summary = importer.preview_summary()
        if not rows:
            self.stdout.write("В файле нет записей.")
            return

        self.stdout.write(self.style.MIGRATE_HEADING("Разница с текущей базой:"))
        for row in rows:
            if row["status"] == "error":
                line = f"  [{row['row']:>3}] {row['kind']}: {row['detail']}"
                self.stdout.write(self.style.ERROR(line))
            else:
                marker = {"add": "+", "update": "~", "unchanged": "="}[row["status"]]
                line = f"  {marker} {row['kind']:<10} {row['key']}"
                style = ("SUCCESS" if row["status"] == "add" else "NOTICE") if row["status"] in ("add", "update") else ""
                self.stdout.write(getattr(self.style, style)(line) if style else line)

        self.stdout.write(
            self.style.WARNING(
                f"Итого: добавить {summary['add']}, обновить {summary['update']}, "
                f"без изменений {summary['unchanged']}, ошибок {summary['error']}."
            )
        )

        if options["dry_run"]:
            self.stdout.write(self.style.NOTICE("dry-run: изменения не применены."))
            return

        result = importer.commit()
        log = ContentImporter.save_log(os.path.basename(path), fmt, result)
        self.stdout.write(
            self.style.SUCCESS(
                f"Готово: добавлено {result.added}, обновлено {result.updated}, "
                f"без изменений {result.unchanged}, ошибок {len(result.errors)} (импорт #{log.pk})."
            )
        )