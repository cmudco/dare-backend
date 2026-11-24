import logging

from allauth.account.adapter import DefaultAccountAdapter
from allauth.core.exceptions import ImmediateHttpResponse
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from django.shortcuts import redirect

from config.env import (FRONTEND_CONFIRM_EMAIL_URL,
                        FRONTEND_PASSWORD_RESET_URL, OIDC_AUTO_CREATE_USERS)
from users.constants import AuthSourceChoice
from users.services.oidc_service import (email_domain_allowed,
                                         frontend_login_error_url,
                                         onboard_sso_user, sync_platform_role)
from users.utils import detect_platform_from_request, get_platform_frontend_url

logger = logging.getLogger(__name__)


class AccountAdapter(DefaultAccountAdapter):
    def get_email_confirmation_url(self, request, emailconfirmation):
        # Detect platform from request to determine callback parameter
        platform = detect_platform_from_request(request)
        callback_url = get_platform_frontend_url(platform)

        return f"{FRONTEND_CONFIRM_EMAIL_URL}/?key={emailconfirmation.key}&callbackurl={callback_url}"

    def render_mail(self, template_prefix, email, context):
        """
        Renders an email template with context and returns the email message.
        This method is called for all emails, including password reset emails.
        """

        if template_prefix == "account/email/password_reset_key":
            uid = context["uid"]
            token = context["token"]

            # Try to get request from context to detect platform
            request = context.get("request")
            if request:
                platform = detect_platform_from_request(request)
                callback_url = get_platform_frontend_url(platform)
                password_reset_url = f"{FRONTEND_PASSWORD_RESET_URL or ''}/{uid}/{token}?callbackurl={callback_url}"
            else:
                # Fallback to default behavior if request is not available
                callback_url = get_platform_frontend_url(AuthSourceChoice.DARE)
                password_reset_url = f"{FRONTEND_PASSWORD_RESET_URL or ''}/{uid}/{token}?callbackurl={callback_url}"

            context["password_reset_url"] = password_reset_url

        return super().render_mail(template_prefix, email, context)


class SocialAccountAdapter(DefaultSocialAccountAdapter):
    """Who may sign in through the identity provider, and what a first sign-in gets."""

    def pre_social_login(self, request, sociallogin):
        claims = sociallogin.account.extra_data
        email = claims.get("email") or ""
        if not email:
            self._refuse("no_email")
        if claims.get("email_verified") is False:
            self._refuse("email_unverified")
        if not email_domain_allowed(email):
            self._refuse("domain_not_allowed")

        if sociallogin.is_existing:
            if not sociallogin.user.is_active:
                self._refuse("account_inactive")
            sync_platform_role(sociallogin.user, claims)
        elif not OIDC_AUTO_CREATE_USERS:
            self._refuse("signup_closed")

    def save_user(self, request, sociallogin, form=None):
        user = super().save_user(request, sociallogin, form)
        onboard_sso_user(user, sociallogin.account.extra_data)
        return user

    def on_authentication_error(
        self, request, provider, error=None, exception=None, extra_context=None
    ):
        logger.warning("SSO sign-in failed: %s (%r)", error, exception)
        self._refuse("provider_error")

    def _refuse(self, reason):
        raise ImmediateHttpResponse(redirect(frontend_login_error_url(reason)))
