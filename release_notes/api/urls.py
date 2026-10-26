from rest_framework.routers import DefaultRouter

from release_notes.api.views import ReleaseNoteViewSet

router = DefaultRouter()
router.register("release-notes", ReleaseNoteViewSet, basename="release-note")

urlpatterns = router.urls
