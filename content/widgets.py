"""Виджеты для форм админки.

Виджеты подключают статику и JS к API-контенту (счётчик символов под полем).
"""

from django.forms import Textarea


class CharCountTextarea(Textarea):
    """Textarea с живым счётчиком символов под полем.

    Наличие атрибута ``data-charcount="1"`` включает JS-счётчик
    (``content/static/admin/js/char_counter.js``).

    Args:
        attrs: Дополнительные HTML-атрибуты для ``<textarea>``
            (перекрывают значения по умолчанию).
    """

    class Media:
        js = ("admin/js/char_counter.js",)

    def __init__(self, attrs=None):
        final_attrs = {"rows": 5, "data-charcount": "1"}
        if attrs:
            final_attrs.update(attrs)
        super().__init__(final_attrs)