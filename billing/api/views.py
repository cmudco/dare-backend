from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from common.permissions import IsOwner
from billing.api.serializers import WalletSerializer, TransactionSerializer
from billing.models import Transaction
from common.pagination import CustomPageNumberPagination  

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
        List all transactions for the authenticated user.
        """
        queryset = Transaction.objects.filter(
            user=request.user
        ).order_by('-created_at')

        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = TransactionSerializer(page, many=True)
            return self.get_paginated_response(serializer.data)

        serializer = TransactionSerializer(queryset, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=['get'], url_path='transactions/(?P<transaction_id>[^/.]+)')
    def transaction_detail(self, request, pk=None, transaction_id=None):
        """
        Retrieve a specific transaction.
        """
        try:
            transaction = Transaction.objects.get(
                id=transaction_id,
                user=request.user
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