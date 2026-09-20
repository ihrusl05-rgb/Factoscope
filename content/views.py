"""Публичный API для экранов adreality."""

from __future__ import annotations

from datetime import date
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

from django.http import HttpResponse, JsonResponse
from django.templatetags.static import static
from django.utils import timezone
from django.views import View

from .api import get_daily_payload, get_etag
from .models import FACT_LIMIT_DEFAULT, normalize_sign

MAX_FACT_LIMIT = 100


def _horoscope_with_image(request, horoscope: dict) -> dict:
    image_path = static(f"content/zodiac/{horoscope['sign']}.svg")
    return {**horoscope, "image_url": request.build_absolute_uri(image_path)}


def _add_horoscope_images(request, payload: dict) -> dict:
    result = {**payload}
    if result.get("horoscope") is not None:
        result["horoscope"] = _horoscope_with_image(request, result["horoscope"])
    if "horoscopes" in result:
        result["horoscopes"] = [
            _horoscope_with_image(request, horoscope)
            for horoscope in result["horoscopes"]
        ]
    return result


def _payload_to_xml(payload: dict) -> str:
    root = ET.Element(
        "daily",
        {"date": payload["date"], "sign": payload["sign"] or ""},
    )
    if "horoscope" in payload:
        horizon = payload["horoscope"]
        if horizon is not None:
            el = ET.SubElement(root, "horoscope")
            el.set("date", horizon["date"])
            el.set("sign", horizon["sign"])
            el.set("image_url", horizon["image_url"])
            el.text = escape(horizon["text"] or "")
    elif "horoscopes" in payload:
        for horizon in payload["horoscopes"]:
            el = ET.SubElement(root, "horoscope")
            el.set("date", horizon["date"])
            el.set("sign", horizon["sign"])
            el.set("image_url", horizon["image_url"])
            el.text = escape(horizon["text"] or "")
    for fact in payload.get("facts", []):
        el = ET.SubElement(root, "fact")
        if fact["category"]:
            el.set("category", fact["category"])
        el.text = escape(fact["text"] or "")
    return ET.tostring(root, encoding="unicode", xml_declaration=True)


def _parse_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value)
    except (ValueError, TypeError):
        return None


def _error(message: str) -> HttpResponse:
    return HttpResponse(
        '{"error": "%s"}' % message, content_type="application/json", status=400
    )


def _get_sign(request) -> tuple[str | None, HttpResponse | None]:
    raw_sign = request.GET.get("sign", "").strip()
    if not raw_sign:
        return None, None
    sign = normalize_sign(raw_sign)
    if not sign:
        return None, _error("unknown sign")
    return sign, None


def _get_day(request) -> tuple[date | None, HttpResponse | None]:
    raw_date = request.GET.get("date") or timezone.localdate().isoformat()
    day = _parse_date(raw_date)
    if day is None:
        return None, _error("invalid date, use ISO-8601 (YYYY-MM-DD)")
    return day, None


def _get_limit(request) -> int:
    try:
        return max(0, min(MAX_FACT_LIMIT, int(request.GET.get("limit", FACT_LIMIT_DEFAULT))))
    except (TypeError, ValueError):
        return FACT_LIMIT_DEFAULT


def _render(request, payload: dict) -> HttpResponse:
    payload = _add_horoscope_images(request, payload)
    fmt = (request.GET.get("format") or "json").lower()
    etag = get_etag(payload)

    if_none_match = request.headers.get("If-None-Match")
    if if_none_match and if_none_match.strip('"') == etag:
        return HttpResponse(status=304)

    if fmt == "xml":
        response = HttpResponse(
            _payload_to_xml(payload), content_type="application/xml; charset=utf-8"
        )
    else:
        response = JsonResponse(payload)
    response["ETag"] = f'"{etag}"'
    response["Cache-Control"] = "no-cache"
    return response


class TodayAPIView(View):
    """GET /api/today?sign=aries&date=2026-09-16&limit=5&format=json|xml

    Совмещённый пакет: факты + гороскоп(ы). content=all (по умолчанию) | facts |
    horoscopes — вернуть только часть. Без `sign` возвращает все активные знаки
    на дату (в случайном порядке, если включён тумблер в админке).

    Для раздельной выдачи используйте /api/horoscope и /api/facts.
    """

    def get(self, request):
        sign, err = _get_sign(request)
        if err:
            return err
        day, err = _get_day(request)
        if err:
            return err

        content = (request.GET.get("content") or "all").lower()
        if content not in ("all", "facts", "horoscopes"):
            return _error("content must be one of: all, facts, horoscopes")

        payload = get_daily_payload(sign, day, _get_limit(request), content)
        return _render(request, payload)


class HoroscopeAPIView(View):
    """GET /api/horoscope?sign=aries&date=2026-09-16&format=json|xml

    Со `sign` — гороскоп этого знака (`horoscope`, может быть null).
    Без `sign` — все активные знаки на дату (`horoscopes`).
    """

    def get(self, request):
        sign, err = _get_sign(request)
        if err:
            return err
        day, err = _get_day(request)
        if err:
            return err

        payload = get_daily_payload(sign, day, content="horoscopes")
        return _render(request, payload)


class FactsAPIView(View):
    """GET /api/facts?date=2026-09-16&limit=5&format=json|xml

    Только факты (общие на день, без привязки к знаку).
    """

    def get(self, request):
        day, err = _get_day(request)
        if err:
            return err

        payload = get_daily_payload(None, day, _get_limit(request), content="facts")
        return _render(request, payload)
