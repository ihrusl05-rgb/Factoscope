"""Публичный API для экранов adreality."""

from __future__ import annotations

from datetime import date
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

from django.http import HttpResponse
from django.utils import timezone
from django.views import View

from .api import get_daily_payload, get_etag
from .models import FACT_LIMIT_DEFAULT, normalize_sign

MAX_FACT_LIMIT = 100


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
            el.text = escape(horizon["text"] or "")
    else:
        for horizon in payload["horoscopes"]:
            el = ET.SubElement(root, "horoscope")
            el.set("date", horizon["date"])
            el.set("sign", horizon["sign"])
            el.text = escape(horizon["text"] or "")
    for fact in payload["facts"]:
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


class TodayAPIView(View):
    """GET /api/today?sign=aries&date=2026-09-16&limit=5&format=json|xml

    Без `sign` возвращает все активные знаки на дату
    (в случайном порядке, если включён тумблер в админке).
    """

    def get(self, request):
        raw_sign = request.GET.get("sign", "").strip()
        sign = normalize_sign(raw_sign) if raw_sign else None
        if raw_sign and not sign:
            return HttpResponse(
                '{"error": "unknown sign"}', content_type="application/json", status=400
            )

        raw_date = request.GET.get("date") or timezone.localdate().isoformat()
        day = _parse_date(raw_date)
        if day is None:
            return HttpResponse(
                '{"error": "invalid date, use ISO-8601 (YYYY-MM-DD)"}',
                content_type="application/json",
                status=400,
            )

        try:
            limit = max(0, min(MAX_FACT_LIMIT, int(request.GET.get("limit", FACT_LIMIT_DEFAULT))))
        except (TypeError, ValueError):
            limit = FACT_LIMIT_DEFAULT

        payload = get_daily_payload(sign, day, limit)
        fmt = (request.GET.get("format") or "json").lower()
        etag = get_etag(payload)

        if_none_match = request.headers.get("If-None-Match")
        if if_none_match and if_none_match.strip('"') == etag:
            return HttpResponse(status=304)

        if fmt == "xml":
            body = _payload_to_xml(payload)
            response = HttpResponse(body, content_type="application/xml; charset=utf-8")
        else:
            from django.http import JsonResponse

            response = JsonResponse(payload)
        response["ETag"] = f'"{etag}"'
        response["Cache-Control"] = "no-cache"
        return response