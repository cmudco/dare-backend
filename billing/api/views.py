from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Count, Sum
from django.utils import timezone
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from billing.api.serializers import (
    AllocateSerializer,
    EffectivePolicySerializer,
    FundBudgetSerializer,
    GroupWalletReadSerializer,
    GroupWalletWriteSerializer,
    MemberRowSerializer,
    OwnedGroupSerializer,
    SystemRefillPolicySerializer,
    TransactionSerializer,
    UpsertUserOverrideSerializer,
    UserRefillOverrideSerializer,
    WalletSerializer,
)
from billing.constants import TransactionTypeChoice
from billing.group_wallet_service import (
    AllocateToMemberRequest,
    FundGroupBudgetRequest,
    GroupWalletService,
    UpdateGroupPolicyRequest,
    UpsertUserOverrideRequest,
)
from billing.models import (
    GroupWallet,
    SystemRefillPolicy,
    Transaction,
    UserRefillOverride,
)
from billing.services import WalletService
from common.pagination import CustomPageNumberPagination
from common.permissions import IsSuperAdmin
from conversations.models import Message
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

    @action(detail=False, methods=['get'])
    def wallet(self, request):
        """
        Get the wallet details for the authenticated user.
        """
        wallet = request.user.wallet
        serializer = WalletSerializer(wallet)
        return Response(serializer.data)

    @action(detail=False, methods=['get'], url_path='effective-policy')
    def effective_policy(self, request):
        """Return the caller's resolved refill policy (amount + period + sources)."""
        policy = WalletService.get_effective_refill_policy(request.user)
        return Response(EffectivePolicySerializer(policy).data)

    @action(detail=False, methods=['get'])
    def transactions(self, request):
        """
        List all transactions for the authenticated user filtered by platform.

        Each platform (DARE or SocraticBots) only sees its own transactions.
        """
        platform = detect_platform_from_request(request)

        queryset = Transaction.objects.filter(
            user=request.user,
            platform=platform
        ).order_by('-created_at')

        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = TransactionSerializer(page, many=True)
            return self.get_paginated_response(serializer.data)

        serializer = TransactionSerializer(queryset, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=['get'])
    def model_stats(self, request):
        platform = detect_platform_from_request(request)

        per_model_stats = Transaction.objects.filter(
            user=request.user,
            type=TransactionTypeChoice.DEBIT,
            llm__isnull=False,
            platform=platform
        ).values(
            'llm__id',
            'llm__name',
            'llm__identifier',
            'llm__provider'
        ).annotate(
            total_cost=Sum('amount'),
            input_tokens=Sum('input_tokens'),
            output_tokens=Sum('output_tokens'),
            transaction_count=Count('id')
        ).order_by('-total_cost')

        models_billing_stats = []
        for stat in per_model_stats:
            input_tokens = stat['input_tokens'] or 0
            output_tokens = stat['output_tokens'] or 0
            total_cost = stat['total_cost'] or 0

            models_billing_stats.append({
                'llm_id': stat['llm__id'],
                'llm_name': stat['llm__name'],
                'llm_identifier': stat['llm__identifier'],
                'llm_provider': stat['llm__provider'],
                'input_tokens': input_tokens,
                'output_tokens': output_tokens,
                'total_tokens': input_tokens + output_tokens,
                'total_cost': f"${total_cost:.6f}" if total_cost else "$0.00",
                'total_cost_decimal': total_cost,
                'transaction_count': stat['transaction_count']
            })

        overall_stats = Transaction.objects.filter(
            user=request.user,
            type=TransactionTypeChoice.DEBIT,
            llm__isnull=False,
            platform=platform
        ).aggregate(
            total_cost=Sum('amount'),
            total_input_tokens=Sum('input_tokens'),
            total_output_tokens=Sum('output_tokens'),
            total_transactions=Count('id')
        )

        response_data = {
            'models_billing_stats': models_billing_stats,
            'overall_stats': {
                'total_cost': f"${overall_stats['total_cost']:.6f}" if overall_stats['total_cost'] else "$0.00",
                'total_cost_decimal': overall_stats['total_cost'] or 0,
                'total_input_tokens': overall_stats['total_input_tokens'] or 0,
                'total_output_tokens': overall_stats['total_output_tokens'] or 0,
                'total_tokens': (overall_stats['total_input_tokens'] or 0) + (overall_stats['total_output_tokens'] or 0),
                'total_transactions': overall_stats['total_transactions'] or 0
            }
        }

        return Response(response_data)

    @action(detail=False, methods=['get'], url_path='energy-stats')
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
            base_qs
            .values("llm__id", "llm__name", "llm__identifier", "llm__provider")
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

        return Response({
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
        })

    @action(detail=True, methods=['get'], url_path='transactions/(?P<transaction_id>[^/.]+)')
    def transaction_detail(self, request, pk=None, transaction_id=None):
        platform = detect_platform_from_request(request)

        try:
            transaction = Transaction.objects.get(
                id=transaction_id,
                user=request.user,
                platform=platform
            )
            serializer = TransactionSerializer(transaction)
            return Response(serializer.data)
        except Transaction.DoesNotExist:
            return Response(
                {"detail": "Transaction not found."},
                status=status.HTTP_404_NOT_FOUND
            )

    @action(
        detail=False,
        methods=['put', 'delete'],
        permission_classes=[IsAuthenticated, IsSuperAdmin],
        url_path=r'users/(?P<user_id>[^/.]+)/refill-override',
    )
    def admin_user_refill_override(self, request, user_id=None):
        """
        Admin endpoint to upsert or clear a per-user refill override.
        Scope: platform-wide (any user, any group).
        """
        try:
            target = User.objects.get(pk=user_id)
        except User.DoesNotExist:
            return Response({"detail": "User not found."}, status=status.HTTP_404_NOT_FOUND)

        if request.method.lower() == 'delete':
            UserRefillOverride.objects.filter(user=target).delete()
            return Response(status=status.HTTP_204_NO_CONTENT)

        serializer = UpsertUserOverrideSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        try:
            override = GroupWalletService.upsert_user_override(UpsertUserOverrideRequest(
                owner_or_admin=request.user,
                target_user_id=target.id,
                refill_amount=data.get('refill_amount'),
                refill_period_days=data.get('refill_period_days'),
                reason=data.get('reason', ''),
                clear_amount=data.get('clear_amount', False),
                clear_period=data.get('clear_period', False),
            ))
        except ValidationError as exc:
            return _validation_response(exc)
        if override is None:
            return Response(None)
        return Response(UserRefillOverrideSerializer(override).data)

    def paginate_queryset(self, queryset):
        if not hasattr(self, 'paginator'):
            self.paginator = self.pagination_class()
        return self.paginator.paginate_queryset(queryset, self.request, view=self)

    def get_paginated_response(self, data):
        assert hasattr(self, 'paginator')
        return self.paginator.get_paginated_response(data)


