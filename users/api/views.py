from django.db.models import Sum
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from django.contrib.auth import get_user_model
from django_rq import enqueue

from conversations.constants import SenderType
from conversations.models import Conversation, Message
from files.models import File
from prompts.models import Prompt
from users.constants import VectorDBChoice

User = get_user_model()

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


        token_stats = Message.active_objects.filter(
            conversation__user=user,
            sender_type=SenderType.AI_ASSISTANT
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
            from django_rq import get_queue

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