import json
from datetime import date
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from django.urls import reverse

from .models import ZODIAC_SIGNS, Fact, Horoscope, ImportLog, SiteSettings
from .services import ContentImporter

JSON_BODY = """{
  "horoscopes": [
    {"sign": "aries", "date": "2026-09-16", "text": "Тест Овен"},
    {"sign": "taurus", "date": "2026-09-16", "text": "Тест Телец"}
  ],
  "facts": [
    {"text": "Факт первый", "category": "science"},
    {"text": "Факт второй", "category": "animals", "ordering": 1}
  ]
}""".encode("utf-8")

XML_BODY = """<?xml version="1.0" encoding="UTF-8"?>
<content>
  <horoscope sign="cancer" date="2026-09-16">Текст Рака</horoscope>
  <fact category="history">Факт из XML</fact>
</content>""".encode("utf-8")

BAD_XML_BODY = b"<not_content><x/></not_content>"

DAILY_XML_BODY = """<?xml version="1.0" encoding="UTF-8"?>
<horoscope date="2026-09-16" day="среда">
  <sign name="Овен">
    <forecast> День подходит для быстрых решений. </forecast>
    <advice> Сначала делайте главное. </advice>
  </sign>
  <sign name="Телец">
    <forecast> Спокойный и практичный день. </forecast>
  </sign>
</horoscope>""".encode("utf-8")


def _xlsx_body(rows):
    """Возвращает байты .xlsx таблицы date|sign|forecast|advice для тестов."""
    from io import BytesIO

    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.append(["date", "sign", "forecast", "advice"])
    for row in rows:
        ws.append(row)
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


