from typing import Any, Dict, Union

import resend
from django.conf import settings
from django.core.mail import EmailMessage, EmailMultiAlternatives

resend.api_key = settings.EMAIL_HOST_PASSWORD


def send_email_via_resend(
    email_message: Union[EmailMessage, EmailMultiAlternatives]
) -> Dict[str, Any]:
    """
    Send an email using the Resend API.
    """
    subject = email_message.subject
    from_email = email_message.from_email
    recipients = email_message.to


    if hasattr(email_message, "alternatives") and email_message.alternatives:
        html_content = email_message.alternatives[0][0]
        params = {
            "from": from_email,
            "to": recipients,
            "subject": subject,
            "html": html_content,
        }
    else:
        params = {
            "from": from_email,
            "to": recipients,
            "subject": subject,
            "text": email_message.body,
        }

    response = resend.Emails.send(params)
    return response