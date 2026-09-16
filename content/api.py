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


def _payload_key(sign: str, day) -> str:
    day_str = day.isoformat() if hasattr(day, "isoformat") else str(day)
    return f"api:today:{CACHE_VERSION}:v{_facts_version()}:{sign or 'all'}:{day_str}"


def _flags() -> dict:
    flags = cache.get("api:site_flags")
    if flags is None:
        s = SiteSettings.load()
        flags = {"shuffle_facts": s.shuffle_facts, "shuffle_horoscopes": s.shuffle_horoscopes}
        cache.set("api:site_flags", flags, timeout=FLAGS_TTL)
    return flags


def build_daily_payload(sign: str | None, day: date) -> dict:
    """Пакет для экрана. sign=None — все активные знаки на дату.

    Случайный порядок (факты и список знаков) применяется здесь, поэтому при
    включённых флагах результат не берётся из кэша.
    """
    flags = _flags()
    payload = {
        "date": day.isoformat(),
        "sign": sign,
        "facts": [
            {"text": f.text, "category": f.category}
            for f in Fact.objects.filter(is_active=True).order_by("ordering", "id")
        ],
    }
    if flags["shuffle_facts"]:
        random.shuffle(payload["facts"])

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
        payload.pop("horooscopes", None)
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


def get_daily_payload(sign: str | None, day: date, limit: int = FACT_LIMIT_DEFAULT) -> dict:
    """Пакет, обрезанный до `limit` фактов.

    Если включён случайный режим — каждый запрос отдаёт новый порядок/выборку
    (лимит фактов применяется к перемешанному списку, а не к кэшу).
    """
    flags = _flags()
    random_mode = (
        flags["shuffle_facts"] or (sign is None and flags["shuffle_horoscopes"])
    )
    if random_mode:
        payload = build_daily_payload(sign, day)
    else:
        payload = cache.get(_payload_key(sign, day))
        if payload is None:
            payload = build_daily_payload(sign, day)
            cache.set(_payload_key(sign, day), payload, timeout=PAYLOAD_TTL)

    facts = payload["facts"]
    if flags["shuffle_facts"]:
        if limit < len(facts):
            facts = random.sample(facts, limit)
    else:
        facts = facts[:limit]
    return {**payload, "facts": facts}


def get_etag(payload: dict) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.md5(raw).hexdigest()


def invalidate_horoscope(sign: str, day: date) -> None:
    cache.delete(_payload_key(sign, day))
    cache.delete(_payload_key(None, day))


def invalidate_facts() -> None:
    """Меняет номер версии фактов — все пакеты пересчитываются заново."""
    version = cache.get("api:facts_version", 1)
    cache.set("api:facts_version", version + 1, timeout=None)


def invalidate_settings() -> None:
    cache.delete("api:site_flags")