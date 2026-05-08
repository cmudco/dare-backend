from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Count, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from api_keys.models import UserProviderAPIKey
from billing.api.serializers import (
    ActiveWalletRefSerializer,
    AllocateSerializer,
    EffectivePolicySerializer,
    FeatureFlagsSerializer,
    FundBudgetSerializer,
    GroupWalletReadSerializer,
    GroupWalletWriteSerializer,
    LiteLLMKeyCreateSerializer,
    LiteLLMKeyReadSerializer,
    LiteLLMKeyRenameSerializer,
    LiteLLMTestRequestSerializer,
    MemberRowSerializer,
    OwnedGroupSerializer,
    SetActiveWalletRequestSerializer,
    SystemRefillPolicySerializer,
    TransactionSerializer,
    UpsertUserOverrideSerializer,
    UserRefillOverrideSerializer,
    WalletSerializer,
    WalletsListResponseSerializer,
)
from billing.litellm_probe import probe_litellm_connection
from api_keys.constants import BillingModeChoice
from billing.constants import (
    TransactionTypeChoice,
    LiteLLMKeySourceChoice,
    UserWalletPreferenceTypeChoice,
)
from users.constants import AuthSourceChoice
from billing.group_wallet_service import (
    AllocateToMemberRequest,
    FundGroupBudgetRequest,
    GroupWalletService,
    UpdateGroupPolicyRequest,
    UpsertUserOverrideRequest,
)
from billing.models import (
    BYOKeyFeatureFlag,
    GroupWallet,
    LiteLLMKey,
    SystemRefillPolicy,
    Transaction,
    UserRefillOverride,
    UserWalletPreference,
    Wallet,
)
from billing.services import WalletService
from common.pagination import CustomPageNumberPagination
from common.permissions import IsSuperAdmin
from conversations.models import Message
from core.services.sb_client import SocraticBooksClient
from core.services.energy_service import compute_relatable_stats
from users.models import User
from users.utils import detect_platform_from_request


def _validation_response(exc: ValidationError):
    detail = getattr(exc, "message_dict", None) or {"detail": exc.messages}
    return Response(detail, status=status.HTTP_400_BAD_REQUEST)


