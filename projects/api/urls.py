from rest_framework.routers import DefaultRouter

from projects.api.views import PersonalProjectViewSet

router = DefaultRouter()
router.register("projects", PersonalProjectViewSet, basename="personal-project")

urlpatterns = router.urls
