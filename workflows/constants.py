from django.db import models

APP_NAME = "workflows"

class Mode(models.IntegerChoices):
    SERIAL = 1, "Serial"
    PARALLEL = 2, "Parallel"
