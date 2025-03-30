from django.db import models

APP_NAME = "files"
ALLOWED_FILES = ['docx', 'doc', 'pdf', 'txt', 'md', 'json', 'plain']

class FileStatus(models.IntegerChoices):
    PROCESSING = 0, "Processing"
    PROCESSED = 1, "Processed"
    FAILED = 2, "Failed"