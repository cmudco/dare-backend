import os
import uuid

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.storage import default_storage
from django.db.models import Sum
from django.utils import timezone
from django_rq import enqueue, get_queue
from dj_rest_auth.registration.views import VerifyEmailView
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken


from billing.constants import TransactionTypeChoice
from billing.models import Transaction
from conversations.constants import SenderType
from conversations.models import Conversation, Message
from files.models import File
from prompts.models import Prompt
from users.constants import VectorDBChoice, AuthSourceChoice, RoleChoice
from users.models import AccessCodeGroup
from users.services import AvatarService, AvatarValidationError

User = get_user_model()


class CustomVerifyEmailView(VerifyEmailView):
    """
    Custom email verification view that returns JWT tokens after successful verification.
    This enables auto-login after email verification for a smoother onboarding experience.
    
    Only DARE users receive tokens - Socratic Bots users are redirected to their own
    frontend and can't use tokens stored in DARE's localStorage anyway.
    """
    def post(self, request, *args, **kwargs):
        # Validate the key first
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        # Set the key in kwargs so get_object() works (like parent class does)
        self.kwargs['key'] = serializer.validated_data['key']
        
        # Get the confirmation object and confirm it
        confirmation = self.get_object()
        confirmation.confirm(self.request)
        
        # Get the user from the confirmation
        user = confirmation.email_address.user
        
        # Prepare response
        response_data = {'detail': 'ok'}
        
        # Only generate JWT tokens for DARE users (auto-login)
        # Socratic Bots users will be redirected to their frontend where these tokens wouldn't be accessible
        if user.auth_source == AuthSourceChoice.DARE:
            refresh = RefreshToken.for_user(user)
            response_data['access'] = str(refresh.access_token)
            response_data['refresh'] = str(refresh)
        
        return Response(response_data, status=status.HTTP_200_OK)




class UserStatsView(APIView):

    def get(self, request, *args, **kwargs):
        user = request.user

        prompt_count = Prompt.active_objects.filter(user=user).count()

        file_count = File.active_objects.filter(user=user).count()

        conversation_count = Conversation.active_objects.filter(user=user).count()

        message_count = Message.active_objects.filter(conversation__user=user).count()

        ai_message_count = Message.active_objects.filter(
            conversation__user=user,
            sender_type=SenderType.AI_ASSISTANT
        ).count()

        tagged_files_count = File.active_objects.filter(user=user, tags__isnull=False).count()

        token_stats = Transaction.objects.filter(
            user=user,
            type=TransactionTypeChoice.DEBIT,
            llm__isnull=False
        ).aggregate(
            total_input_tokens=Sum('input_tokens'),
            total_output_tokens=Sum('output_tokens')
        )

        stats = {
            'prompt_count': prompt_count,
            'file_count': file_count,
            'conversation_count': conversation_count,
            'message_count': message_count,
            'ai_message_count': ai_message_count,
            'tagged_files_count': tagged_files_count,
            'total_input_tokens': token_stats['total_input_tokens'] or 0,
            'total_output_tokens': token_stats['total_output_tokens'] or 0,
            'total_tokens': (token_stats['total_input_tokens'] or 0) + (token_stats['total_output_tokens'] or 0)
        }

        return Response(stats, status=status.HTTP_200_OK)

class VectorDBViewSet(viewsets.ViewSet):
    """
    ViewSet for managing user's vector database preference.
    """
    permission_classes = [IsAuthenticated]

    def get_vector_db_response(self, vector_db):
        """Create a standardized response for vector DB data."""
        try:
            vector_db_name = dict(VectorDBChoice.choices).get(vector_db, "Unknown")
            return {
                "vector_db": vector_db,
                "vector_db_name": vector_db_name
            }
        except Exception:
            return {
                "vector_db": VectorDBChoice.WEAVIATE,
                "vector_db_name": dict(VectorDBChoice.choices).get(VectorDBChoice.WEAVIATE)
            }

    @action(detail=False, methods=['get', 'post'])
    def preference(self, request):
        """
        Get or update the vector DB setting for the authenticated user.

        GET: Returns the current vector DB setting
        POST: Updates the vector DB setting and starts migration
        """
        if request.method == 'GET':
            try:
                user = request.user
                vector_db_value = user.vector_db
                return Response(self.get_vector_db_response(vector_db_value))
            except Exception as e:
                return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        vector_db = request.data.get('vector_db')

        if vector_db is None:
            return Response(
                {"error": "vector_db field is required"},
                status=status.HTTP_400_BAD_REQUEST
            )

        valid_choices = [choice[0] for choice in VectorDBChoice.choices]
        if vector_db not in valid_choices:
            return Response(
                {"error": f"Invalid vector_db value. Must be one of: {valid_choices}"},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            current_db = request.user.vector_db

            if current_db == vector_db:
                return Response(self.get_vector_db_response(vector_db))


            request.user.vector_db = vector_db
            request.user.save(update_fields=['vector_db'])

            return Response({
                **self.get_vector_db_response(vector_db),
                "migration_status": "queued"
            })
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=False, methods=['get'])
    def migration_status(self, request):
        """Get the status of the current migration job."""
        try:
            queue = get_queue()

            return Response({
                "status": "No migration in progress",
                "current_vector_db": self.get_vector_db_response(request.user.vector_db)
            })
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

class ChunkingSettingsViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]

    @action(detail=False, methods=['get', 'post', 'patch'], url_path='settings')
    def config(self, request):
        user = request.user

        if request.method == 'GET':
            return Response({
                "chunk_size": user.chunk_size,
                "overlap_size": user.overlap_size
            })

        chunk_size = request.data.get('chunk_size')
        overlap_size = request.data.get('overlap_size')

        if chunk_size is None or overlap_size is None:
            return Response(
                {"error": "chunk_size and overlap_size fields are required"},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            chunk_size = int(chunk_size)
            overlap_size = int(overlap_size)

            if chunk_size <= 0:
                return Response(
                    {"error": "chunk_size  must be positive"},
                    status=status.HTTP_400_BAD_REQUEST
                )

            if overlap_size < 0 or overlap_size >= chunk_size:
                return Response(
                    {"error": "overlap_size must be non-negative and less than chunk_size"},
                    status=status.HTTP_400_BAD_REQUEST
                )

            user.chunk_size = chunk_size
            user.overlap_size = overlap_size
            user.save(update_fields=["chunk_size", "overlap_size"])

            return Response({
                "chunk_size": user.chunk_size,
                "overlap_size": user.overlap_size
            })
        except ValueError:
            return Response(
                {"error": "chunk_size and overlap_size must be integers"},
                status=status.HTTP_400_BAD_REQUEST
            )


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def token_health_check(request):
    """
    Health check endpoint to verify JWT token validity.

    Returns user info if token is valid, 401 if expired/invalid.
    Used by frontend to periodically check token status.
    """
    try:
        user = request.user
        return Response({
            'status': 'valid',
            'user_id': user.id,
            'username': user.username if hasattr(user, 'username') else user.email,
            'is_active': user.is_active,
            'timestamp': timezone.now().isoformat()
        }, status=status.HTTP_200_OK)
    except Exception:
        return Response({
            'status': 'invalid',
            'detail': 'Token validation failed'
        }, status=status.HTTP_401_UNAUTHORIZED)

class AccessCodeCheckView(APIView):
    """
    Cross-platform user validation endpoint.

    Used by SocraticBots backend for cross-platform validation.
    Checks if a user exists in DARE and can access both platforms.
    """
    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        """
        Check if access code exists in DARE backend

        Expected input:
        {
            "access_code": "ABC123"
        }

        Returns:
        {
            "exists": true/false,
            "default_role": "USER", "CREATOR", etc.,
            "available_slots": integer,
            "message": "descriptive message"
        }
        """
        access_code = request.data.get('access_code')

        if not access_code:
            return Response(
                {
                    "exists": False,
                    "default_role": None,
                    "available_slots": 0,
                    "message": "Access code is required"
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            code_group = AccessCodeGroup.objects.get(access_code=access_code)

            # Check if code is available
            if not code_group.is_available:
                if code_group.is_expired:
                    message = "Access code exists but has expired"
                elif not code_group.is_active:
                    message = "Access code exists but is inactive"
                else:
                    message = "Access code exists but has reached maximum capacity"

                return Response({
                    "exists": True,
                    "default_role": code_group.default_role,
                    "available_slots": 0,
                    "message": message
                })

            # Code exists and is available
            available_slots = code_group.max_capacity - code_group.current_usage

            return Response({
                "exists": True,
                "default_role": code_group.default_role,
                "available_slots": available_slots,
                "message": f"Access code is available with {code_group.get_default_role_display()} role"
            })

        except AccessCodeGroup.DoesNotExist:
            return Response({
                "exists": False,
                "default_role": None,
                "available_slots": 0,
                "message": "Access code not found"
            })


class AvatarViewSet(viewsets.ViewSet):
    """ViewSet for managing user avatar/profile pictures."""

    permission_classes = [IsAuthenticated]

    @action(detail=False, methods=["post"], url_path="upload")
    def upload(self, request):
        """Upload a new avatar image."""
        try:
            avatar_file = request.FILES.get("avatar")
            avatar_url = AvatarService.upload_avatar(request.user, avatar_file, request)

            return Response({
                "avatar_url": avatar_url,
                "avatar_type": "custom",
                "message": "Avatar uploaded successfully"
            })

        except AvatarValidationError as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

        except Exception as e:
            return Response(
                {"error": f"Failed to upload avatar: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    @action(detail=False, methods=["delete"], url_path="remove")
    def remove(self, request):
        """Remove current avatar and reset to initials."""
        try:
            AvatarService.remove_avatar(request.user)

            return Response({
                "avatar_type": "initials",
                "message": "Avatar removed successfully"
            })

        except Exception as e:
            return Response(
                {"error": f"Failed to remove avatar: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class InternalSetRoleView(APIView):
    """
    Internal endpoint for inter-service communication.
    Allows SB backend to set a user's platform_role during migrations.

    Authenticated via X-Internal-Key header (shared secret).
    """
    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        # Verify internal key
        internal_key = request.headers.get('X-Internal-Key', '')
        expected_key = getattr(settings, 'DARE_INTERNAL_KEY', '')
        if not internal_key or internal_key != expected_key:
            return Response(
                {"error": "Unauthorized"},
                status=status.HTTP_403_FORBIDDEN
            )

        user_id = request.data.get('user_id')
        platform_role = request.data.get('platform_role')

        if not user_id or not platform_role:
            return Response(
                {"error": "user_id and platform_role are required"},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Validate role value
        valid_roles = [choice[0] for choice in RoleChoice.choices]
        if platform_role not in valid_roles:
            return Response(
                {"error": f"Invalid platform_role. Must be one of: {valid_roles}"},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            user = User.objects.get(id=user_id)
            old_role = user.platform_role
            user.platform_role = platform_role
            user.save(update_fields=['platform_role'])

            return Response({
                "success": True,
                "user_id": user.id,
                "email": user.email,
                "old_role": old_role,
                "new_role": platform_role
            })

        except User.DoesNotExist:
            return Response(
                {"error": f"User with id={user_id} not found"},
                status=status.HTTP_404_NOT_FOUND
            )