class BillingViewSet(viewsets.ViewSet):
    """
    ViewSet for billing-related operations.
    """

    permission_classes = [IsAuthenticated]
    pagination_class = CustomPageNumberPagination

    @action(detail=False, methods=["get"])
    def wallet(self, request):
        """
        Get the wallet details for the authenticated user.
        """
        wallet = request.user.wallet
        serializer = WalletSerializer(wallet)
        return Response(serializer.data)

    @action(detail=False, methods=["get"], url_path="effective-policy")
    def effective_policy(self, request):
        """Return the caller's resolved refill policy (amount + period + sources)."""
        policy = WalletService.get_effective_refill_policy(request.user)
        return Response(EffectivePolicySerializer(policy).data)

    @action(detail=False, methods=["get"])
    def transactions(self, request):
        """
        List the authenticated user's transactions, paginated and filtered.

        Query params (all optional):
            platform:     "ALL" | "DARE" | "SocraticBots"
                          When omitted, defaults to the platform detected from
                          the auth scope (preserves the SocraticBots backend's
                          behavior when it calls without the param).
            billing_mode: "wallet" | "own_api"
                          Filter results to a single billing mode.

        The response wraps DRF's standard paginated payload and adds a
        `summary` object with counts per billing mode under the current
        platform filter, so tab badges in the UI can display accurate totals
        across all pages rather than just the current page.
        """
        platform_param = request.query_params.get("platform")
        billing_mode_param = request.query_params.get("billing_mode")

        base_qs = Transaction.objects.filter(user=request.user)

        if platform_param == "ALL":
            pass
        elif platform_param in AuthSourceChoice.values:
            base_qs = base_qs.filter(platform=platform_param)
        else:
            base_qs = base_qs.filter(platform=detect_platform_from_request(request))

        summary = {
            "all": base_qs.count(),
            "wallet": base_qs.filter(billing_mode=BillingModeChoice.WALLET).count(),
            "ownApi": base_qs.filter(billing_mode=BillingModeChoice.OWN_API).count(),
            "litellm": base_qs.filter(
                billing_mode=BillingModeChoice.LITELLM
            ).count(),
        }

        queryset = base_qs.order_by("-created_at")
        if billing_mode_param in BillingModeChoice.values:
            queryset = queryset.filter(billing_mode=billing_mode_param)

        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = TransactionSerializer(page, many=True)
            response = self.get_paginated_response(serializer.data)
            response.data["summary"] = summary
            return response

        serializer = TransactionSerializer(queryset, many=True)
        return Response({"results": serializer.data, "summary": summary})

    @action(detail=False, methods=["get"])
    def model_stats(self, request):
        platform = detect_platform_from_request(request)

        per_model_stats = (
            Transaction.objects.filter(
                user=request.user,
                type=TransactionTypeChoice.DEBIT,
                llm__isnull=False,
                platform=platform,
            )
            .values("llm__id", "llm__name", "llm__identifier", "llm__provider")
            .annotate(
                total_cost=Sum("amount"),
                input_tokens=Sum("input_tokens"),
                output_tokens=Sum("output_tokens"),
                transaction_count=Count("id"),
            )
            .order_by("-total_cost")
        )

        models_billing_stats = []
        for stat in per_model_stats:
            input_tokens = stat["input_tokens"] or 0
            output_tokens = stat["output_tokens"] or 0
            total_cost = stat["total_cost"] or 0

            models_billing_stats.append(
                {
                    "llm_id": stat["llm__id"],
                    "llm_name": stat["llm__name"],
                    "llm_identifier": stat["llm__identifier"],
                    "llm_provider": stat["llm__provider"],
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "total_tokens": input_tokens + output_tokens,
                    "total_cost": f"${total_cost:.6f}" if total_cost else "$0.00",
                    "total_cost_decimal": total_cost,
                    "transaction_count": stat["transaction_count"],
                }
            )

        overall_stats = Transaction.objects.filter(
            user=request.user,
            type=TransactionTypeChoice.DEBIT,
            llm__isnull=False,
            platform=platform,
        ).aggregate(
            total_cost=Sum("amount"),
            total_input_tokens=Sum("input_tokens"),
            total_output_tokens=Sum("output_tokens"),
            total_transactions=Count("id"),
        )

        response_data = {
            "models_billing_stats": models_billing_stats,
            "overall_stats": {
                "total_cost": (
                    f"${overall_stats['total_cost']:.6f}"
                    if overall_stats["total_cost"]
                    else "$0.00"
                ),
                "total_cost_decimal": overall_stats["total_cost"] or 0,
                "total_input_tokens": overall_stats["total_input_tokens"] or 0,
                "total_output_tokens": overall_stats["total_output_tokens"] or 0,
                "total_tokens": (overall_stats["total_input_tokens"] or 0)
                + (overall_stats["total_output_tokens"] or 0),
                "total_transactions": overall_stats["total_transactions"] or 0,
            },
        }

        return Response(response_data)

    @action(detail=False, methods=["get"], url_path="energy-stats")
    def energy_stats(self, request):
        platform = detect_platform_from_request(request)
        period = request.query_params.get("period", "all")

        base_qs = Message.active_objects.filter(
            conversation__user=request.user,
            conversation__source=platform,
            energy_wh__isnull=False,
            energy_wh__gt=0,
        )

        if period != "all":
            days_map = {"7d": 7, "30d": 30, "90d": 90}
            days = days_map.get(period, 0)
            if days:
                cutoff = timezone.now() - timezone.timedelta(days=days)
                base_qs = base_qs.filter(created_at__gte=cutoff)

        totals = base_qs.aggregate(
            total_energy_wh=Sum("energy_wh"),
            total_carbon_g=Sum("carbon_g"),
            total_water_ml=Sum("water_ml"),
            message_count=Count("id"),
        )

        total_energy = float(totals["total_energy_wh"] or 0)
        total_carbon = float(totals["total_carbon_g"] or 0)
        total_water = float(totals["total_water_ml"] or 0)
        message_count = totals["message_count"] or 0

        relatable = compute_relatable_stats(total_energy)

        per_model = (
            base_qs.values("llm__id", "llm__name", "llm__identifier", "llm__provider")
            .annotate(
                energy_wh_sum=Sum("energy_wh"),
                carbon_g_sum=Sum("carbon_g"),
                water_ml_sum=Sum("water_ml"),
                message_count=Count("id"),
            )
            .order_by("-energy_wh_sum")
        )

        models_breakdown = [
            {
                "llmId": row["llm__id"],
                "llmName": row["llm__name"],
                "llmIdentifier": row["llm__identifier"],
                "llmProvider": row["llm__provider"],
                "energyWh": float(row["energy_wh_sum"] or 0),
                "carbonG": float(row["carbon_g_sum"] or 0),
                "waterMl": float(row["water_ml_sum"] or 0),
                "messageCount": row["message_count"],
            }
            for row in per_model
        ]

        return Response(
            {
                "overallStats": {
                    "totalEnergyWh": round(total_energy, 4),
                    "totalCarbonG": round(total_carbon, 4),
                    "totalWaterMl": round(total_water, 4),
                    "messageCount": message_count,
                },
                "relatableStats": {
                    "phoneBatteryPct": round(relatable.phone_battery_pct, 4),
                    "googleSearchesEquiv": round(relatable.google_searches_equiv, 2),
                    "ledBulbSeconds": round(relatable.led_bulb_seconds, 2),
                    "netflixSeconds": round(relatable.netflix_seconds, 2),
                    "evMeters": round(relatable.ev_meters, 2),
                    "fridgeSeconds": round(relatable.fridge_seconds, 2),
                    "humanThinkingSeconds": round(relatable.human_thinking_seconds, 2),
                },
                "modelsBreakdown": models_breakdown,
                "period": period,
            }
        )

    @action(detail=False, methods=["get"], url_path=r"bots/(?P<bot_id>[^/.]+)/usage")
    def bot_usage(self, request, bot_id=None):
        """
        Per-bot usage breakdown for the bot owner.

        Owner-only: only returns rows the caller is the bot owner of (matched
        by ``Transaction.bot_owner_id == request.user.id``). Anonymous-call
        rows are bucketed by a hash of the conversation's ``anonymous_session_id``
        so the dashboard can distinguish individual sessions without exposing
        the raw id.

        Query params:
            group_by: ``user`` (default) | ``date``
            period: ``7d`` | ``30d`` | ``90d`` | ``all`` (default)
        """
        try:
            bot_pk = int(bot_id)
        except (TypeError, ValueError):
            return Response(
                {"detail": "invalid bot_id"}, status=status.HTTP_400_BAD_REQUEST
            )

        base_qs = Transaction.objects.filter(
            bot_id=bot_pk,
            bot_owner=request.user,
            type=TransactionTypeChoice.DEBIT,
        )

        period = request.query_params.get("period", "all")
        if period != "all":
            days = {"7d": 7, "30d": 30, "90d": 90}.get(period)
            if days:
                cutoff = timezone.now() - timezone.timedelta(days=days)
                base_qs = base_qs.filter(created_at__gte=cutoff)

        totals = base_qs.aggregate(
            total_cost=Sum("amount"),
            total_input=Sum("input_tokens"),
            total_output=Sum("output_tokens"),
            message_count=Count("id"),
        )

        # Per data-schema-contract: separate named fields per shape, never a
        # polymorphic `breakdown` field. The FE narrows by reading `groupBy`
        # and then accesses the matching named field directly.
        group_by = request.query_params.get("group_by", "user")
        user_breakdown = None
        date_breakdown = None
        if group_by == "user":
            rows = (
                base_qs.values("user__id", "user__email")
                .annotate(
                    total_cost=Sum("amount"),
                    input_tokens=Sum("input_tokens"),
                    output_tokens=Sum("output_tokens"),
                    message_count=Count("id"),
                )
                .order_by("-total_cost")
            )
            user_breakdown = [
                {
                    "userId": row["user__id"],
                    "userEmail": row["user__email"] or "anonymous",
                    "totalCost": str(row["total_cost"] or 0),
                    "inputTokens": row["input_tokens"] or 0,
                    "outputTokens": row["output_tokens"] or 0,
                    "messageCount": row["message_count"],
                }
                for row in rows
            ]
        elif group_by == "date":
            rows = (
                base_qs.annotate(date=TruncDate("created_at"))
                .values("date")
                .annotate(
                    total_cost=Sum("amount"),
                    message_count=Count("id"),
                )
                .order_by("date")
            )
            date_breakdown = [
                {
                    "date": row["date"].isoformat() if row["date"] else None,
                    "totalCost": str(row["total_cost"] or 0),
                    "messageCount": row["message_count"],
                }
                for row in rows
            ]
        else:
            return Response(
                {"detail": "group_by must be 'user' or 'date'"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            {
                "botId": bot_pk,
                "period": period,
                "groupBy": group_by,
                "totals": {
                    "totalCost": str(totals["total_cost"] or 0),
                    "totalInputTokens": totals["total_input"] or 0,
                    "totalOutputTokens": totals["total_output"] or 0,
                    "messageCount": totals["message_count"] or 0,
                },
                "userBreakdown": user_breakdown,
                "dateBreakdown": date_breakdown,
            }
        )

    @action(detail=False, methods=["patch"], url_path=r"bots/(?P<bot_id>[^/.]+)/cap")
    def bot_cap(self, request, bot_id=None):
        """
        Update a bot's spend cap. Owner-only — verified by checking that
        the caller has at least one Transaction stamped as ``bot_owner`` for
        this bot id (a bot they've never been billed for they don't own).

        Body: ``{ "budget": "<decimal>" }``
        """
        try:
            bot_pk = int(bot_id)
        except (TypeError, ValueError):
            return Response(
                {"detail": "invalid bot_id"}, status=status.HTTP_400_BAD_REQUEST
            )

        budget_raw = request.data.get("budget")
        if budget_raw is None:
            return Response(
                {"detail": "budget is required"}, status=status.HTTP_400_BAD_REQUEST
            )

        # Ownership check: short-circuit when the caller has been stamped as
        # bot_owner for this bot id at least once. For brand-new bots that
        # have never been billed yet, fall back to asking SB (the owner of
        # the Bot table).
        owner_known_locally = Transaction.objects.filter(
            bot_id=bot_pk,
            bot_owner=request.user,
        ).exists()
        if not owner_known_locally:
            config = SocraticBooksClient.get_bot_billing_config(bot_pk)
            if config is None:
                return Response(
                    {"detail": "bot not found"},
                    status=status.HTTP_404_NOT_FOUND,
                )
            if config.owner_dare_user_id != request.user.id:
                return Response(
                    {"detail": "You do not own this bot."},
                    status=status.HTTP_403_FORBIDDEN,
                )

        try:
            new_cap = Decimal(str(budget_raw))
        except Exception:
            return Response(
                {"detail": "budget must be a number"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        ok, body = SocraticBooksClient.update_bot_cap(bot_pk, new_cap)
        if ok:
            return Response(body)
        # Surface SB's discriminated error code with appropriate HTTP status.
        sb_error = (body or {}).get("error")
        http_status = (
            status.HTTP_404_NOT_FOUND
            if sb_error == "Bot not found"
            else status.HTTP_400_BAD_REQUEST
        )
        return Response(body or {"detail": "cap update failed"}, status=http_status)

    @action(
        detail=True, methods=["get"], url_path="transactions/(?P<transaction_id>[^/.]+)"
    )
    def transaction_detail(self, request, pk=None, transaction_id=None):
        platform = detect_platform_from_request(request)

        try:
            transaction = Transaction.objects.get(
                id=transaction_id, user=request.user, platform=platform
            )
            serializer = TransactionSerializer(transaction)
            return Response(serializer.data)
        except Transaction.DoesNotExist:
            return Response(
                {"detail": "Transaction not found."}, status=status.HTTP_404_NOT_FOUND
            )

    @action(
        detail=False,
        methods=["put", "delete"],
        permission_classes=[IsAuthenticated, IsSuperAdmin],
        url_path=r"users/(?P<user_id>[^/.]+)/refill-override",
    )
    def admin_user_refill_override(self, request, user_id=None):
        """
        Admin endpoint to upsert or clear a per-user refill override.
        Scope: platform-wide (any user, any group).
        """
        try:
            target = User.objects.get(pk=user_id)
        except User.DoesNotExist:
            return Response(
                {"detail": "User not found."}, status=status.HTTP_404_NOT_FOUND
            )

        if request.method.lower() == "delete":
            UserRefillOverride.objects.filter(user=target).delete()
            return Response(status=status.HTTP_204_NO_CONTENT)

        serializer = UpsertUserOverrideSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        try:
            override = GroupWalletService.upsert_user_override(
                UpsertUserOverrideRequest(
                    owner_or_admin=request.user,
                    target_user_id=target.id,
                    refill_amount=data.get("refill_amount"),
                    refill_period_days=data.get("refill_period_days"),
                    reason=data.get("reason", ""),
                    clear_amount=data.get("clear_amount", False),
                    clear_period=data.get("clear_period", False),
                )
            )
        except ValidationError as exc:
            return _validation_response(exc)
        if override is None:
            return Response(None)
        return Response(UserRefillOverrideSerializer(override).data)

    def paginate_queryset(self, queryset):
        if not hasattr(self, "paginator"):
            self.paginator = self.pagination_class()
        return self.paginator.paginate_queryset(queryset, self.request, view=self)

    def get_paginated_response(self, data):
        assert hasattr(self, "paginator")
        return self.paginator.get_paginated_response(data)

    # ===== Multi-wallet endpoints =========================================

    @action(detail=False, methods=["get"], url_path="wallets")
    def wallets(self, request):
        """
        Unified list of every wallet the caller can route through:
        - DARE Wallet (always present)
        - BYO keys (gated by BYOKeyFeatureFlag.is_byo_enabled())
        - LiteLLM keys (user-self-served + admin-issued individual + cohort)

        Response shape uses `kind`/`type` discriminators with type-specific
        named fields per the data-schema-contract rule (no wire-level unions).
        """
        user = request.user
        pref = UserWalletPreference.get_or_create_for(user)
        byo_enabled = BYOKeyFeatureFlag.is_byo_enabled()

        wallets_list = []

        # DARE wallet — always present, always default
        try:
            dare_wallet = user.wallet
        except Wallet.DoesNotExist:
            dare_wallet = None

        wallets_list.append(
            {
                "type": UserWalletPreferenceTypeChoice.DARE,
                "ref_id": None,
                "label": "DARE Wallet",
                "is_default": True,
                "is_active": pref.active_wallet_type
                == UserWalletPreferenceTypeChoice.DARE,
                "status": {
                    "kind": "BALANCE",
                    "balance": str(dare_wallet.balance) if dare_wallet else "0.00",
                    "last_refill_at": (
                        dare_wallet.last_refill_at if dare_wallet else None
                    ),
                },
            }
        )

        # BYO is a single *collective* wallet: one row per user, regardless of
        # how many provider keys they've configured. When this wallet is
        # active, the router picks whichever BYO key matches the requested
        # provider at dispatch time. The `provider` field becomes a comma-
        # joined summary of configured providers so the UI can show a chip
        # like "OpenAI, Claude" without requiring an extra fetch.
        if byo_enabled:
            byo_qs = (
                UserProviderAPIKey.active_objects.filter(user=user)
                .exclude(api_key__isnull=True)
                .exclude(api_key="")
            )
            configured_providers = list(byo_qs.values_list("provider", flat=True))
            if configured_providers:
                wallets_list.append(
                    {
                        "type": UserWalletPreferenceTypeChoice.BYO,
                        "ref_id": None,
                        "label": "BYO Wallet",
                        "provider": ", ".join(sorted(configured_providers)),
                        "is_default": False,
                        "is_active": (
                            pref.active_wallet_type
                            == UserWalletPreferenceTypeChoice.BYO
                        ),
                        "status": {"kind": "EXTERNAL"},
                    }
                )

        # LiteLLM keys — visible-for-user already filters expiry + group membership
        # and select_related's source_group for the cohort name (no N+1).
        litellm_qs = LiteLLMKey.visible_for_user(user)
        for key in litellm_qs:
            group_name = key.source_group.access_code if key.source_group else None
            wallets_list.append(
                {
                    "type": UserWalletPreferenceTypeChoice.LITELLM,
                    "ref_id": str(key.pk),
                    "label": key.label,
                    "source": key.source,
                    "group_name": group_name,
                    "expires_at": key.expires_at,
                    "base_url": key.base_url,
                    "is_default": False,
                    "is_active": (
                        pref.active_wallet_type
                        == UserWalletPreferenceTypeChoice.LITELLM
                        and pref.active_wallet_ref_id == str(key.pk)
                    ),
                    "status": {"kind": "EXTERNAL"},
                }
            )

        body = {
            "active_wallet": {
                "type": pref.active_wallet_type,
                "ref_id": pref.active_wallet_ref_id,
            },
            "byo_enabled": byo_enabled,
            "wallets": wallets_list,
        }
        # Use serializer purely for shape validation / camelCase rendering.
        return Response(WalletsListResponseSerializer(body).data)

    @action(detail=False, methods=["put"], url_path="wallets/active")
    def set_active_wallet(self, request):
        """
        Body: { "type": "DARE"|"BYO"|"LITELLM", "refId": "..." | null }

        Validates: ref must exist, must belong to the caller, must be
        non-expired (LiteLLM), and the BYO flag must be on (BYO).
        UserWalletPreference.full_clean() enforces the same invariants on save.
        """
        serializer = SetActiveWalletRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        wallet_type = serializer.validated_data["type"]
        ref_id = serializer.validated_data.get("ref_id") or None

        pref = UserWalletPreference.get_or_create_for(request.user)

        if wallet_type == UserWalletPreferenceTypeChoice.DARE:
            pref.active_wallet_type = UserWalletPreferenceTypeChoice.DARE
            pref.active_wallet_ref_id = None
        elif wallet_type == UserWalletPreferenceTypeChoice.BYO:
            if not BYOKeyFeatureFlag.is_byo_enabled():
                return Response(
                    {
                        "code": "BYO_DISABLED",
                        "message": _("BYO wallet type is currently disabled."),
                    },
                    status=status.HTTP_403_FORBIDDEN,
                )
            # Collective BYO: ref_id None means "use whatever BYO key matches
            # the requested provider at dispatch time". We just need at least
            # one populated key on file.
            if not ref_id:
                has_any_byo = (
                    UserProviderAPIKey.active_objects.filter(user=request.user)
                    .exclude(api_key__isnull=True)
                    .exclude(api_key="")
                    .exists()
                )
                if not has_any_byo:
                    return Response(
                        {
                            "code": "BYOK_NO_KEYS",
                            "message": _(
                                "Add at least one BYO provider key before setting BYO active."
                            ),
                        },
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                pref.active_wallet_type = UserWalletPreferenceTypeChoice.BYO
                pref.active_wallet_ref_id = None
            else:
                if not UserProviderAPIKey.active_objects.filter(
                    pk=ref_id, user=request.user
                ).exists():
                    return Response(
                        {
                            "code": "WALLET_NOT_FOUND",
                            "message": _("BYO key not found."),
                        },
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                pref.active_wallet_type = UserWalletPreferenceTypeChoice.BYO
                pref.active_wallet_ref_id = ref_id
        elif wallet_type == UserWalletPreferenceTypeChoice.LITELLM:
            visible_keys = LiteLLMKey.visible_for_user(request.user)
            if not ref_id:
                if not visible_keys.exists():
                    return Response(
                        {
                            "code": "LITELLM_NO_KEYS",
                            "message": _(
                                "Set up a LiteLLM key before setting LITELLM active."
                            ),
                        },
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                return Response(
                    {
                        "code": "WALLET_NEEDS_REF",
                        "message": _("Choose which LiteLLM key to activate."),
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if not visible_keys.filter(pk=ref_id).exists():
                return Response(
                    {
                        "code": "WALLET_NOT_FOUND",
                        "message": _("LiteLLM key not found or no longer accessible."),
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
            pref.active_wallet_type = UserWalletPreferenceTypeChoice.LITELLM
            pref.active_wallet_ref_id = ref_id

        try:
            pref.save()
        except ValidationError as exc:
            return _validation_response(exc)

        return Response(
            {
                "active_wallet": ActiveWalletRefSerializer(
                    {
                        "type": pref.active_wallet_type,
                        "ref_id": pref.active_wallet_ref_id,
                    }
                ).data,
            }
        )

    @action(detail=False, methods=["get"], url_path="feature-flags")
    def feature_flags(self, request):
        """
        Lightweight endpoint so the FE can hide the "+ BYO Key" button before
        any add attempt.
        """
        return Response(
            FeatureFlagsSerializer(
                {
                    "byo_enabled": BYOKeyFeatureFlag.is_byo_enabled(),
                }
            ).data
        )


class LiteLLMKeyViewSet(
    viewsets.GenericViewSet,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
):
    """
    User-scoped CRUD on the caller's *self-served* LiteLLM keys.

    Admin-issued individual (`ADMIN_USER`) and cohort (`ADMIN_GROUP`) keys are
    visible to the user via GET /wallets/ but are not mutable here — those are
    managed by superadmins via Django admin per spec §5.
    """

    permission_classes = [IsAuthenticated]
    serializer_class = LiteLLMKeyReadSerializer

    def get_queryset(self):
        # Only the caller's USER-source keys are mutable here.
        return LiteLLMKey.objects.filter(
            source=LiteLLMKeySourceChoice.USER,
            owner_user=self.request.user,
        )

    def create(self, request, *args, **kwargs):
        write = LiteLLMKeyCreateSerializer(data=request.data)
        write.is_valid(raise_exception=True)
        key = LiteLLMKey.objects.create(
            label=write.validated_data["label"],
            base_url=write.validated_data["base_url"],
            api_key=write.validated_data[
                "api_key"
            ],  # EncryptedCharField encrypts on save
            source=LiteLLMKeySourceChoice.USER,
            owner_user=request.user,
            created_by=request.user,
        )
        return Response(
            LiteLLMKeyReadSerializer(key).data, status=status.HTTP_201_CREATED
        )

    def partial_update(self, request, *args, **kwargs):
        instance = self.get_object()
        write = LiteLLMKeyRenameSerializer(data=request.data, partial=True)
        write.is_valid(raise_exception=True)
        instance.label = write.validated_data["label"]
        instance.save(update_fields=["label", "updated_at"])
        return Response(LiteLLMKeyReadSerializer(instance).data)

    def destroy(self, request, *args, **kwargs):
        # `reset_pref_on_litellm_delete` (billing/signals.py) handles the
        # cascade-reset of UserWalletPreference for any user whose active
        # wallet pointed at this key.
        return super().destroy(request, *args, **kwargs)

    @action(detail=False, methods=["post"], url_path="test")
    def test_unsaved(self, request):
        """Probe an unsaved {base_url, api_key} pair so the modal can verify
        the LiteLLM proxy is reachable before persisting the row. Always 200;
        a failed probe is communicated via the `ok` field in the body."""
        s = LiteLLMTestRequestSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        result = probe_litellm_connection(
            s.validated_data["base_url"],
            s.validated_data["api_key"],
        )
        return Response(
            {"ok": result.ok, "models": result.model_names, "error": result.error}
        )

    @action(detail=True, methods=["post"], url_path="test")
    def test_saved(self, request, pk=None):
        """Probe a stored key. Restricted to keys the user owns (USER source)
        per the viewset's queryset filter."""
        key = self.get_object()
        result = probe_litellm_connection(key.base_url, key.api_key)
        return Response(
            {"ok": result.ok, "models": result.model_names, "error": result.error}
        )


class SystemRefillPolicyViewSet(viewsets.ViewSet):
    """Singleton endpoints for reading/updating the platform refill default. Admin-only."""

    permission_classes = [IsAuthenticated, IsSuperAdmin]

    def list(self, request):
        policy = SystemRefillPolicy.load()
        return Response(SystemRefillPolicySerializer(policy).data)

    def partial_update(self, request, pk=None):
        policy = SystemRefillPolicy.load()
        serializer = SystemRefillPolicySerializer(
            policy, data=request.data, partial=True
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


class GroupWalletViewSet(viewsets.GenericViewSet, mixins.UpdateModelMixin):
    """
    Owner-scoped endpoints for managing a group's wallet policy and allocations.
    Admins can also operate on any group via the admin-only actions (fund).
    """

    permission_classes = [IsAuthenticated]
    serializer_class = GroupWalletReadSerializer

    def get_queryset(self):
        user = self.request.user
        qs = GroupWallet.objects.select_related("group", "group__group_owner")
        if GroupWalletService.is_admin(user):
            return qs
        return qs.filter(group__group_owner=user, group__is_active=True)

    # --- Owner-facing reads ------------------------------------------------

    @action(detail=False, methods=["get"], url_path="owned")
    def owned(self, request):
        groups = GroupWalletService.list_owned_groups(request.user)
        serializer = OwnedGroupSerializer(groups, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=["get"], url_path="members")
    def members(self, request, pk=None):
        group_wallet = self.get_object()
        users = group_wallet.group.users.all().select_related(
            "wallet", "refill_override"
        )
        serializer = MemberRowSerializer(users, many=True)
        return Response(serializer.data)

    # --- Owner-facing writes ----------------------------------------------

    def partial_update(self, request, pk=None):
        group_wallet = self.get_object()
        serializer = GroupWalletWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        try:
            updated = GroupWalletService.update_group_policy(
                UpdateGroupPolicyRequest(
                    group_wallet_id=group_wallet.id,
                    owner=request.user,
                    refill_amount=data.get("refill_amount"),
                    refill_period_days=data.get("refill_period_days"),
                    is_active=data.get("is_active"),
                    clear_amount=data.get("clear_amount", False),
                    clear_period=data.get("clear_period", False),
                )
            )
        except PermissionDenied as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_403_FORBIDDEN)
        except ValidationError as exc:
            return _validation_response(exc)

        return Response(GroupWalletReadSerializer(updated).data)

    @action(detail=True, methods=["post"], url_path="allocate")
    def allocate(self, request, pk=None):
        group_wallet = self.get_object()
        serializer = AllocateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        try:
            _owner_row, member_row = GroupWalletService.allocate_to_member(
                AllocateToMemberRequest(
                    group_wallet_id=group_wallet.id,
                    owner=request.user,
                    recipient_user_id=data["recipient_user_id"],
                    amount=data["amount"],
                    note=data.get("note", ""),
                )
            )
        except PermissionDenied as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_403_FORBIDDEN)
        except ValidationError as exc:
            return _validation_response(exc)
        except User.DoesNotExist:
            return Response(
                {"detail": "Recipient not found."}, status=status.HTTP_404_NOT_FOUND
            )

        group_wallet.refresh_from_db()
        recipient = User.objects.select_related("wallet", "refill_override").get(
            pk=data["recipient_user_id"]
        )
        return Response(
            {
                "groupWallet": GroupWalletReadSerializer(group_wallet).data,
                "transaction": TransactionSerializer(member_row).data,
                "recipient": MemberRowSerializer(recipient).data,
            }
        )

    @action(
        detail=True,
        methods=["post"],
        url_path="fund",
        permission_classes=[IsAuthenticated, IsSuperAdmin],
    )
    def fund(self, request, pk=None):
        group_wallet = self.get_object()
        serializer = FundBudgetSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        try:
            updated = GroupWalletService.fund_group_budget(
                FundGroupBudgetRequest(
                    group_wallet_id=group_wallet.id,
                    actor=request.user,
                    amount=data["amount"],
                    note=data.get("note", ""),
                )
            )
        except PermissionDenied as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_403_FORBIDDEN)
        except ValidationError as exc:
            return _validation_response(exc)
        return Response(GroupWalletReadSerializer(updated).data)

    # --- Per-member override (owner or admin) ------------------------------

    @action(
        detail=True,
        methods=["put", "delete"],
        url_path=r"members/(?P<user_id>[^/.]+)/override",
    )
    def member_override(self, request, pk=None, user_id=None):
        group_wallet = self.get_object()
        try:
            target = User.objects.get(pk=user_id)
        except User.DoesNotExist:
            return Response(
                {"detail": "Member not found."}, status=status.HTTP_404_NOT_FOUND
            )

        if target.access_code_group_id != group_wallet.group_id:
            return Response(
                {"detail": "User is not a member of this group."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if request.method.lower() == "delete":
            try:
                GroupWalletService.remove_user_override(request.user, target.id)
            except PermissionDenied as exc:
                return Response({"detail": str(exc)}, status=status.HTTP_403_FORBIDDEN)
            return Response(status=status.HTTP_204_NO_CONTENT)

        serializer = UpsertUserOverrideSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        try:
            override = GroupWalletService.upsert_user_override(
                UpsertUserOverrideRequest(
                    owner_or_admin=request.user,
                    target_user_id=target.id,
                    refill_amount=data.get("refill_amount"),
                    refill_period_days=data.get("refill_period_days"),
                    reason=data.get("reason", ""),
                    clear_amount=data.get("clear_amount", False),
                    clear_period=data.get("clear_period", False),
                )
            )
        except PermissionDenied as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_403_FORBIDDEN)
        except ValidationError as exc:
            return _validation_response(exc)

        target.refresh_from_db()
        return Response(
            {
                "override": (
                    UserRefillOverrideSerializer(override).data if override else None
                ),
                "member": MemberRowSerializer(target).data,
            }
        )
