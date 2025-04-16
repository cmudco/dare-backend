# email_logs/backends.py
from django.core.mail.backends.smtp import EmailBackend as SMTPEmailBackend
from django.core.mail.backends.console import EmailBackend as ConsoleEmailBackend
from django.core.mail.message import EmailMessage
from django.utils import timezone
from .models import EmailLog


class LoggingEmailBackendMixin:
    """
    A mixin to add email logging functionality to an email backend.
    """
    def send_messages(self, email_messages):
        """
        Overrides the send_messages method to log each email to the EmailLog model.
        """
        if not email_messages:
            return 0

        sent_count = 0
        for message in email_messages:
            # Log the email before sending
            email_log = EmailLog.objects.create(
                recipient=", ".join(message.to),  # Join multiple recipients if any
                subject=message.subject,
                body=message.body,
                status="PENDING",
            )

            try:
                # Send the email using the parent backend
                result = super().send_messages([message])
                if result:
                    email_log.status = "SENT"
                    email_log.sent_at = timezone.now()
                    sent_count += result
                else:
                    email_log.status = "FAILED"
                    email_log.error_message = "Failed to send email (unknown error)"
            except Exception as e:
                email_log.status = "FAILED"
                email_log.error_message = str(e)
            finally:
                email_log.save()

        return sent_count


class LoggingSMTPEmailBackend(LoggingEmailBackendMixin, SMTPEmailBackend):
    """
    A custom email backend that logs emails to the EmailLog model before sending via SMTP.
    """
    pass


class LoggingConsoleEmailBackend(LoggingEmailBackendMixin, ConsoleEmailBackend):
    """
    A custom email backend that logs emails to the EmailLog model before sending via console.
    """
    pass