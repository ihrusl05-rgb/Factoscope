"""Сборка «пакета на день» для экранов adreality + кэширование."""

from __future__ import annotations

import hashlib
import json
import random
from datetime import date

from django.core.cache import cache

from .models import FACT_LIMIT_DEFAULT, Fact, Horoscope, SiteSettings

CACHE_VERSION = "v1"
PAYLOAD_TTL = 60 * 60 * 6
FLAGS_TTL = 30


def _facts_version() -> int:
    return cache.get("api:facts_version", 1)


def _payload_key(sign: str, day, content: str = "all") -> str:
    """Формирует ключ кэша для пакета дня.

    Args:
        sign: Код знака зодиака или ``None`` (все знаки).
        day: Дата в виде :class:`~datetime.date` или ISO-строки.
        content: Вариант контента: ``all``, ``facts`` или ``horoscopes``.

    Returns:
        Строка-ключ для Django cache.
    """
    day_str = day.isoformat() if hasattr(day, "isoformat") else str(day)
    return f"api:today:{CACHE_VERSION}:v{_facts_version()}:{content}:{sign or 'all'}:{day_str}"


def _flags() -> dict:
    """Возвращает текущие тумблеры показа с кэшированием на 30 секунд.

    Returns:
        Словарь ``{"shuffle_facts": bool, "shuffle_horoscopes": bool}``.
    """
    flags = cache.get("api:site_flags")
    if flags is None:
        s = SiteSettings.load()
        flags = {"shuffle_facts": s.shuffle_facts, "shuffle_horoscopes": s.shuffle_horoscopes}
        cache.set("api:site_flags", flags, timeout=FLAGS_TTL)
    return flags


def build_daily_payload(sign: str | None, day: date, content: str = "all") -> dict:
    """Собирает «пакет на день» для экрана.

    Факты и список знаков перемешиваются здесь, поэтому при включённых
    тумблерах `shuffle_facts`/`shuffle_horoscopes` результат не берётся
    из кэша.

    Args:
        sign: Код знака или ``None`` — тогда возвращаются все активные знаки.
        day: Дата дня.
        content: Вариант контента: ``all``, ``facts`` или ``horoscopes``.

    Returns:
        Словарь с ключами ``date``, ``sign`` и ``facts``/``horoscope``
        (или ``horoscopes``) в зависимости от ``content``.
    """
    flags = _flags()
    payload = {
        "date": day.isoformat(),
        "sign": sign,
    }
    if content != "horoscopes":
        payload["facts"] = [
            {"text": f.text, "category": f.category}
            for f in Fact.objects.filter(is_active=True).order_by("ordering", "id")
        ]
        if flags["shuffle_facts"]:
            random.shuffle(payload["facts"])

    if content == "facts":
        return payload

    if sign:
        horizon = (
            Horoscope.objects.filter(sign=sign, date=day, is_active=True, is_draft=False).first()
        )
        payload["horoscope"] = (
            {
                "text": horizon.text,
                "sign": horizon.sign,
                "date": horizon.date.isoformat(),
            }
            if horizon
            else None
        )
        payload.pop("horoscopes", None)
    else:
        rows = list(
            Horoscope.objects.filter(date=day, is_active=True, is_draft=False)
            .order_by("sign")
            .values("sign", "date", "text")
        )
        if flags["shuffle_horoscopes"]:
            random.shuffle(rows)
        payload["horoscopes"] = [
            {"text": r["text"], "sign": r["sign"], "date": r["date"].isoformat()}
            for r in rows
        ]
        payload.pop("horoscope", None)
    return payload


def get_daily_payload(
    sign: str | None,
    day: date,
    limit: int = FACT_LIMIT_DEFAULT,
    content: str = "all",
) -> dict:
    """Возвращает пакет дня, обрезанный до ``limit`` фактов.

    Если включён случайный режим — каждый запрос отдаёт новый порядок или
    выборку (лимит применяется к перемешанному списку, а не к кэшу).

    Args:
        sign: Код знака или ``None`` (все знаки на дату).
        day: Дата дня.
        limit: Максимальное количество фактов.
        content: Вариант контента: ``all``, ``facts`` или ``horoscopes``.

    Returns:
        Словарь-пакет (см. ``build_daily_payload``).
    """
    flags = _flags()
    random_mode = (
        (flags["shuffle_facts"] and content != "horoscopes")
        or (sign is None and flags["shuffle_horoscopes"] and content != "facts")
    )
    if random_mode:
        payload = build_daily_payload(sign, day, content)
    else:
        payload = cache.get(_payload_key(sign, day, content))
        if payload is None:
            payload = build_daily_payload(sign, day, content)
            cache.set(_payload_key(sign, day, content), payload, timeout=PAYLOAD_TTL)

    facts = payload.get("facts")
    if facts is None:
        return payload
    facts = list(facts)
    if flags["shuffle_facts"]:
        if limit < len(facts):
            facts = random.sample(facts, limit)
    else:
        facts = facts[:limit]
    return {**payload, "facts": facts}


def get_etag(payload: dict) -> str:
    """Вычисляет ETag для пакета.

    Args:
        payload: Словарь-пакет.

    Returns:
        MD5-хеш от канонического JSON-представления.
    """
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.md5(raw).hexdigest()


def invalidate_horoscope(sign: str, day: date) -> None:
    """Сбрасывает все варианты кэша, где может быть гороскоп.

    Args:
        sign: Код знака, который изменился.
        day: Дата, для которой изменился гороскоп.
    """
    for content in ("all", "horoscopes"):
        cache.delete(_payload_key(sign, day, content))
        cache.delete(_payload_key(None, day, content))


def invalidate_facts() -> None:
    """Меняет номер версии фактов — все пакеты пересчитываются заново."""
    version = cache.get("api:facts_version", 1)
    cache.set("api:facts_version", version + 1, timeout=None)


def invalidate_settings() -> None:
    """Сбрасывает кэш тумблеров показа."""
    cache.delete("api:site_flags")