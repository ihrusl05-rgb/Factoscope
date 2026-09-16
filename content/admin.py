"""Дженерик-админка: CRUD, вкл/выкл, импорт контента (XML/JSON)."""

import os
import random
import uuid

from django.conf import settings
from django.contrib import admin, messages
from django.core.files.uploadedfile import UploadedFile
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import path, reverse
from django.utils import timezone

from .models import ZODIAC_SIGNS, Fact, Horoscope, ImportLog, SiteSettings
from .services import ContentImporter, ImportParseError

IMPORT_TMP_ROOT = os.path.join(settings.MEDIA_ROOT, "import_tmp")

ZODIAC_SIGN_LABELS = dict(ZODIAC_SIGNS)


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------

def _toggle_active(modeladmin, request, queryset, value: bool):
    if queryset.model is Horoscope:
        from .api import invalidate_horoscope

        for obj in queryset:
            invalidate_horoscope(obj.sign, obj.date)
    elif queryset.model is Fact:
        from .api import invalidate_facts

        invalidate_facts()
    count = queryset.update(is_active=value)
    state = "показывать" if value else "скрыть"
    modeladmin.message_user(request, f"Отмечено {count} записей: {state}.")


@admin.action(description="Показывать выделенные")
def make_active(modeladmin, request, queryset):
    _toggle_active(modeladmin, request, queryset, True)


@admin.action(description="Скрыть выделенные")
def make_inactive(modeladmin, request, queryset):
    _toggle_active(modeladmin, request, queryset, False)


def _shuffle(seq):
    random.shuffle(seq)


@admin.action(description="Перемешать порядок фактов (случайно)")
def shuffle_facts_action(modeladmin, request, queryset):
    """Разбрасывает `ordering` — следующий показ пойдёт в случайном порядке."""
    from .api import invalidate_facts

    ids = list(queryset.order_by("id").values_list("id", flat=True))
    order = list(range(len(ids)))
    _shuffle(order)
    for pk, ordering in zip(ids, order):
        Fact.objects.filter(pk=pk).update(ordering=ordering)
    invalidate_facts()
    modeladmin.message_user(request, f"Порядок перемешан: {len(ids)} фактов.")


@admin.action(description="Перемешать тексты между знаками (случайно)")
def shuffle_horooscopes_action(modeladmin, request, queryset):
    """Перемешивает тексты гороскопов внутри каждой даты выбранных знаков."""
    from .api import invalidate_horoscope

    groups: dict = {}
    for obj in queryset:
        groups.setdefault(obj.date, []).append(obj)
    moved = 0
    for day, objs in groups.items():
        texts = [o.text for o in objs]
        _shuffle(texts)
        for obj, new_text in zip(objs, texts):
            if obj.text != new_text:
                Horoscope.objects.filter(pk=obj.pk).update(text=new_text)
                invalidate_horoscope(obj.sign, day)
                moved += 1
    modeladmin.message_user(
        request, f"Тексты перемешаны: изменено {moved} из {len(queryset)} гороскопов."
    )


@admin.action(description="Принять из черновика (проверено)")
def accept_drafts(modeladmin, request, queryset):
    from .api import invalidate_horoscope

    updated = 0
    for obj in queryset.filter(is_draft=True):
        Horoscope.objects.filter(pk=obj.pk).update(is_draft=False)
        invalidate_horoscope(obj.sign, obj.date)
        updated += 1
    modeladmin.message_user(request, f"Принято из черновика: {updated} записей.")


@admin.action(description="Вернуть в черновик")
def return_to_draft(modeladmin, request, queryset):
    from .api import invalidate_horoscope

    updated = queryset.update(is_draft=True)
    for obj in queryset:
        invalidate_horoscope(obj.sign, obj.date)
    modeladmin.message_user(request, f"Возвращено в черновик: {updated} записей.")


# ---------------------------------------------------------------------------
# ModelAdmin
# ---------------------------------------------------------------------------

@admin.register(Horoscope)
class HoroscopeAdmin(admin.ModelAdmin):
    list_display = ("date", "sign", "text_short", "is_draft", "is_active")
    list_editable = ("is_active",)
    list_filter = ("sign", "is_active", "is_draft", "date")
    search_fields = ("text",)
    date_hierarchy = "date"
    list_per_page = 200
    actions = [make_active, make_inactive, accept_drafts, return_to_draft, shuffle_horooscopes_action]
    ordering = ("-date", "sign")

    @admin.display(description="Текст")
    def text_short(self, obj):
        return obj.text[:70]


@admin.register(Fact)
class FactAdmin(admin.ModelAdmin):
    list_display = ("text_short", "category", "ordering", "is_active")
    list_editable = ("ordering", "is_active")
    list_filter = ("category", "is_active")
    search_fields = ("text",)
    list_per_page = 200
    actions = [make_active, make_inactive, shuffle_facts_action]
    ordering = ("ordering", "id")

    @admin.display(description="Текст")
    def text_short(self, obj):
        return obj.text[:70]


