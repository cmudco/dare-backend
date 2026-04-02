from rest_framework import serializers


class VerifyOtpSerializer(serializers.Serializer):
    code = serializers.CharField(required=True, max_length=20)
