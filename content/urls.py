from django.urls import path

from . import views

app_name = "content"

urlpatterns = [
    path("today", views.TodayAPIView.as_view(), name="today"),
]