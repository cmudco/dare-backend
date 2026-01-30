"""
Custom JWT serializers for adding role and platform claims to JWT tokens.
"""
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer


class CustomTokenObtainPairSerializer(TokenObtainPairSerializer):
    """
    Custom JWT serializer that adds platform role and access claims to the token.

    These claims are used by Socratic Bots to determine user permissions
    without needing additional API calls to DARE.
    """

    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)

        # Add role claims
        token['platform_role'] = user.platform_role
        token['is_superuser'] = user.is_superuser

        # Add platform access flags
        token['is_dare_accessible'] = user.is_dare_accessible
        token['is_socratic_bots_accessible'] = user.is_socratic_bots_accessible

        # Add auth source
        token['auth_source'] = user.auth_source

        return token
