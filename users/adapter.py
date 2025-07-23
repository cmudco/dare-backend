from allauth.account.adapter import DefaultAccountAdapter

from config.env import FRONTEND_CONFIRM_EMAIL_URL, FRONTEND_PASSWORD_RESET_URL
from users.utils import detect_platform_from_request, get_platform_frontend_url
from users.constants import AuthSourceChoice

class AccountAdapter(DefaultAccountAdapter):
    def get_email_confirmation_url(self, request, emailconfirmation):
        # Detect platform from request to determine callback parameter
        platform = detect_platform_from_request(request)
        callback_url = get_platform_frontend_url(platform)

        return f"{FRONTEND_CONFIRM_EMAIL_URL}?key={emailconfirmation.key}&callbackurl={callback_url}"

    def render_mail(self, template_prefix, email, context):
        """
        Renders an email template with context and returns the email message.
        This method is called for all emails, including password reset emails.
        """

        if template_prefix == 'account/email/password_reset_key':
            uid = context['uid']
            token = context['token']

            # Try to get request from context to detect platform
            request = context.get('request')
            if request:
                platform = detect_platform_from_request(request)
                callback_url = get_platform_frontend_url(platform)
                password_reset_url = f"{FRONTEND_PASSWORD_RESET_URL or ''}/{uid}/{token}?callbackurl={callback_url}"
            else:
                # Fallback to default behavior if request is not available
                callback_url = get_platform_frontend_url(AuthSourceChoice.DARE)
                password_reset_url = f"{FRONTEND_PASSWORD_RESET_URL or ''}/{uid}/{token}?callbackurl={callback_url}"

            context['password_reset_url'] = password_reset_url

        return super().render_mail(template_prefix, email, context)