class ImportServiceTests(TestCase):
    def test_json_import_commit(self):
        importer = ContentImporter(JSON_BODY, "json")
        self.assertEqual(importer.preview_summary(), {"add": 4, "update": 0, "unchanged": 0, "error": 0})
        result = importer.commit()
        self.assertEqual((result.added, result.updated), (4, 0))
        self.assertEqual(Horoscope.objects.count(), 2)
        self.assertEqual(Fact.objects.count(), 2)
        log = ContentImporter.save_log("test.json", "json", result)
        self.assertEqual(log.report["added"], 4)

    def test_import_is_idempotent(self):
        importer = ContentImporter(JSON_BODY, "json")
        importer.commit()
        importer2 = ContentImporter(JSON_BODY, "json")
        self.assertEqual(importer2.preview_summary(), {"add": 0, "update": 0, "unchanged": 4, "error": 0})
        result2 = importer2.commit()
        self.assertEqual(result2.unchanged, 4)

    def test_import_updates_existing_horoscope(self):
        ContentImporter(JSON_BODY, "json").commit()
        new_body = JSON_BODY.replace("Тест Овен".encode(), "Новый текст Овна".encode())
        importer = ContentImporter(new_body, "json")
        self.assertEqual(importer.preview_summary()["update"], 1)
        result = importer.commit()
        self.assertEqual(result.updated, 1)
        self.assertEqual(
            Horoscope.objects.get(sign="aries", date="2026-09-16").text,
            "Новый текст Овна",
        )

    def test_json_accepts_singular_horoscope_and_prediction_fields(self):
        body = """{
          "date": "2026-09-21",
          "horoscope": [
            {"sign": "Овен", "prediction": "Отдыхайте.", "advice": "Не спешите."},
            {"sign": "Стрелец", "text": "Просто текст."}
          ],
          "facts": []
        }""".encode("utf-8")
        importer = ContentImporter(body, "json")
        self.assertEqual(importer.preview_summary()["add"], 2)
        result = importer.commit()
        self.assertEqual(result.added, 2)
        h = Horoscope.objects.get(date="2026-09-21", sign="sagittarius")
        self.assertEqual(h.text, "Просто текст.")
        h2 = Horoscope.objects.get(date="2026-09-21", sign="aries")
        self.assertIn("Отдыхайте.", h2.text)
        self.assertIn("Не спешите.", h2.text)

    def test_import_errors_for_bad_records(self):
        body = """{
          "horoscopes": [
            {"sign": "dragons", "date": "2026-09-16", "text": "x"},
            {"sign": "aries", "date": "недата", "text": "x"},
            {"sign": "aries", "date": "2026-09-16"}
          ],
          "facts": [{"category": "science"}, {"text": ""}]
        }""".encode("utf-8")
        importer = ContentImporter(body, "json")
        self.assertEqual(len(importer.errors), 5)
        self.assertFalse(importer.has_valid_records())
        summary = importer.preview_summary()
        self.assertEqual(summary, {"add": 0, "update": 0, "unchanged": 0, "error": 5})

    def test_xml_import(self):
        importer = ContentImporter(XML_BODY, "xml")
        self.assertEqual(importer.preview_summary()["add"], 2)
        result = importer.commit()
        self.assertEqual(result.added, 2)
        self.assertTrue(Fact.objects.filter(text="Факт из XML").exists())

    def test_bad_xml(self):
        from .services import ImportParseError

        with self.assertRaises(ImportParseError):
            ContentImporter(BAD_XML_BODY, "xml")

    def test_daily_sheet_xml(self):
        """Вариант B: <horoscope date><sign name><forecast>/<advice>."""
        importer = ContentImporter(DAILY_XML_BODY, "xml")
        self.assertEqual(importer.preview_summary()["add"], 2)
        result = importer.commit()
        self.assertEqual(result.added, 2)
        owen = Horoscope.objects.get(sign="aries", date="2026-09-16")
        self.assertIn("День подходит", owen.text)
        self.assertIn("Сначала делайте главное", owen.text)
        taurus = Horoscope.objects.get(sign="taurus", date="2026-09-16")
        self.assertEqual(taurus.text, "Спокойный и практичный день.")

    def test_cyrillic_sign_alias(self):
        body = '{"horoscopes": [{"sign": "Овен", "date": "2026-09-16", "text": "текст"}]}'.encode("utf-8")
        importer = ContentImporter(body, "json")
        self.assertEqual(importer.horoscopes[0]["sign"], "aries")

    def test_xlsx_import(self):
        """Копирайтерский формат: date|sign|forecast|advice."""
        body = _xlsx_body([("2026-09-16", "Овен", "Прогноз Овна", "Совет Овна")])
        importer = ContentImporter(body, "xlsx")
        self.assertEqual(importer.preview_summary()["add"], 1)
        result = importer.commit()
        self.assertEqual(result.added, 1)
        h = Horoscope.objects.get(sign="aries", date="2026-09-16")
        self.assertEqual(h.text, "Прогноз Овна\n\nСовет Овна")

    def test_xlsx_mark_draft(self):
        body = _xlsx_body([("2026-09-16", "Телец", "Текст тельца")])
        ContentImporter(body, "xlsx", mark_draft=True).commit()
        self.assertTrue(Horoscope.objects.get(sign="taurus").is_draft)

    def test_draft_untouched_on_update(self):
        """Переимпорт файла не снимает отметку «проверено» с существующих."""
        body = _xlsx_body([("2026-09-16", "Телец", "Текст тельца")])
        ContentImporter(body, "xlsx", mark_draft=True).commit()
        ContentImporter(body, "xlsx").commit()
        self.assertTrue(Horoscope.objects.get(sign="taurus").is_draft)

    def test_xlsx_wide_format(self):
        """Формат «широкая таблица»: знак | период | Гороскоп на DD.MM.YYYY | ..."""
        from io import BytesIO

        from openpyxl import Workbook

        wb = Workbook()
        ws = wb.active
        ws.title = "Гороскоп"
        ws.append(["Знак зодиака", "Период", "Гороскоп на 16.09.2026", "Гороскоп на 17.09.2026"])
        ws.append(["Овен", "21 марта — 19 апреля", "Текст Овна 16-го.", "Текст Овна 17-го."])
        ws.append(["Телец", "20 апреля — 20 мая", "Текст Тельца 16-го."])
        buf = BytesIO()
        wb.save(buf)
        importer = ContentImporter(buf.getvalue(), "xlsx")
        self.assertEqual(importer.preview_summary()["add"], 3)
        result = importer.commit()
        self.assertEqual(result.added, 3)
        self.assertEqual(Horoscope.objects.get(sign="aries", date="2026-09-16").text, "Текст Овна 16-го.")
        self.assertEqual(Horoscope.objects.get(sign="taurus", date="2026-09-16").text, "Текст Тельца 16-го.")
        self.assertFalse(Horoscope.objects.filter(sign="taurus", date="2026-09-17").exists())

    def test_xlsx_wide_format_with_russian_date_headers(self):
        """Широкая таблица принимает даты с русскими названиями месяцев."""
        from io import BytesIO

        from openpyxl import Workbook

        wb = Workbook()
        ws = wb.active
        ws.append(["Знак зодиака", "20 сентября 2026", "Гороскоп на 21 сентября 2026"])
        ws.append(["Овен", "Текст Овна 20-го.", "Текст Овна 21-го."])
        buf = BytesIO()
        wb.save(buf)

        importer = ContentImporter(buf.getvalue(), "xlsx")

        self.assertEqual(len(importer.horoscopes), 2)
        self.assertEqual(importer.horoscopes[0]["date"], date(2026, 9, 20))
        self.assertEqual(importer.horoscopes[1]["date"], date(2026, 9, 21))

    def test_xlsx_wide_format_with_excel_date_header(self):
        """Широкая таблица принимает настоящую Excel-дату в ячейке шапки."""
        from io import BytesIO

        from openpyxl import Workbook

        wb = Workbook()
        ws = wb.active
        ws.append(["Знак зодиака", date(2026, 9, 20)])
        ws.append(["Овен", "Текст Овна."])
        buf = BytesIO()
        wb.save(buf)

        importer = ContentImporter(buf.getvalue(), "xlsx")

        self.assertEqual(len(importer.horoscopes), 1)
        self.assertEqual(importer.horoscopes[0]["date"], date(2026, 9, 20))

    def test_xlsx_missing_advice_column_ok(self):
        body = _xlsx_body([("2026-09-16", "Близнецы", "Текст близнецов")])
        # переписываем шапку без advice
        from io import BytesIO

        from openpyxl import Workbook

        wb = Workbook()
        ws = wb.active
        ws.append(["date", "sign", "forecast"])
        ws.append(["2026-09-16", "Gemini", "Текст близнецов"])
        buf = BytesIO()
        wb.save(buf)
        importer = ContentImporter(buf.getvalue(), "xlsx")
        self.assertEqual(importer.preview_summary()["add"], 1)
        self.assertEqual(importer.commit().added, 1)

    def test_csv_import(self):
        body = "date;sign;forecast;advice\n2026-09-17;Лев;Гороскоп льва;Совет льва".encode("utf-8")
        importer = ContentImporter(body, "csv")
        self.assertEqual(importer.preview_summary()["add"], 1)
        result = importer.commit()
        self.assertEqual(result.added, 1)
        h = Horoscope.objects.get(sign="leo", date="2026-09-17")
        self.assertEqual(h.text, "Гороскоп льва\n\nСовет льва")

    def test_csv_with_utf8_bom(self):
        body = "\ufeffdate;sign;forecast\n2026-09-17;Дева;Гороскоп девы".encode("utf-8")
        importer = ContentImporter(body, "csv")
        self.assertEqual(importer.horoscopes[0]["sign"], "virgo")

    def test_table_missing_required_column(self):
        from .services import ImportParseError

        # Формат с признаком «широкая таблица» не подходит без дат в шапках
        from io import BytesIO

        from openpyxl import Workbook

        wb = Workbook()
        ws = wb.active
        ws.append(["Колонка А", "Колонка Б"])
        ws.append(["значение", "значение"])
        buf = BytesIO()
        wb.save(buf)
        with self.assertRaises(ImportParseError):
            ContentImporter(buf.getvalue(), "xlsx")


