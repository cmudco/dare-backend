from django.core.exceptions import ImproperlyConfigured

EMAIL_BACKENDS = {
    "resend": "email_logs.backends.LoggingResendEmailBackend",
    "smtp": "email_logs.backends.LoggingSMTPEmailBackend",
    "console": "email_logs.backends.LoggingConsoleEmailBackend",
}


def resolve_email_provider(provider, host, user, password):
    """An explicit EMAIL_PROVIDER wins; unset keeps the legacy inference."""
    if provider and provider.strip():
        name = provider.strip().lower()
        if name not in EMAIL_BACKENDS:
            raise ImproperlyConfigured(
                f"EMAIL_PROVIDER must be one of {', '.join(EMAIL_BACKENDS)}; got {provider!r}."
            )
        return name
    if user and password:
        return "resend"
    if host:
        return "smtp"
    return "console"
