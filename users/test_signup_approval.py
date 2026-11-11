"""Registration policy, reviewer decisions and queued-delivery regression tests."""

from unittest.mock import patch

from allauth.account.models import EmailAddress
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase, override_settings
from redis.exceptions import ConnectionError as RedisConnectionError
from rest_framework.test import APIClient

from users.access_request_constants import DeliveryKind, DeliveryState, RequestState
from users.access_request_models import (
    AccessRequest,
    AccessRequestDelivery,
    AccessRequestPolicy,
)
from users.api.access_requests import AccessRequestThrottle
from users.models import AccessCodeGroup
from users.services.access_request_service import (
    activate_registration,
    decide_request,
    deliver,
    enqueue_delivery,
    queue_delivery,
    request_access,
)

User = get_user_model()


@override_settings(
    ACCOUNT_EMAIL_VERIFICATION="mandatory",
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    EMAIL_DELIVERY_CONFIGURED=True,
    ACCESS_REQUEST_THROTTLE_RATE="10000/hour",
    DEBUG=True,
)
class SignupApprovalTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.reviewer = User.objects.create_superuser(
            email="reviewer@example.test", password="test-password-123"
        )
        self.policy = AccessRequestPolicy.objects.create(
            enabled=True,
            admin_base_url="http://localhost:8000",
            registration_url="http://localhost:5173/register",
        )
        self.policy.reviewers.add(self.reviewer)
        self.queue_patch = patch(
            "users.services.access_request_service.django_rq.get_queue"
        )
        self.queue = self.queue_patch.start().return_value
        self.addCleanup(self.queue_patch.stop)

    def register(self, email="applicant@example.test", code=""):
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(
                "/users/api/dj-rest-auth/registration/",
                {
                    "name": "Test Applicant",
                    "email": email,
                    "password1": "test-password-123",
                    "password2": "test-password-123",
                    "access_code": code,
                },
                format="json",
            )

    def applicant(self):
        response = self.register()
        self.assertEqual(response.status_code, 201, response.data)
        return User.objects.get(email="applicant@example.test")

    def decide(self, request, approve, **kwargs):
        with self.captureOnCommitCallbacks(execute=True):
            result = decide_request(
                request.pk, actor=self.reviewer, approve=approve, **kwargs
            )
        request.refresh_from_db()
        return result

    def test_disabled_policy_preserves_code_free_registration(self):
        self.policy.enabled = False
        self.policy.save(update_fields=["enabled"])
        user = self.applicant()
        self.assertTrue(user.is_active)
        self.assertFalse(AccessRequest.objects.filter(registered_user=user).exists())

    def test_missing_policy_preserves_code_free_registration(self):
        self.policy.delete()
        user = self.applicant()
        self.assertTrue(user.is_active)
        self.assertFalse(AccessRequest.objects.filter(registered_user=user).exists())

    def test_enabled_policy_creates_inactive_account_and_queues_verification(self):
        user = self.applicant()
        self.assertFalse(user.is_active)
        self.assertTrue(
            AccessRequest.objects.filter(
                registered_user=user, status=RequestState.PENDING
            ).exists()
        )
        jobs = [call.args[0] for call in self.queue.enqueue.call_args_list]
        self.assertIn("users.tasks.deliver_access_request", jobs)
        self.assertIn("users.tasks.send_signup_verification", jobs)

    def test_invitation_signup_consumes_code_without_further_approval(self):
        request_access(name="Invited", email="invite@example.test", reason="Test")
        request = AccessRequest.objects.get(email="invite@example.test")
        self.decide(request, True)
        response = self.register("invite@example.test", request.code_group.access_code)
        self.assertEqual(response.status_code, 201, response.data)
        user = User.objects.get(email="invite@example.test")
        self.assertTrue(user.is_active)
        self.assertFalse(response.data["approval_required"])
        request.code_group.refresh_from_db()
        self.assertEqual(request.code_group.current_usage, 1)

    def test_invitation_cannot_be_used_by_another_email(self):
        request_access(name="Invited", email="invite@example.test", reason="Test")
        request = AccessRequest.objects.get(email="invite@example.test")
        self.decide(request, True)
        response = self.register("other@example.test", request.code_group.access_code)
        self.assertEqual(response.status_code, 400)
        self.assertFalse(User.objects.filter(email="other@example.test").exists())

    def test_approve_then_verify_activates_only_after_verification(self):
        user = self.applicant()
        request = AccessRequest.objects.get(registered_user=user)
        self.decide(request, True)
        user.refresh_from_db()
        self.assertFalse(user.is_active)
        EmailAddress.objects.filter(user=user).update(verified=True)
        with self.captureOnCommitCallbacks(execute=True):
            self.assertTrue(activate_registration(request.pk))
        user.refresh_from_db()
        self.assertTrue(user.is_active)

    def test_verify_then_approve_activates_only_after_approval(self):
        user = self.applicant()
        request = AccessRequest.objects.get(registered_user=user)
        EmailAddress.objects.filter(user=user).update(verified=True)
        self.assertFalse(activate_registration(request.pk))
        self.decide(request, True)
        user.refresh_from_db()
        self.assertTrue(user.is_active)

    def test_decline_queues_notification_and_keeps_account_inactive(self):
        user = self.applicant()
        request = AccessRequest.objects.get(registered_user=user)
        self.decide(request, False)
        self.assertEqual(request.status, RequestState.DECLINED)
        user.refresh_from_db()
        self.assertFalse(user.is_active)
        self.assertTrue(request.deliveries.filter(kind=DeliveryKind.DECLINE).exists())

    def test_reversals_revoke_and_restore_without_changing_credentials_or_credit(self):
        user = self.applicant()
        EmailAddress.objects.filter(user=user).update(verified=True)
        request = AccessRequest.objects.get(registered_user=user)
        self.decide(request, True)
        user.refresh_from_db()
        password, role, credit = user.password, user.platform_role, user.wallet.balance
        self.decide(
            request, False, revise=True, expected_version=request.decision_version
        )
        user.refresh_from_db()
        request.code_group.refresh_from_db()
        self.assertFalse(user.is_active)
        self.assertFalse(request.code_group.is_active)
        self.decide(
            request, True, revise=True, expected_version=request.decision_version
        )
        user.refresh_from_db()
        user.wallet.refresh_from_db()
        self.assertTrue(user.is_active)
        self.assertEqual(
            (user.password, user.platform_role, user.wallet.balance),
            (password, role, credit),
        )
        self.assertEqual(request.decisions.count(), 3)

    def test_superseded_approval_email_is_not_sent_after_decline(self):
        user = self.applicant()
        request = AccessRequest.objects.get(registered_user=user)
        self.decide(request, True)
        approval = request.deliveries.get(kind=DeliveryKind.APPROVAL)
        self.decide(
            request, False, revise=True, expected_version=request.decision_version
        )
        with patch("django.core.mail.EmailMessage.send") as send:
            self.assertFalse(deliver(approval.pk))
            send.assert_not_called()
        approval.refresh_from_db()
        self.assertEqual(approval.state, DeliveryState.FAILED)
        self.assertEqual(approval.failure_reason, "decision_superseded")

    def test_stale_reversal_is_rejected(self):
        user = self.applicant()
        request = AccessRequest.objects.get(registered_user=user)
        self.decide(request, True)
        with self.assertRaises(ValidationError):
            self.decide(request, False, revise=True, expected_version=0)
        self.assertEqual(request.status, RequestState.APPROVED)

    def test_unconfigured_staff_cannot_review(self):
        user = self.applicant()
        request = AccessRequest.objects.get(registered_user=user)
        outsider = User.objects.create_superuser(
            email="outside@example.test", password="test-password-123"
        )
        with self.assertRaises(PermissionDenied):
            decide_request(request.pk, actor=outsider, approve=True)
        request.refresh_from_db()
        self.assertEqual(request.status, RequestState.PENDING)

    def test_request_does_not_send_inline(self):
        request = AccessRequest.objects.create(email="queue@example.test", name="Queue")
        with patch("users.services.access_request_service.deliver") as send:
            with self.captureOnCommitCallbacks(execute=True):
                delivery = queue_delivery(
                    request, DeliveryKind.REVIEW, self.reviewer.email, self.reviewer
                )
            send.assert_not_called()
        self.queue.enqueue.assert_called_with(
            "users.tasks.deliver_access_request", delivery.pk, job_timeout=60
        )

    def test_queue_failure_is_visible(self):
        request = AccessRequest.objects.create(
            email="failed@example.test", name="Queue"
        )
        delivery = AccessRequestDelivery.objects.create(
            request=request,
            kind=DeliveryKind.REVIEW,
            recipient=self.reviewer.email,
            reviewer=self.reviewer,
        )
        self.queue.enqueue.side_effect = RedisConnectionError("test-only outage")
        self.assertFalse(enqueue_delivery(delivery.pk))
        delivery.refresh_from_db()
        self.assertEqual(delivery.state, DeliveryState.FAILED)
        self.assertEqual(delivery.failure_reason, "mail_queue_unavailable")

    def test_sent_delivery_is_not_sent_again(self):
        request = AccessRequest.objects.create(email="sent@example.test", name="Sent")
        delivery = AccessRequestDelivery.objects.create(
            request=request,
            kind=DeliveryKind.REVIEW,
            recipient=self.reviewer.email,
            reviewer=self.reviewer,
            state=DeliveryState.SENT,
        )
        with patch("users.services.access_request_service.mail_for") as render:
            self.assertFalse(deliver(delivery.pk))
            render.assert_not_called()

    def test_signup_options_report_readiness_without_reviewer_details(self):
        response = self.client.get("/users/api/signup-options/")
        self.assertEqual(response.data, {"approval_signup_enabled": True})
        self.policy.enabled = False
        self.policy.save(update_fields=["enabled"])
        self.assertEqual(
            self.client.get("/users/api/signup-options/").data,
            {"approval_signup_enabled": False},
        )

    @override_settings(ACCESS_REQUEST_THROTTLE_RATE=None)
    def test_default_throttle_allows_a_shared_classroom(self):
        self.assertEqual(AccessRequestThrottle().rate, "300/hour")


class SingleUseInvitationTests(TestCase):
    def test_stale_instances_cannot_consume_the_last_slot_twice(self):
        group = AccessCodeGroup.objects.create(
            name="Single slot", access_code="single-slot-test", max_capacity=1
        )
        stale = AccessCodeGroup.objects.get(pk=group.pk)
        self.assertTrue(group.use_code())
        self.assertFalse(stale.use_code())
        group.refresh_from_db()
        self.assertEqual(group.current_usage, 1)
