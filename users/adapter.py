from allauth.account.adapter import DefaultAccountAdapter

from config.env import FRONTEND_CONFIRM_EMAIL_URL, FRONTEND_PASSWORD_RESET_URL

class AccountAdapter(DefaultAccountAdapter):
    def get_email_confirmation_url(self, request, emailconfirmation):
        return f"{FRONTEND_CONFIRM_EMAIL_URL}?key={emailconfirmation.key}"

    def render_mail(self, template_prefix, email, context):
        """
        Renders an email template with context and returns the email message.
        This method is called for all emails, including password reset emails.
        """

        if template_prefix == 'account/email/password_reset_key':

            uid = context['uid']
            token = context['token']

            context['password_reset_url'] = f"{FRONTEND_PASSWORD_RESET_URL.rstrip('/')}/{uid}/{token}"

        return super().render_mail(template_prefix, email, context)
