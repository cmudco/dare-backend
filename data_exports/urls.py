from django.urls import path

from data_exports.api.views import AccountExportView, AccountRestoreView

app_name = "data_exports"

urlpatterns = [
    path(
        "api/account-data/export/", AccountExportView.as_view(), name="account-export"
    ),
    path(
        "api/account-data/restore/",
        AccountRestoreView.as_view(),
        name="account-restore",
    ),
]
