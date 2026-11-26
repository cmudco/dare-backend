from typing import Any, Dict, Union

import resend
from django.conf import settings
from django.core.mail import EmailMessage, EmailMultiAlternatives

resend.api_key = settings.RESEND_API_KEY


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

    if email_message.cc:
        params["cc"] = email_message.cc
    if email_message.bcc:
        params["bcc"] = email_message.bcc

    reply_to = email_message.reply_to or next(
        (
            value
            for key, value in email_message.extra_headers.items()
            if key.lower() == "reply-to"
        ),
        None,
    )
    if reply_to:
        params["reply_to"] = reply_to

    response = resend.Emails.send(params)
    return response