from allauth.account.adapter import DefaultAccountAdapter

from config.env import FRONTEND_CONFIRM_EMAIL_URL


class AccountAdapter(DefaultAccountAdapter):
    def get_email_confirmation_url(self, request, emailconfirmation):
        return f"{FRONTEND_CONFIRM_EMAIL_URL}?key={emailconfirmation.key}"