class MakeTemplateCommandTests(TestCase):
    def test_template_generated_for_month(self):
        import os
        import tempfile
        import zipfile

        from django.core.management import call_command

        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "t.xlsx")
            call_command("make_template", month="2026-10", output=out)
            self.assertTrue(os.path.exists(out))
            with zipfile.ZipFile(out) as z:
                self.assertIn("xl/workbook.xml", z.namelist())
            wb = __import__("openpyxl").load_workbook(out, data_only=True)
            ws = wb["Гороскопы"]
            rows = list(ws.iter_rows(values_only=True))
            self.assertEqual(rows[0], ("date", "sign", "forecast", "advice"))
            self.assertEqual(len(rows), 1 + 31 * 12)
            self.assertEqual(rows[1][0], "2026-10-01")
            self.assertEqual(rows[1][1], "Овен")
            self.assertIn("ТЗ (инструкция)", wb.sheetnames)

    def test_template_defaults_to_next_month(self):
        import os
        import tempfile
        from datetime import date

        from django.core.management import call_command

        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "d.xlsx")
            call_command("make_template", "--month", "2026-12", output=out)
            wb = __import__("openpyxl").load_workbook(out, data_only=True)
            rows = list(wb["Гороскопы"].iter_rows(values_only=True))
            self.assertEqual(len(rows), 1 + 31 * 12)


