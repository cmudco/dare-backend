"""Review requests in Django admin, including authenticated email links."""

from django import forms
from django.contrib import admin, messages
from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404, HttpResponseBadRequest
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _

from users.access_request_constants import DeliveryState, RequestState
from users.access_request_models import (
    AccessRequest,
    AccessRequestDecision,
    AccessRequestDelivery,
    AccessRequestPolicy,
)
from users.services.access_request_service import (
    AccessRequestsUnavailable,
    RequestAlreadyReviewed,
    decide_request,
    deliver,
    resolve_email_review,
    validate_public_url,
)


class PolicyForm(forms.ModelForm):
    class Meta:
        model = AccessRequestPolicy
        fields = "__all__"

    def clean(self):
        data = super().clean()
        if data.get("enabled"):
            for field in ("admin_base_url", "registration_url"):
                try:
                    validate_public_url(
                        data.get(field, ""), origin_only=field == "admin_base_url"
                    )
                except ValidationError as error:
                    self.add_error(field, error)
            reviewers = data.get("reviewers")
            if not reviewers:
                self.add_error(
                    "reviewers", _("Choose at least one authorized reviewer.")
                )
            elif any(
                not u.is_active
                or not u.is_staff
                or not u.has_perm("users.change_accessrequest")
                for u in reviewers
            ):
                self.add_error(
                    "reviewers",
                    _(
                        "Each reviewer must be active staff with permission to change access requests."
                    ),
                )
        return data


@admin.register(AccessRequestPolicy)
class AccessRequestPolicyAdmin(admin.ModelAdmin):
    form = PolicyForm
    filter_horizontal = ["reviewers"]

    def has_add_permission(self, request):
        return request.user.is_superuser and not AccessRequestPolicy.objects.exists()

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return False


class DeliveryInline(admin.TabularInline):
    model = AccessRequestDelivery
    fields = [
        "kind",
        "recipient",
        "decision_version",
        "state",
        "attempts",
        "attempted_at",
        "sent_at",
        "failure_reason",
    ]
    readonly_fields = fields
    extra = 0
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False

    def has_view_permission(self, request, obj=None):
        return request.user.has_perm(
            "users.view_accessrequest"
        ) or request.user.has_perm("users.change_accessrequest")


class DecisionInline(admin.TabularInline):
    model = AccessRequestDecision
    fields = ["version", "previous_status", "status", "reviewer", "decided_at"]
    readonly_fields = fields
    extra = 0
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_view_permission(self, request, obj=None):
        return request.user.has_perm(
            "users.view_accessrequest"
        ) or request.user.has_perm("users.change_accessrequest")


