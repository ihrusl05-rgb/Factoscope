from django.db import models

FACT_LIMIT_DEFAULT = 5

ZODIAC_SIGNS = [
    ("aries", "Овен"),
    ("taurus", "Телец"),
    ("gemini", "Близнецы"),
    ("cancer", "Рак"),
    ("leo", "Лев"),
    ("virgo", "Дева"),
    ("libra", "Весы"),
    ("scorpio", "Скорпион"),
    ("sagittarius", "Стрелец"),
    ("capricorn", "Козерог"),
    ("aquarius", "Водолей"),
    ("pisces", "Рыбы"),
]

SIGN_ALIASES = {
    "овен": "aries",
    "телец": "taurus",
    "близнецы": "gemini",
    "рак": "cancer",
    "лев": "leo",
    "дева": "virgo",
    "весы": "libra",
    "скорпион": "scorpio",
    "стрелец": "sagittarius",
    "козерог": "capricorn",
    "водолей": "aquarius",
    "рыбы": "pisces",
    "aries": "aries",
    "taurus": "taurus",
    "gemini": "gemini",
    "cancer": "cancer",
    "leo": "leo",
    "virgo": "virgo",
    "libra": "libra",
    "scorpio": "scorpio",
    "sagittarius": "sagittarius",
    "capricorn": "capricorn",
    "aquarius": "aquarius",
    "pisces": "pisces",
}


def normalize_sign(value: str) -> str | None:
    """Приводит значение знака к коду модели, учитывая латиницу и кириллицу."""
    return SIGN_ALIASES.get((value or "").strip().lower())


class Horoscope(models.Model):
    sign = models.CharField("Знак зодиака", max_length=16, choices=ZODIAC_SIGNS, db_index=True)
    date = models.DateField("Дата", db_index=True)
    text = models.TextField("Текст")
    is_active = models.BooleanField("Показывать", default=True)
    is_draft = models.BooleanField(
        "Черновик",
        default=False,
        help_text="Не проверено после импорта (не показывается на экранах)",
    )

    class Meta:
        verbose_name = "Гороскоп"
        verbose_name_plural = "Гороскопы"
        unique_together = ("sign", "date")
        ordering = ("date", "sign")

    def __str__(self):
        return f"{self.get_sign_display()} · {self.date}"


class Fact(models.Model):
    text = models.TextField("Текст")
    category = models.CharField("Категория", max_length=64, blank=True, db_index=True)
    ordering = models.PositiveIntegerField("Порядок", default=0)
    is_active = models.BooleanField("Показывать", default=True)

    class Meta:
        verbose_name = "Факт"
        verbose_name_plural = "Факты"
        ordering = ("ordering", "id")

    def __str__(self):
        return self.text[:60]


class SiteSettings(models.Model):
    """Одна строка настроек показа (синглтон, pk=1)."""

    shuffle_facts = models.BooleanField(
        "Перемешивать факты",
        default=False,
        help_text="Если включено, API отдаёт факты в случайном порядке "
        "(при ограничении limit — случайную выборку на каждый запрос).",
    )
    shuffle_horoscopes = models.BooleanField(
        "Перемешивать гороскопы",
        default=False,
        help_text="Если включено, запрос без параметра sign вернёт все знаки "
        "в случайном порядке (порядок меняется при каждом обращении).",
    )

    class Meta:
        verbose_name = "Настройки показа"
        verbose_name_plural = "Настройки показа"

    def __str__(self):
        return "Настройки показа"

    @classmethod
    def load(cls) -> "SiteSettings":
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class ImportLog(models.Model):
    class Status(models.TextChoices):
        SUCCESS = "success", "Успех"
        PARTIAL = "partial", "Частично"
        ERROR = "error", "Ошибка"

    class Format(models.TextChoices):
        JSON = "json", "JSON"
        XML = "xml", "XML"
        XLSX = "xlsx", "Excel"
        CSV = "csv", "CSV"

    filename = models.CharField("Файл", max_length=255)
    format = models.CharField("Формат", max_length=8, choices=Format.choices)
    status = models.CharField("Статус", max_length=8, choices=Status.choices)
    report = models.JSONField("Отчёт", default=dict, blank=True)
    created_at = models.DateTimeField("Импортировано", auto_now_add=True)

    class Meta:
        verbose_name = "Импорт"
        verbose_name_plural = "Импорты"
        ordering = ("-created_at",)

    def __str__(self):
        return f"{self.filename} · {self.get_status_display()}"