class SystemRefillPolicyViewSet(viewsets.ViewSet):
    """Singleton endpoints for reading/updating the platform refill default. Admin-only."""
    permission_classes = [IsAuthenticated, IsSuperAdmin]

    def list(self, request):
        policy = SystemRefillPolicy.load()
        return Response(SystemRefillPolicySerializer(policy).data)

    def partial_update(self, request, pk=None):
        policy = SystemRefillPolicy.load()
        serializer = SystemRefillPolicySerializer(policy, data=request.data, partial=True)
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

    @action(detail=False, methods=['get'], url_path='owned')
    def owned(self, request):
        groups = GroupWalletService.list_owned_groups(request.user)
        serializer = OwnedGroupSerializer(groups, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=['get'], url_path='members')
    def members(self, request, pk=None):
        group_wallet = self.get_object()
        users = group_wallet.group.users.all().select_related("wallet", "refill_override")
        serializer = MemberRowSerializer(users, many=True)
        return Response(serializer.data)

    # --- Owner-facing writes ----------------------------------------------

    def partial_update(self, request, pk=None):
        group_wallet = self.get_object()
        serializer = GroupWalletWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        try:
            updated = GroupWalletService.update_group_policy(UpdateGroupPolicyRequest(
                group_wallet_id=group_wallet.id,
                owner=request.user,
                refill_amount=data.get('refill_amount'),
                refill_period_days=data.get('refill_period_days'),
                is_active=data.get('is_active'),
                clear_amount=data.get('clear_amount', False),
                clear_period=data.get('clear_period', False),
            ))
        except PermissionDenied as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_403_FORBIDDEN)
        except ValidationError as exc:
            return _validation_response(exc)

        return Response(GroupWalletReadSerializer(updated).data)

    @action(detail=True, methods=['post'], url_path='allocate')
    def allocate(self, request, pk=None):
        group_wallet = self.get_object()
        serializer = AllocateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        try:
            _owner_row, member_row = GroupWalletService.allocate_to_member(AllocateToMemberRequest(
                group_wallet_id=group_wallet.id,
                owner=request.user,
                recipient_user_id=data['recipient_user_id'],
                amount=data['amount'],
                note=data.get('note', ''),
            ))
        except PermissionDenied as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_403_FORBIDDEN)
        except ValidationError as exc:
            return _validation_response(exc)
        except User.DoesNotExist:
            return Response({"detail": "Recipient not found."}, status=status.HTTP_404_NOT_FOUND)

        group_wallet.refresh_from_db()
        recipient = (
            User.objects.select_related("wallet", "refill_override")
            .get(pk=data['recipient_user_id'])
        )
        return Response({
            "groupWallet": GroupWalletReadSerializer(group_wallet).data,
            "transaction": TransactionSerializer(member_row).data,
            "recipient": MemberRowSerializer(recipient).data,
        })

    @action(
        detail=True,
        methods=['post'],
        url_path='fund',
        permission_classes=[IsAuthenticated, IsSuperAdmin],
    )
    def fund(self, request, pk=None):
        group_wallet = self.get_object()
        serializer = FundBudgetSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        try:
            updated = GroupWalletService.fund_group_budget(FundGroupBudgetRequest(
                group_wallet_id=group_wallet.id,
                actor=request.user,
                amount=data['amount'],
                note=data.get('note', ''),
            ))
        except PermissionDenied as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_403_FORBIDDEN)
        except ValidationError as exc:
            return _validation_response(exc)
        return Response(GroupWalletReadSerializer(updated).data)

    # --- Per-member override (owner or admin) ------------------------------

    @action(
        detail=True,
        methods=['put', 'delete'],
        url_path=r'members/(?P<user_id>[^/.]+)/override',
    )
    def member_override(self, request, pk=None, user_id=None):
        group_wallet = self.get_object()
        try:
            target = User.objects.get(pk=user_id)
        except User.DoesNotExist:
            return Response({"detail": "Member not found."}, status=status.HTTP_404_NOT_FOUND)

        if target.access_code_group_id != group_wallet.group_id:
            return Response(
                {"detail": "User is not a member of this group."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if request.method.lower() == 'delete':
            try:
                GroupWalletService.remove_user_override(request.user, target.id)
            except PermissionDenied as exc:
                return Response({"detail": str(exc)}, status=status.HTTP_403_FORBIDDEN)
            return Response(status=status.HTTP_204_NO_CONTENT)

        serializer = UpsertUserOverrideSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        try:
            override = GroupWalletService.upsert_user_override(UpsertUserOverrideRequest(
                owner_or_admin=request.user,
                target_user_id=target.id,
                refill_amount=data.get('refill_amount'),
                refill_period_days=data.get('refill_period_days'),
                reason=data.get('reason', ''),
                clear_amount=data.get('clear_amount', False),
                clear_period=data.get('clear_period', False),
            ))
        except PermissionDenied as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_403_FORBIDDEN)
        except ValidationError as exc:
            return _validation_response(exc)

        target.refresh_from_db()
        return Response({
            "override": UserRefillOverrideSerializer(override).data if override else None,
            "member": MemberRowSerializer(target).data,
        })
