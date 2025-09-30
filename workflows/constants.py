from django.db import models

APP_NAME = "workflows"

class Mode(models.IntegerChoices):
    SERIAL = 1, "Serial"
    PARALLEL = 2, "Parallel"
    
class WorkflowRunStepStatus(models.TextChoices):
    PENDING = 'pending', 'Pending'
    RUNNING = 'running', 'Running'
    COMPLETED = 'completed', 'Completed'
    FAILED = 'failed', 'Failed'
    SKIPPED = 'skipped', 'Skipped'