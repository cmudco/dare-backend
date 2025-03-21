from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from django.http import HttpResponse

def empty_view(request):
    return HttpResponse('')

admin_paths = [
    path("admin/", admin.site.urls),
]

app_paths = [
    path("users/", include("users.urls"), name="users"),
    path("account/", include("allauth.account.urls")),
    path("", include("files.urls", namespace="files")),
    path("", include("conversations.urls", namespace="conversations")),
    path("", include("prompts.urls", namespace="prompts")),
    path("", include("workflows.urls", namespace="workflows")),

]

other_paths = [
    path('password-reset/<str:uidb64>/<str:token>/', empty_view, name='password_reset_confirm'),
]

urlpatterns = admin_paths + app_paths + other_paths

# Use the settings object to get the MEDIA_ROOT and MEDIA_URL
urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
# TODO: Yet to be implemented
# handler404 = Error404View.as_view()
# handler500 = Error500View.as_view()
# handler403 = Error403View.as_view()
# handler400 = Error400View.as_view()

admin.site.site_title = "DARE Administration"
admin.site.site_header = "DARE Administration"
admin.site.index_title = "DARE Administration"