class ApiTests(TestCase):
    def setUp(self):
        Horoscope.objects.create(sign="aries", date=date(2026, 9, 16), text="Гороскоп Овна")
        Fact.objects.create(text="Факт 1", category="science", ordering=0)
        Fact.objects.create(text="Факт 2", category="animals", ordering=1)

    def test_json_ok(self):
        r = self.client.get("/api/today", {"sign": "aries", "date": "2026-09-16"})
        self.assertEqual(r.status_code, 200)
        data = json.loads(r.content)
        self.assertEqual(data["sign"], "aries")
        self.assertEqual(data["horoscope"]["text"], "Гороскоп Овна")
        self.assertEqual(
            data["horoscope"]["image_url"],
            "http://testserver/static/content/zodiac/aries.svg",
        )
        self.assertEqual(len(data["facts"]), 2)
        self.assertTrue(r["ETag"])

    def test_all_zodiac_images_exist(self):
        image_root = Path(settings.BASE_DIR) / "content" / "static" / "content" / "zodiac"
        for sign, _label in ZODIAC_SIGNS:
            self.assertTrue((image_root / f"{sign}.svg").is_file(), sign)

    def test_fact_limit(self):
        r = self.client.get("/api/today", {"sign": "aries", "date": "2026-09-16", "limit": 1})
        self.assertEqual(len(json.loads(r.content)["facts"]), 1)

    def test_content_facts_only(self):
        r = self.client.get("/api/today", {"sign": "aries", "date": "2026-09-16", "content": "facts"})
        data = json.loads(r.content)
        self.assertNotIn("horoscope", data)
        self.assertEqual(len(data["facts"]), 2)

    def test_content_horoscopes_only(self):
        r = self.client.get("/api/today", {"sign": "aries", "date": "2026-09-16", "content": "horoscopes"})
        data = json.loads(r.content)
        self.assertNotIn("facts", data)
        self.assertEqual(data["horoscope"]["text"], "Гороскоп Овна")

    def test_content_horoscopes_all_signs(self):
        Horoscope.objects.create(sign="taurus", date=date(2026, 9, 16), text="Телец")
        r = self.client.get("/api/today", {"date": "2026-09-16", "content": "horoscopes"})
        data = json.loads(r.content)
        self.assertNotIn("facts", data)
        self.assertEqual(len(data["horoscopes"]), 2)

    def test_content_invalid_is_400(self):
        r = self.client.get("/api/today", {"sign": "aries", "date": "2026-09-16", "content": "qq"})
        self.assertEqual(r.status_code, 400)

    def test_inactive_horoscope_hidden(self):
        Horoscope.objects.filter(sign="aries").update(is_active=False)
        r = self.client.get("/api/today", {"sign": "aries", "date": "2026-09-16"})
        self.assertIsNone(json.loads(r.content)["horoscope"])

    def test_draft_horoscope_hidden(self):
        Horoscope.objects.filter(sign="aries").update(is_draft=True)
        r = self.client.get("/api/today", {"sign": "aries", "date": "2026-09-16"})
        self.assertIsNone(json.loads(r.content)["horoscope"])
        r = self.client.get("/api/today", {"date": "2026-09-16"})
        self.assertEqual(json.loads(r.content)["horoscopes"], [])

    def test_inactive_facts_hidden(self):
        Fact.objects.filter(text="Факт 2").update(is_active=False)
        r = self.client.get("/api/today", {"sign": "aries", "date": "2026-09-16"})
        texts = [f["text"] for f in json.loads(r.content)["facts"]]
        self.assertNotIn("Факт 2", texts)

    def test_xml_format(self):
        r = self.client.get("/api/today", {"sign": "aries", "date": "2026-09-16", "format": "xml"})
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"<horoscope", r.content)
        self.assertIn(b'image_url="http://testserver/static/content/zodiac/aries.svg"', r.content)
        self.assertIn(b"<daily", r.content)

    def test_etag_304(self):
        etag = self.client.get("/api/today", {"sign": "aries", "date": "2026-09-16"})["ETag"]
        r = self.client.get("/api/today", {"sign": "aries", "date": "2026-09-16"}, HTTP_IF_NONE_MATCH=etag)
        self.assertEqual(r.status_code, 304)

    def test_missing_sign_returns_all_signs(self):
        """Запрос без sign возвращает все активные гороскопы на дату."""
        Horoscope.objects.create(sign="taurus", date=date(2026, 9, 16), text="Гороскоп Тельца")
        r = self.client.get("/api/today", {"date": "2026-09-16"})
        self.assertEqual(r.status_code, 200)
        data = json.loads(r.content)
        signs = {h["sign"] for h in data["horoscopes"]}
        self.assertEqual(signs, {"aries", "taurus"})
        self.assertNotIn("horoscope", data)

    def test_all_signs_xml(self):
        Horoscope.objects.create(sign="taurus", date=date(2026, 9, 16), text="Телец")
        r = self.client.get("/api/today", {"date": "2026-09-16", "format": "xml"})
        self.assertIn(b"<horoscope", r.content)
        self.assertEqual(r.content.count(b"<horoscope"), 2)

    def test_shuffle_facts_toggle(self):
        """При включённом тумблере limit возвращает случайную выборку нужного размера."""
        settings = SiteSettings.load()
        settings.shuffle_facts = True
        settings.save()
        all_texts = {f.text for f in Fact.objects.all()}
        for _ in range(5):
            r = self.client.get("/api/today", {"sign": "aries", "date": "2026-09-16", "limit": 1})
            facts = json.loads(r.content)["facts"]
            self.assertEqual(len(facts), 1)
            self.assertLessEqual(set(f["text"] for f in facts), all_texts)

    def test_shuffle_horoscopes_toggle(self):
        settings = SiteSettings.load()
        settings.shuffle_horoscopes = True
        settings.save()
        r = self.client.get("/api/today", {"date": "2026-09-16"})
        data = json.loads(r.content)
        signs = [h["sign"] for h in data["horoscopes"]]
        self.assertEqual(set(signs), {"aries"})
        self.assertEqual(len(signs), 1)

    def test_invalid_date_is_400(self):
        r = self.client.get("/api/today", {"sign": "aries", "date": "16.09.2026"})
        self.assertEqual(r.status_code, 400)

    def test_unknown_sign_is_400(self):
        r = self.client.get("/api/today", {"sign": "no-such", "date": "2026-09-16"})
        self.assertEqual(r.status_code, 400)

    def test_horoscope_endpoint_with_sign(self):
        r = self.client.get("/api/horoscope", {"sign": "aries", "date": "2026-09-16"})
        self.assertEqual(r.status_code, 200)
        data = json.loads(r.content)
        self.assertEqual(data["horoscope"]["text"], "Гороскоп Овна")
        self.assertNotIn("facts", data)
        self.assertNotIn("horoscopes", data)

    def test_horoscope_endpoint_all_signs(self):
        Horoscope.objects.create(sign="taurus", date=date(2026, 9, 16), text="Телец")
        r = self.client.get("/api/horoscope", {"date": "2026-09-16"})
        data = json.loads(r.content)
        self.assertNotIn("facts", data)
        self.assertEqual({h["sign"] for h in data["horoscopes"]}, {"aries", "taurus"})

    def test_horoscope_endpoint_unknown_sign_is_400(self):
        r = self.client.get("/api/horoscope", {"sign": "no-such"})
        self.assertEqual(r.status_code, 400)

    def test_facts_endpoint(self):
        r = self.client.get("/api/facts", {"date": "2026-09-16", "limit": 1})
        self.assertEqual(r.status_code, 200)
        data = json.loads(r.content)
        self.assertEqual(len(data["facts"]), 1)
        self.assertNotIn("horoscope", data)
        self.assertNotIn("horoscopes", data)

    def test_facts_endpoint_xml(self):
        r = self.client.get("/api/facts", {"date": "2026-09-16", "format": "xml"})
        self.assertIn(b"<fact", r.content)
        self.assertNotIn(b"<horoscope", r.content)

    def test_horoscope_cache_invalidated_for_horoscopes_content(self):
        self.client.get("/api/horoscope", {"sign": "aries", "date": "2026-09-16"})
        h = Horoscope.objects.get(sign="aries", date="2026-09-16")
        h.text = "Обновлённый текст"
        h.save()
        r = self.client.get("/api/horoscope", {"sign": "aries", "date": "2026-09-16"})
        self.assertEqual(json.loads(r.content)["horoscope"]["text"], "Обновлённый текст")

    def test_cache_invalidated_on_horoscope_save(self):
        self.client.get("/api/today", {"sign": "aries", "date": "2026-09-16"})
        h = Horoscope.objects.get(sign="aries", date="2026-09-16")
        h.text = "Обновлённый текст"
        h.save()
        r = self.client.get("/api/today", {"sign": "aries", "date": "2026-09-16"})
        self.assertEqual(json.loads(r.content)["horoscope"]["text"], "Обновлённый текст")

    def test_cache_invalidated_on_fact_change(self):
        Fact.objects.create(text="Новый факт", category="x", ordering=3)
        r = self.client.get("/api/today", {"sign": "aries", "date": "2026-09-16"})
        self.assertIn("Новый факт", [f["text"] for f in json.loads(r.content)["facts"]])


class AdminImportTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_superuser("admin", "a@b.c", "pass")
        self.client = Client()
        self.client.force_login(user)

    def test_import_preview_and_confirm(self):
        url = reverse("admin:content_import")
        r = self.client.post(url, {"action": "preview", "file": SimpleUploadedFile("x.json", JSON_BODY)})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Предпросмотр x.json")
        self.assertContains(r, "добавить")

        file_path = r.context["file_path"]
        r2 = self.client.post(url, {"action": "confirm", "file_path": file_path})
        self.assertEqual(r2.status_code, 302)
        self.assertEqual(Horoscope.objects.count(), 2)
        self.assertEqual(Fact.objects.count(), 2)
        self.assertTrue(ImportLog.objects.exists())

    def test_import_rejects_bad_format(self):
        url = reverse("admin:content_import")
        r = self.client.post(url, {"action": "preview", "file": SimpleUploadedFile("x.txt", b"data")})
        self.assertContains(r, "Поддерживаются только файлы .json, .xml, .xlsx, .csv")

    def test_import_xlsx_confirm_marks_draft(self):
        url = reverse("admin:content_import")
        body = _xlsx_body([("2026-09-16", "Овен", "Прогноз Овна", "Совет Овна")])
        r = self.client.post(url, {"action": "preview", "file": SimpleUploadedFile("t.xlsx", body)})
        self.assertEqual(r.status_code, 200)
        r2 = self.client.post(url, {"action": "confirm", "file_path": r.context["file_path"]})
        self.assertEqual(r2.status_code, 302)
        h = Horoscope.objects.get(sign="aries")
        self.assertTrue(h.is_draft)
        self.assertTrue(ImportLog.objects.get(format="xlsx"))

    def test_accept_drafts_action(self):
        h = Horoscope.objects.create(sign="aries", date="2026-09-16", text="текст", is_draft=True)
        r = self.client.post(
            reverse("admin:content_horoscope_changelist"),
            {"action": "accept_drafts", "_selected_action": [h.pk]},
        )
        self.assertEqual(r.status_code, 302)
        h.refresh_from_db()
        self.assertFalse(h.is_draft)

    def test_return_to_draft_action(self):
        h = Horoscope.objects.create(sign="aries", date="2026-09-16", text="текст")
        r = self.client.post(
            reverse("admin:content_horoscope_changelist"),
            {"action": "return_to_draft", "_selected_action": [h.pk]},
        )
        self.assertEqual(r.status_code, 302)
        h.refresh_from_db()
        self.assertTrue(h.is_draft)

    def test_form_has_char_count_widget(self):
        for name in ("content_horoscope_add", "content_fact_add"):
            r = self.client.get(reverse(f"admin:{name}"))
            self.assertEqual(r.status_code, 200)
            self.assertContains(r, "data-charcount")
            self.assertContains(r, "admin/js/char_counter.js")

    def test_fact_list_shows_character_count_after_text(self):
        fact = Fact.objects.create(text="Факт из 16 знаков", category="science")
        r = self.client.get(reverse("admin:content_fact_changelist"))

        self.assertEqual(r.status_code, 200)
        self.assertContains(r, '<th scope="col" class="column-character_count">', html=False)
        self.assertContains(r, "Символов")
        self.assertContains(
            r,
            f'<td class="field-character_count">{len(fact.text)}</td>',
            html=False,
        )
        self.assertLess(r.content.index(b"column-text_short"), r.content.index(b"column-character_count"))
        self.assertLess(r.content.index(b"column-character_count"), r.content.index(b"column-category"))

    def test_horoscope_list_shows_character_count_after_text(self):
        horoscope = Horoscope.objects.create(
            sign="aries",
            date="2026-09-18",
            text="Прогноз для Овна",
        )
        r = self.client.get(reverse("admin:content_horoscope_changelist"))

        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Символов")
        self.assertContains(
            r,
            f'<td class="field-character_count">{len(horoscope.text)}</td>',
            html=False,
        )
        self.assertLess(r.content.index(b"column-text_short"), r.content.index(b"column-character_count"))
        self.assertLess(r.content.index(b"column-character_count"), r.content.index(b"column-is_draft"))

    def test_shuffle_facts_action_reverses_order(self):
        from unittest.mock import patch

        f1 = Fact.objects.create(text="Факт A", ordering=0)
        f2 = Fact.objects.create(text="Факт B", ordering=1)

        def arr_reverse(lst):
            lst.reverse()
            return None

        with patch("content.admin._shuffle", side_effect=arr_reverse):
            r = self.client.post(
                reverse("admin:content_fact_changelist"),
                {"action": "shuffle_facts_action", "_selected_action": [f1.pk, f2.pk]},
            )
        self.assertEqual(r.status_code, 302)
        f1.refresh_from_db()
        f2.refresh_from_db()
        self.assertLess(f2.ordering, f1.ordering)

    def test_shuffle_horoscope_texts_action_preserves_set(self):
        h1 = Horoscope.objects.create(sign="cancer", date="2026-09-16", text="Текст один")
        h2 = Horoscope.objects.create(sign="leo", date="2026-09-16", text="Текст два")
        texts_before = {h1.text, h2.text}
        r = self.client.post(
            reverse("admin:content_horoscope_changelist"),
            {"action": "shuffle_horooscopes_action", "_selected_action": [h1.pk, h2.pk]},
        )
        self.assertEqual(r.status_code, 302)
        h1.refresh_from_db()
        h2.refresh_from_db()
        self.assertEqual({h1.text, h2.text}, texts_before)
        self.assertNotEqual(h1.text, texts_before)
