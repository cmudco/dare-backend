from django.test import TestCase

from billing.constants import LiteLLMKeySourceChoice, UserWalletPreferenceTypeChoice
from billing.models import LiteLLMKey, UserWalletPreference
from core.services.auxiliary_models import MEMORY, TITLE, auxiliary_descriptor
from users.models import User


class AuxiliaryModelTests(TestCase):
    """A proxy user's side jobs run on their roster, not DARE's."""

    def setUp(self):
        self.user = User.objects.create_user(email="proxy@example.com", password="x")
        self.key = LiteLLMKey.objects.create(
            label="gateway",
            base_url="https://proxy.example/v1",
            api_key="k",
            source=LiteLLMKeySourceChoice.USER,
            owner_user=self.user,
            created_by=self.user,
            title_model="gemini/gemini-3.1-flash-lite",
            memory_model="gpt-5.6-luna",
        )

    def _use_the_key(self):
        pref = UserWalletPreference.get_or_create_for(self.user)
        pref.active_wallet_type = UserWalletPreferenceTypeChoice.LITELLM
        pref.active_wallet_ref_id = str(self.key.pk)
        pref.save()

    def test_chosen_title_model_is_returned_for_a_proxy_user(self):
        self._use_the_key()
        descriptor = auxiliary_descriptor(self.user, TITLE)
        self.assertIsNotNone(descriptor)
        self.assertEqual(descriptor.identifier, "gemini/gemini-3.1-flash-lite")
        self.assertEqual(descriptor.litellm_key, self.key)

    def test_chosen_memory_model_is_returned_for_a_proxy_user(self):
        self._use_the_key()
        descriptor = auxiliary_descriptor(self.user, MEMORY)
        self.assertEqual(descriptor.identifier, "gpt-5.6-luna")

    def test_a_wallet_user_keeps_dare_defaults(self):
        # Preference stays DARE, so the caller falls back to its own model.
        self.assertIsNone(auxiliary_descriptor(self.user, TITLE))

    def test_an_unset_field_falls_back_rather_than_guessing(self):
        self.key.title_model = ""
        self.key.save()
        self._use_the_key()
        self.assertIsNone(auxiliary_descriptor(self.user, TITLE))
        # The other job is unaffected by the blank one.
        self.assertIsNotNone(auxiliary_descriptor(self.user, MEMORY))

    def test_no_user_means_no_choice(self):
        self.assertIsNone(auxiliary_descriptor(None, TITLE))

    def test_the_descriptor_dispatches_to_the_proxy_transport(self):
        self._use_the_key()
        handle = auxiliary_descriptor(self.user, TITLE).to_dispatch_handle()
        # provider must describe the transport, or dispatch picks a
        # provider-native service the proxy cannot serve.
        self.assertEqual(handle.provider, "custom")
        self.assertEqual(handle.identifier, "gemini/gemini-3.1-flash-lite")
