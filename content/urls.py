from django.urls import path

from . import views

app_name = "content"

urlpatterns = [
    path("today", views.TodayAPIView.as_view(), name="today"),
    path("horoscope", views.HoroscopeAPIView.as_view(), name="horoscope"),
    path("facts", views.FactsAPIView.as_view(), name="facts"),
]
