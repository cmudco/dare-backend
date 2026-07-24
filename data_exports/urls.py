from django.urls import path

from data_exports.api.views import DataExportDownloadView

app_name = "data_exports"

urlpatterns = [
    path(
        "api/data-exports/download/",
        DataExportDownloadView.as_view(),
        name="data-export-download",
    ),
]
