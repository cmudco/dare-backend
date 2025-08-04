from django.urls import path, include

app_name = 'notifications'

urlpatterns = [
    path('api/', include('notifications.api.urls')),
]