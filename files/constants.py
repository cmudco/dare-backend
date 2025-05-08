from django.db import models

APP_NAME = "files"
ALLOWED_FILES = ['docx', 'doc', 'pdf', 'txt', 'md', 'json', 'plain','vnd.openxmlformats-officedocument.wordprocessingml.document', 'rtf', 'html', 'xml', 'csv', 'xls', 'xlsx', 'pptx', 'ppt', 'jpg', 'jpeg', 'png', 'gif', 'bmp', 'tiff', 'webp','application/openxmlformats-officedocument.spreadsheetml.sheet', 'application/vnd.ms-excel', 'application/vnd.openxmlformats-officedocument.presentationml.presentation', 'application/vnd.ms-powerpoint', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', 'application/msword', 'application/pdf', 'text/plain', 'text/html', 'text/xml', 'text/csv']

class FileStatus(models.IntegerChoices):
    PROCESSING = 0, "Processing"
    PROCESSED = 1, "Processed"
    FAILED = 2, "Failed"