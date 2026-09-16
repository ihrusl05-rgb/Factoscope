"""Инвалидация кэша API при изменении контента."""

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from . import api
from .models import Fact, Horoscope, SiteSettings


@receiver([post_save, post_delete], sender=Horoscope)
def on_horoscope_change(sender, instance, **kwargs):
    api.invalidate_horoscope(instance.sign, instance.date)


@receiver([post_save, post_delete], sender=Fact)
def on_fact_change(sender, instance, **kwargs):
    api.invalidate_facts()


@receiver([post_save, post_delete], sender=SiteSettings)
def on_settings_change(sender, instance, **kwargs):
    api.invalidate_settings()