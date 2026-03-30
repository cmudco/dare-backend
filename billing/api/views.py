from django.db.models import Sum, Count
from django.utils import timezone
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from common.permissions import IsOwner
from billing.api.serializers import WalletSerializer, TransactionSerializer
from billing.models import Transaction
from billing.constants import TransactionTypeChoice
from common.pagination import CustomPageNumberPagination
from conversations.models import Message
from core.services.energy_service import compute_relatable_stats
from users.utils import detect_platform_from_request

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

    @action(detail=False, methods=['get'])
    def transactions(self, request):
        """
        List all transactions for the authenticated user filtered by platform.

        Each platform (DARE or SocraticBots) only sees its own transactions.
        """
        # Detect platform from request headers
        platform = detect_platform_from_request(request)

        # Filter transactions by user AND platform
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
        """
        Get per-model token usage and cost statistics for the authenticated user
        filtered by platform.

        Each platform (DARE or SocraticBots) only sees its own statistics.
        """
        # Detect platform from request headers
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
        """
        Get aggregated energy/environmental impact stats for the authenticated user.

        Query parameters:
            period: "7d", "30d", "90d", "all" (default: "all")

        Returns overall totals, relatable stats, and per-model breakdown.
        """
        platform = detect_platform_from_request(request)
        period = request.query_params.get("period", "all")

        # Base queryset: user's messages with energy data via conversation source
        base_qs = Message.active_objects.filter(
            conversation__user=request.user,
            conversation__source=platform,
            energy_wh__isnull=False,
            energy_wh__gt=0,
        )

        # Apply date filter
        if period != "all":
            days_map = {"7d": 7, "30d": 30, "90d": 90}
            days = days_map.get(period, 0)
            if days:
                cutoff = timezone.now() - timezone.timedelta(days=days)
                base_qs = base_qs.filter(created_at__gte=cutoff)

        # Overall aggregates
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

        # Relatable stats from total energy
        relatable = compute_relatable_stats(total_energy)

        # Per-model breakdown
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
        """
        Retrieve a specific transaction filtered by platform.

        Users can only view transactions from their current platform.
        """
        # Detect platform from request headers
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

    def paginate_queryset(self, queryset):
        """
        Return a paginated queryset.
        """
        if not hasattr(self, 'paginator'):
            self.paginator = self.pagination_class()
        return self.paginator.paginate_queryset(queryset, self.request, view=self)

    def get_paginated_response(self, data):
        """
        Return a paginated response.
        """
        assert hasattr(self, 'paginator')
        return self.paginator.get_paginated_response(data)