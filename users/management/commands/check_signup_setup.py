"""Inspect signup configuration; send mail only with an explicit recipient."""

from email.utils import parseaddr
from smtplib import SMTPException
from urllib.parse import urlsplit

from django.conf import settings
from django.contrib.sites.models import Site
from django.core.exceptions import ValidationError
from django.core.mail import EmailMessage
from django.core.management.base import BaseCommand, CommandError
from django.core.validators import validate_email

from config import env
from users.access_request_models import AccessRequestPolicy
from users.services.access_request_service import reviewers_for, validate_public_url


class Command(BaseCommand):
    help = "Check migrated signup configuration without changing users or sending mail."

    def add_arguments(self, parser):
        parser.add_argument("--require-enabled", action="store_true")
        parser.add_argument(
            "--send-test-to",
            help="Explicitly send one diagnostic message to this email address after checks pass.",
        )

    def handle(self, *args, **options):
        errors = []
        http_admin = False
        backend = settings.EMAIL_BACKEND
        smtp = backend in {
            "email_logs.backends.LoggingSMTPEmailBackend",
            "django.core.mail.backends.smtp.EmailBackend",
        }
        resend = backend == "email_logs.backends.LoggingResendEmailBackend"
        if not smtp and not resend:
            errors.append(
                "Select the SMTP or Resend delivery backend; console mail is not delivered."
            )
        if smtp and not settings.EMAIL_HOST:
            errors.append("Configure EMAIL_HOST for the approved SMTP service.")
        if smtp and bool(settings.EMAIL_HOST_USER) != bool(
            settings.EMAIL_HOST_PASSWORD
        ):
            errors.append(
                "SMTP username and password must both be set or both be blank for an authorized relay."
            )
        if smtp and settings.EMAIL_USE_TLS and settings.EMAIL_USE_SSL:
            errors.append("Select STARTTLS or implicit TLS, not both.")
        if resend and not settings.EMAIL_HOST_PASSWORD:
            errors.append(
                "Configure the Resend API key privately in EMAIL_HOST_PASSWORD."
            )
        try:
            validate_email(parseaddr(settings.DEFAULT_FROM_EMAIL or "")[1])
        except ValidationError:
            errors.append("Configure EMAIL_FROM with an approved sender address.")
        if settings.ACCOUNT_EMAIL_VERIFICATION != "mandatory":
            errors.append("ACCOUNT_EMAIL_VERIFICATION must remain mandatory.")
        for setting in ("FRONTEND_CONFIRM_EMAIL_URL", "FRONTEND_PASSWORD_RESET_URL"):
            # These URLs are owned by config.env and consumed by the existing account adapter.
            try:
                validate_public_url(getattr(env, setting) or "")
            except ValidationError:
                errors.append(
                    f"Configure a valid public {setting} URL (HTTPS or an explicitly allowlisted HTTP origin)."
                )
        policy = AccessRequestPolicy.objects.filter(pk=1).first()
        if not policy:
            errors.append(
                "Create Access request settings in Django admin after applying migrations."
            )
        else:
            for field in ("admin_base_url", "registration_url"):
                try:
                    validate_public_url(
                        getattr(policy, field), origin_only=field == "admin_base_url"
                    )
                    if field == "admin_base_url":
                        http_admin = urlsplit(policy.admin_base_url).scheme == "http"
                except ValidationError:
                    errors.append(
                        f"Configure a valid {field} in Access request settings."
                    )
            if not reviewers_for(policy):
                errors.append(
                    "Choose at least one active staff reviewer with change-access-request permission."
                )
            if not policy.enabled:
                if options["require_enabled"]:
                    errors.append("Access requests are disabled in Django admin.")
                else:
                    self.stdout.write(
                        "Access requests are disabled; keep them disabled until delivery tests pass."
                    )
            if not policy.email_approval_enabled:
                self.stdout.write(
                    "Email approvals are disabled; administrators must use the admin request list."
                )
        if not settings.DEBUG:
            if http_admin:
                if settings.SESSION_COOKIE_SECURE or settings.CSRF_COOKIE_SECURE:
                    errors.append(
                        "Secure session/CSRF cookies cannot be used with the allowlisted HTTP admin origin."
                    )
                self.stdout.write(
                    "HTTP admin origin explicitly allowlisted. Retain the required VPN/firewall restrictions; this mode does not add TLS."
                )
            elif not (settings.SESSION_COOKIE_SECURE and settings.CSRF_COOKIE_SECURE):
                errors.append(
                    "Enable secure session/CSRF cookies for the production HTTPS deployment."
                )
        site = Site.objects.get_current()
        if site.domain in {"example.com", "localhost"} and not settings.DEBUG:
            errors.append(
                "Configure the public domain and name under Django admin Sites, then restart web processes."
            )
        self.stdout.write(
            f"Trusted proxy count: {settings.REST_FRAMEWORK.get('NUM_PROXIES', 0)}. Verify this matches the ingress and blocks direct access."
        )
        recipient = options["send_test_to"]
        if recipient:
            try:
                validate_email(recipient)
            except ValidationError:
                errors.append("--send-test-to must be a valid email address.")
        if errors:
            for error in errors:
                self.stderr.write(error)
            raise CommandError(
                "Signup configuration is incomplete; no diagnostic mail was sent."
            )
        self.stdout.write(
            self.style.SUCCESS(
                "Signup configuration checks passed. Provider authorization and inbox delivery still require a live test."
            )
        )
        if recipient:
            message = EmailMessage(
                "DARE signup delivery test",
                "This diagnostic checks the configured DARE mail provider. No access request, account, invitation or permission was created. Confirm receipt with the deployment administrator.",
                to=[recipient],
            )
            message.dare_sensitive = True
            try:
                sent = message.send(fail_silently=False)
            except (SMTPException, OSError):
                raise CommandError(
                    "Diagnostic delivery failed. Check provider settings and Email Logs privately."
                ) from None
            if sent != 1:
                raise CommandError(
                    "Mail backend did not accept the diagnostic. Check Email Logs privately."
                )
            self.stdout.write(
                self.style.SUCCESS(
                    "Diagnostic submitted to mail backend. Confirm inbox receipt separately."
                )
            )
