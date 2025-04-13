from django.db import models
from common.models import TimeStampMixin


class EmailLog(TimeStampMixin):
    """
    A model to store details of emails sent by the application.
    """
    recipient = models.EmailField()
    subject = models.CharField(max_length=255)
    body = models.TextField()
    sent_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(
        max_length=20,
        choices=[
            ("PENDING", "Pending"),
            ("SENT", "Sent"),
            ("FAILED", "Failed"),
        ],
        default="PENDING",
    )
    error_message = models.TextField(null=True, blank=True)

    class Meta:
        verbose_name = "Email Log"
        verbose_name_plural = "Email Logs"

    def __str__(self):
        return f"Email to {self.recipient} - {self.subject} ({self.status})"