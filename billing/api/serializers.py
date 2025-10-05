from rest_framework import serializers
from billing.models import Transaction, Wallet
from conversations.api.serializers import LLMSerializer


class WalletSerializer(serializers.ModelSerializer):
    display_balance = serializers.CharField(read_only=True)

    class Meta:
        model = Wallet
        fields = ["display_balance", "created_at", "updated_at"]

class TransactionSerializer(serializers.ModelSerializer):
    display_amount = serializers.CharField(read_only=True)
    type = serializers.CharField(source="get_type_display")
    llm  = LLMSerializer(read_only=True)

    class Meta:
        model = Transaction
        fields = ["id", "display_amount", "type", "message", "llm", "llm_name", "input_tokens", "output_tokens", "created_at", "updated_at", ]