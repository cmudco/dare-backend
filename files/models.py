from django.db import models
from django.conf import settings

from common.models import BaseModel

class File(BaseModel):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='files')
    file = models.FileField(upload_to='files/')
    name = models.CharField(max_length=255, blank=True, null=True)
    file_type = models.CharField(max_length=50, blank=True, null=True)
    size = models.PositiveIntegerField(null=True, blank=True)
    tags = models.JSONField(default=list, blank=True)

    def __str__(self):
        return self.name if self.name else self.file.name