@admin.register(ImportLog)
class ImportLogAdmin(admin.ModelAdmin):
    list_display = ("filename", "format", "status", "report_preview", "created_at")
    list_filter = ("status", "format")
    readonly_fields = ("filename", "format", "status", "report", "created_at")

    @admin.display(description="Отчёт")
    def report_preview(self, obj):
        r = obj.report
        return (
            f"добавлено {r.get('added', 0)}, "
            f"обновлено {r.get('updated', 0)}, "
            f"без изменений {r.get('unchanged', 0)}, "
            f"ошибок {len(r.get('errors', []))}"
        )


@admin.register(SiteSettings)
class SiteSettingsAdmin(admin.ModelAdmin):
    """Синглтон-настройки показа: тумблеры случайного порядка в API."""

    def has_add_permission(self, request):
        return not SiteSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        SiteSettings.load()
        return super().changelist_view(request, extra_context=extra_context)


# ---------------------------------------------------------------------------
# Импорт контента
# ---------------------------------------------------------------------------

def _save_tmp_file(upload: UploadedFile) -> str:
    os.makedirs(IMPORT_TMP_ROOT, exist_ok=True)
    base, ext = os.path.splitext(upload.name or "")
    name = f"{uuid.uuid4().hex}{ext or '.dat'}"
    path = os.path.join(IMPORT_TMP_ROOT, name)
    with open(path, "wb") as fh:
        for chunk in upload.chunks():
            fh.write(chunk)
    return path


def _format_from_path(path: str) -> str | None:
    return {"xml": "xml", "json": "json", "xlsx": "xlsx", "csv": "csv"}.get(
        os.path.splitext(path)[1].strip(".").lower()
    )


def _cleanup_tmp() -> None:
    if not os.path.isdir(IMPORT_TMP_ROOT):
        return
    for name in os.listdir(IMPORT_TMP_ROOT):
        path = os.path.join(IMPORT_TMP_ROOT, name)
        try:
            if timezone.now().timestamp() - os.path.getmtime(path) > 3600:
                os.remove(path)
        except OSError:
            pass


def import_content_view(request):
    context = {
        "title": "Импорт контента",
        "preview": None,
        "summary": None,
        "filename": None,
        "file_path": None,
        "fmt": None,
        "error": None,
        "sign_labels": ZODIAC_SIGN_LABELS,
    }
    _cleanup_tmp()

    if request.method == "POST":
        action = request.POST.get("action")
        file_path = request.POST.get("file_path")

        if action == "preview" and request.FILES.get("file"):
            upload = request.FILES["file"]
            fmt = _format_from_path(upload.name)
            if fmt is None:
                context["error"] = "Поддерживаются только файлы .json, .xml, .xlsx, .csv"
            else:
                tmp = _save_tmp_file(upload)
                try:
                    with open(tmp, "rb") as fh:
                        importer = ContentImporter(fh.read(), fmt)
                except ImportParseError as exc:
                    context["error"] = str(exc)
                    os.remove(tmp)
                else:
                    context["preview"] = importer.preview_rows()
                    context["summary"] = importer.preview_summary()
                    context["filename"] = os.path.basename(upload.name)
                    context["file_path"] = tmp
                    context["fmt"] = fmt
                    if not importer.has_valid_records():
                        context["error"] = "В файле нет валидных записей"

        elif action == "confirm" and file_path and os.path.exists(file_path):
            fmt = _format_from_path(file_path)
            mark_draft = fmt in ("xlsx", "csv")
            with open(file_path, "rb") as fh:
                importer = ContentImporter(fh.read(), fmt, mark_draft=mark_draft)
            result = importer.commit()
            ContentImporter.save_log(os.path.basename(file_path), fmt, result)
            os.remove(file_path)
            messages.success(
                request,
                f"Импортировано: добавлено {result.added}, обновлено {result.updated}, "
                f"без изменений {result.unchanged}, ошибок {len(result.errors)}.",
            )
            for err in result.errors[:20]:
                messages.warning(request, err)
            return HttpResponseRedirect(reverse("admin:content_import"))

    return render(request, "admin/content/import.html", context)


# ---------------------------------------------------------------------------
# Патчинг AdminSite: добавляем страницу импортa к стандартной админке
# ---------------------------------------------------------------------------

_original_get_urls = admin.site.get_urls


def _patched_get_urls():
    urls = _original_get_urls()
    urls.insert(0, path("content/import/", import_content_view, name="content_import"))
    return urls


admin.site.get_urls = _patched_get_urls


_original_get_app_list = admin.site.get_app_list


def _patched_get_app_list(request):
    """Вставка ссылки «Импорт контента» в раздел «content» индекса."""
    app_list = _original_get_app_list(request)
    for app in app_list:
        if app["app_label"] == "content":
            app["models"].insert(
                0,
                {
                    "name": "Импорт контента (XML/JSON/Excel)",
                    "object_name": "ImportContent",
                    "perms": {"add": True, "change": True, "delete": False},
                    "admin_url": reverse("admin:content_import"),
                    "view_only": True,
                },
            )
    return app_list


admin.site.get_app_list = _patched_get_app_list