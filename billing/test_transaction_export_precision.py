from decimal import Decimal
from types import SimpleNamespace

from django.test import SimpleTestCase

from billing.api.serializers import TransactionSerializer
from billing.api.views import TransactionHistoryPagination
from billing.models import Transaction


class TransactionExportPrecisionTests(SimpleTestCase):
    def test_raw_costs_preserve_six_decimals_without_display_rounding(self):
        transaction = Transaction(
            amount=Decimal("0.012345"),
            reference_amount=Decimal("0.000001"),
        )
        data = TransactionSerializer(transaction).data
        self.assertEqual(data["amount"], "0.012345")
        self.assertEqual(data["reference_amount"], "0.000001")
        self.assertEqual(data["display_amount"], "$0.01")

    def test_zero_and_missing_reference_cost_are_distinct(self):
        data = TransactionSerializer(
            Transaction(amount=Decimal("0"), reference_amount=None)
        ).data
        self.assertEqual(data["amount"], "0.000000")
        self.assertIsNone(data["reference_amount"])

    def test_history_page_size_is_bounded_and_preserves_default(self):
        pagination = TransactionHistoryPagination()
        self.assertEqual(pagination.get_page_size(SimpleNamespace(query_params={})), 10)
        self.assertEqual(
            pagination.get_page_size(
                SimpleNamespace(query_params={"page_size": "500"})
            ),
            500,
        )
        self.assertEqual(
            pagination.get_page_size(
                SimpleNamespace(query_params={"page_size": "9999"})
            ),
            500,
        )