@admin.register(AccessRequest)
class AccessRequestAdmin(admin.ModelAdmin):
    list_display = [
        "email",
        "kind",
        "name",
        "status",
        "review_request",
        "created_at",
        "reviewed_by",
        "delivery_status",
    ]
    list_filter = ["kind", "status", "created_at", "deliveries__state"]
    search_fields = ["email", "name"]
    fields = [
        "kind",
        "registered_user",
        "access_managed_in_users",
        "activated_at",
        "name",
        "email",
        "reason",
        "status",
        "created_at",
        "reviewed_at",
        "reviewed_by",
        "review_request",
        "code_group",
    ]
    readonly_fields = fields
    inlines = [DecisionInline, DeliveryInline]
    actions = ["approve_requests", "decline_requests", "retry_emails"]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return (
            super().has_change_permission(request, obj)
            and AccessRequestPolicy.objects.filter(
                pk=1, reviewers=request.user
            ).exists()
        )

    @admin.display(description=_("Review request"))
    def review_request(self, obj):
        pending = obj.status == RequestState.PENDING
        if obj.access_managed_in_users and not pending:
            if not obj.registered_user_id:
                return _("Account deleted; request retained")
            return format_html(
                '<a href="{}">{}</a>',
                reverse("admin:users_user_change", args=[obj.registered_user_id]),
                _("Manage in Users"),
            )
        return format_html(
            '<a href="{}">{}</a>',
            reverse(
                (
                    "admin:users_accessrequest_review"
                    if pending
                    else "admin:users_accessrequest_revise"
                ),
                args=[obj.pk],
            ),
            (
                (
                    _("Record existing admin approval")
                    if obj.registered_user_id
                    and (
                        obj.registered_user.is_staff or obj.registered_user.is_superuser
                    )
                    else _("Approve or decline")
                )
                if pending
                else _("Change decision")
            ),
        )

    @admin.display(description=_("Email delivery"))
    def delivery_status(self, obj):
        states = list(obj.deliveries.values_list("state", flat=True))
        if DeliveryState.FAILED in states:
            return _("Delivery failed — open request for details")
        if any(s in {DeliveryState.PENDING, DeliveryState.SENDING} for s in states):
            return _("Pending or interrupted delivery")
        return _("Submitted to mail backend") if states else _("No email queued")

    def get_urls(self):
        return [
            path(
                "revise/<int:request_id>/",
                self.admin_site.admin_view(self.review),
                {"revise": True},
                name="users_accessrequest_revise",
            ),
            path(
                "review/<int:request_id>/",
                self.admin_site.admin_view(self.review),
                name="users_accessrequest_review",
            ),
            path(
                "email-review/<str:token>/",
                self.admin_site.admin_view(self.review),
                name="users_accessrequest_email_review",
            ),
        ] + super().get_urls()

    def review(self, request, request_id=None, token=None, revise=False):
        if not self.has_change_permission(request):
            raise PermissionDenied
        try:
            obj = (
                resolve_email_review(token, request.user)
                if token
                else AccessRequest.objects.get(pk=request_id)
            )
        except (AccessRequestsUnavailable, RequestAlreadyReviewed):
            return TemplateResponse(
                request,
                "admin/users/accessrequest/review.html",
                {
                    **self.admin_site.each_context(request),
                    "title": _("Request unavailable"),
                    "unavailable": True,
                },
                status=409,
            )
        except (
            signing.BadSignature,
            AccessRequest.DoesNotExist,
            KeyError,
            ValueError,
        ) as error:
            raise Http404 from error
        if request.method == "POST":
            decision = request.POST.get("decision")
            if decision not in {"approve", "decline"}:
                return HttpResponseBadRequest(_("Choose approve or decline."))
            expected_version = None
            if revise:
                try:
                    expected_version = int(request.POST.get("decision_version", ""))
                except (TypeError, ValueError):
                    return HttpResponseBadRequest(
                        _("Reload the request before changing its decision.")
                    )
            try:
                changed = decide_request(
                    obj.pk,
                    actor=request.user,
                    approve=decision == "approve",
                    email_token=token,
                    revise=revise,
                    expected_version=expected_version,
                )
            except ValidationError as error:
                self.message_user(request, " ".join(error.messages), messages.ERROR)
            except (AccessRequestsUnavailable, RequestAlreadyReviewed):
                self.message_user(
                    request,
                    _(
                        "The request could not be reviewed. Check settings and current request status."
                    ),
                    messages.ERROR,
                )
            else:
                if changed:
                    self.log_change(request, obj, f"Access request {decision}d")
                    self.message_user(
                        request,
                        _(
                            "Decision saved. Check email delivery results on the request."
                        ),
                        messages.SUCCESS,
                    )
            return redirect("admin:users_accessrequest_change", obj.pk)
        response = TemplateResponse(
            request,
            "admin/users/accessrequest/review.html",
            {
                **self.admin_site.each_context(request),
                "title": (
                    _("Change access decision")
                    if revise
                    else _("Review access request")
                ),
                "access_request": obj,
                "revising": revise,
                "admin_account": obj.access_managed_in_users
                or bool(
                    obj.registered_user_id
                    and (
                        obj.registered_user.is_staff or obj.registered_user.is_superuser
                    )
                ),
                "user_admin_url": (
                    reverse("admin:users_user_change", args=[obj.registered_user_id])
                    if obj.registered_user_id
                    else ""
                ),
                "can_decide": (
                    (obj.status != RequestState.PENDING)
                    if revise
                    else (obj.status == RequestState.PENDING)
                ),
            },
        )
        # Preserve same-origin CSRF checks without leaking review links off-site.
        response["Referrer-Policy"] = "same-origin"
        response["Cache-Control"] = "no-store"
        response["X-Robots-Tag"] = "noindex, nofollow"
        return response

    def decide_selected(self, request, queryset, *, approve):
        count = 0
        for obj in queryset.exclude(status=RequestState.PENDING):
            self.message_user(
                request,
                format_html(
                    _("{} is already reviewed. Use {} to change the decision."),
                    obj.email,
                    format_html(
                        '<a href="{}">{}</a>',
                        reverse("admin:users_accessrequest_revise", args=[obj.pk]),
                        _("Change decision"),
                    ),
                ),
                messages.WARNING,
            )
        for obj in queryset.filter(status=RequestState.PENDING):
            try:
                if decide_request(obj.pk, actor=request.user, approve=approve):
                    self.log_change(
                        request,
                        obj,
                        (
                            "Approved access request"
                            if approve
                            else "Declined access request"
                        ),
                    )
                    count += 1
            except ValidationError as error:
                self.message_user(
                    request,
                    _("%(email)s: %(reason)s")
                    % {"email": obj.email, "reason": " ".join(error.messages)},
                    messages.ERROR,
                )
            except AccessRequestsUnavailable:
                self.message_user(
                    request,
                    _(
                        "Access requests are unavailable. Check Access request settings."
                    ),
                    messages.ERROR,
                )
        if count:
            self.message_user(
                request,
                _(
                    "Saved %(count)s decision(s). Check delivery results for notifications."
                )
                % {"count": count},
                messages.SUCCESS,
            )

    @admin.action(
        description=_("Approve selected pending requests"), permissions=["change"]
    )
    def approve_requests(self, request, queryset):
        self.decide_selected(request, queryset, approve=True)

    @admin.action(
        description=_("Decline selected pending requests"), permissions=["change"]
    )
    def decline_requests(self, request, queryset):
        self.decide_selected(request, queryset, approve=False)

    @admin.action(
        description=_("Retry failed or interrupted emails"), permissions=["change"]
    )
    def retry_emails(self, request, queryset):
        count = 0
        for delivery in AccessRequestDelivery.objects.filter(
            request__in=queryset
        ).exclude(state=DeliveryState.SENT):
            count += int(deliver(delivery.pk))
        self.message_user(
            request,
            _(
                "Submitted %(count)s email(s). Review delivery records for remaining failures."
            )
            % {"count": count},
        )
