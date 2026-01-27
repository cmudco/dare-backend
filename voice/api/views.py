"""
API Views for Voice agents and conversations.
"""

import logging
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from django.utils import timezone
from django.db.models import Q
from asgiref.sync import async_to_sync

from voice.models import VoiceAgent, VoiceConversation
from voice.constants import VoiceAgentStatus, ConversationStatus
from voice.services.elevenlabs_service import ElevenLabsService
from .serializers import (
    VoiceAgentSerializer,
    VoiceAgentListSerializer,
    VoiceAgentCreateSerializer,
    VoiceAgentUpdateSerializer,
    VoiceConversationSerializer,
    VoiceConversationListSerializer,
    StartConversationSerializer,
    EndConversationSerializer,
)

logger = logging.getLogger(__name__)


class VoiceAgentViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing ElevenLabs Voice Agents.

    Any authenticated user can:
    - Create agents
    - View all active agents + their own agents
    - Update/delete their own agents
    - Take exams on any active agent
    """
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        """
        Return agents based on action:
        - list: all active agents + user's own agents (any status)
        - other: all agents (permission checked in actions)
        """
        user = self.request.user

        if self.action == 'list':
            # User's own agents (any status) + all active agents
            own_agents = Q(user=user, is_deleted=False)
            active_agents = Q(status=VoiceAgentStatus.ACTIVE, is_deleted=False, is_active=True)
            return VoiceAgent.objects.filter(own_agents | active_agents).distinct()

        # For detail views, allow access to check ownership in actions
        return VoiceAgent.objects.filter(is_deleted=False)

    def get_serializer_class(self):
        if self.action == 'list':
            return VoiceAgentListSerializer
        if self.action == 'create':
            return VoiceAgentCreateSerializer
        if self.action in ['update', 'partial_update']:
            return VoiceAgentUpdateSerializer
        return VoiceAgentSerializer

    def create(self, request, *args, **kwargs):
        """Create a new voice agent and sync to ElevenLabs."""
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        # Save to local DB first
        agent = serializer.save(user=request.user)

        # Create on ElevenLabs
        try:
            service = ElevenLabsService()
            config = service.build_agent_config(
                name=agent.name,
                system_prompt=agent.system_prompt,
                voice_id=agent.voice_id,
                temperature=agent.temperature,
                first_message=agent.first_message or agent.exam_instructions,
                max_duration_seconds=agent.max_duration_seconds,
                conversation_config=agent.conversation_config or None,
            )

            result = async_to_sync(service.create_agent)(config)
            agent.elevenlabs_agent_id = result.get('agent_id')
            agent.save(update_fields=['elevenlabs_agent_id'])

            logger.info(f"Created ElevenLabs agent: {agent.elevenlabs_agent_id}")

        except Exception as e:
            # Rollback local creation if ElevenLabs fails
            logger.error(f"Failed to create ElevenLabs agent: {e}")
            agent.delete()
            return Response(
                {"error": f"Failed to create ElevenLabs agent: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

        return Response(
            VoiceAgentSerializer(agent).data,
            status=status.HTTP_201_CREATED
        )

    def update(self, request, *args, **kwargs):
        """Update agent and sync changes to ElevenLabs."""
        instance = self.get_object()

        # Check ownership
        if instance.user != request.user:
            return Response(
                {"error": "You can only update your own agents"},
                status=status.HTTP_403_FORBIDDEN
            )

        partial = kwargs.pop('partial', False)
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        agent = serializer.save()

        # Sync to ElevenLabs if agent_id exists
        if agent.elevenlabs_agent_id:
            try:
                service = ElevenLabsService()
                config = service.build_agent_config(
                    name=agent.name,
                    system_prompt=agent.system_prompt,
                    voice_id=agent.voice_id,
                    temperature=agent.temperature,
                    first_message=agent.first_message or agent.exam_instructions,
                    max_duration_seconds=agent.max_duration_seconds,
                    conversation_config=agent.conversation_config or None,
                )
                async_to_sync(service.update_agent)(
                    agent.elevenlabs_agent_id,
                    config
                )
                logger.info(f"Updated ElevenLabs agent: {agent.elevenlabs_agent_id}")
            except Exception as e:
                logger.error(f"ElevenLabs sync failed: {e}")
                return Response(
                    {
                        "warning": f"Local update saved but ElevenLabs sync failed: {str(e)}",
                        "data": VoiceAgentSerializer(agent).data
                    },
                    status=status.HTTP_207_MULTI_STATUS
                )

        return Response(VoiceAgentSerializer(agent).data)

    def destroy(self, request, *args, **kwargs):
        """Delete agent from local DB and ElevenLabs."""
        instance = self.get_object()

        if instance.user != request.user:
            return Response(
                {"error": "You can only delete your own agents"},
                status=status.HTTP_403_FORBIDDEN
            )

        # Delete from ElevenLabs first
        if instance.elevenlabs_agent_id:
            try:
                service = ElevenLabsService()
                async_to_sync(service.delete_agent)(instance.elevenlabs_agent_id)
                logger.info(f"Deleted ElevenLabs agent: {instance.elevenlabs_agent_id}")
            except Exception as e:
                logger.error(f"Failed to delete from ElevenLabs: {e}")
                return Response(
                    {"error": f"Failed to delete from ElevenLabs: {str(e)}"},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR
                )

        # Soft delete locally
        instance.soft_delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=['post'])
    def get_signed_url(self, request, pk=None):
        """
        Get a signed URL for WebSocket connection to this agent.

        Any authenticated user can get a signed URL for active agents.
        """
        agent = self.get_object()

        # Check if agent is accessible
        if agent.status != VoiceAgentStatus.ACTIVE and agent.user != request.user:
            return Response(
                {"error": "This agent is not active"},
                status=status.HTTP_403_FORBIDDEN
            )

        if not agent.elevenlabs_agent_id:
            return Response(
                {"error": "Agent not configured with ElevenLabs"},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            service = ElevenLabsService()
            signed_url = async_to_sync(service.get_signed_url)(
                agent.elevenlabs_agent_id
            )

            return Response({
                "signed_url": signed_url,
                "agent_id": agent.elevenlabs_agent_id,
            })

        except Exception as e:
            logger.error(f"Failed to get signed URL: {e}")
            return Response(
                {"error": f"Failed to get signed URL: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    @action(detail=True, methods=['post'])
    def start_conversation(self, request, pk=None):
        """
        Record the start of a conversation session.

        Called after user successfully connects via the SDK.
        """
        agent = self.get_object()
        serializer = StartConversationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        elevenlabs_conversation_id = serializer.validated_data['conversation_id']

        # Check if conversation already exists
        if VoiceConversation.objects.filter(
            elevenlabs_conversation_id=elevenlabs_conversation_id
        ).exists():
            return Response(
                {"error": "Conversation already recorded"},
                status=status.HTTP_400_BAD_REQUEST
            )

        conversation = VoiceConversation.objects.create(
            agent=agent,
            user=request.user,
            elevenlabs_conversation_id=elevenlabs_conversation_id,
            status=ConversationStatus.IN_PROGRESS
        )

        logger.info(f"Started conversation: {elevenlabs_conversation_id}")

        return Response(
            VoiceConversationSerializer(conversation).data,
            status=status.HTTP_201_CREATED
        )

    @action(detail=True, methods=['post'])
    def end_conversation(self, request, pk=None):
        """
        Record the end of a conversation and fetch transcript.
        """
        agent = self.get_object()
        serializer = EndConversationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        elevenlabs_conversation_id = serializer.validated_data['conversation_id']

        try:
            conversation = VoiceConversation.objects.get(
                elevenlabs_conversation_id=elevenlabs_conversation_id,
                agent=agent,
                user=request.user
            )
        except VoiceConversation.DoesNotExist:
            return Response(
                {"error": "Conversation not found"},
                status=status.HTTP_404_NOT_FOUND
            )

        # Fetch transcript from ElevenLabs
        try:
            service = ElevenLabsService()
            conv_data = async_to_sync(service.get_conversation)(
                elevenlabs_conversation_id
            )

            conversation.transcript = conv_data.get('transcript', [])
            conversation.ended_at = timezone.now()
            conversation.duration_seconds = int(
                (conversation.ended_at - conversation.started_at).total_seconds()
            )
            conversation.status = ConversationStatus.COMPLETED
            conversation.save()

            logger.info(f"Completed conversation: {elevenlabs_conversation_id}")

        except Exception as e:
            logger.error(f"Failed to fetch transcript: {e}")
            conversation.status = ConversationStatus.FAILED
            conversation.ended_at = timezone.now()
            conversation.save()
            return Response(
                {"error": f"Failed to fetch transcript: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

        return Response(VoiceConversationSerializer(conversation).data)

    @action(detail=False, methods=['get'])
    def voices(self, request):
        """List available ElevenLabs voices."""
        try:
            service = ElevenLabsService()
            voices = async_to_sync(service.list_voices)()

            # Transform to simpler format
            simplified_voices = [
                {
                    "voice_id": v.get("voice_id"),
                    "name": v.get("name"),
                    "preview_url": v.get("preview_url"),
                    "category": v.get("category"),
                }
                for v in voices
            ]

            return Response(simplified_voices)
        except Exception as e:
            logger.error(f"Failed to fetch voices: {e}")
            return Response(
                {"error": f"Failed to fetch voices: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    @action(detail=False, methods=['get'])
    def elevenlabs_agents(self, request):
        """
        List agents directly from ElevenLabs API.

        This returns all agents in the ElevenLabs account,
        regardless of whether they're synced to local database.
        """
        try:
            service = ElevenLabsService()
            agents = async_to_sync(service.list_agents)()

            # Add local sync status to each agent
            elevenlabs_ids = [a.get('agent_id') for a in agents]
            synced_agents = VoiceAgent.objects.filter(
                elevenlabs_agent_id__in=elevenlabs_ids
            ).values_list('elevenlabs_agent_id', flat=True)
            synced_set = set(synced_agents)

            for agent in agents:
                agent['is_synced'] = agent.get('agent_id') in synced_set

            return Response(agents)
        except Exception as e:
            logger.error(f"Failed to fetch ElevenLabs agents: {e}")
            return Response(
                {"error": f"Failed to fetch ElevenLabs agents: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    @action(detail=False, methods=['post'])
    def import_agent(self, request):
        """
        Import an existing ElevenLabs agent into local database.

        This allows users to sync agents that were created directly
        in ElevenLabs dashboard or via other means.
        """
        # Support both camelCase (from frontend) and snake_case
        elevenlabs_agent_id = request.data.get('agentId') or request.data.get('agent_id')
        if not elevenlabs_agent_id:
            return Response(
                {"error": "agent_id is required"},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Check if already synced
        if VoiceAgent.objects.filter(elevenlabs_agent_id=elevenlabs_agent_id).exists():
            return Response(
                {"error": "Agent already imported"},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            service = ElevenLabsService()
            agent_data = async_to_sync(service.get_agent)(elevenlabs_agent_id)

            # Extract data from ElevenLabs response
            conv_config = agent_data.get('conversation_config', {})
            agent_config = conv_config.get('agent', {})
            prompt_config = agent_config.get('prompt', {})
            tts_config = conv_config.get('tts', {})
            conversation_settings = conv_config.get('conversation', {})

            # Create local agent with full config
            agent = VoiceAgent.objects.create(
                user=request.user,
                elevenlabs_agent_id=elevenlabs_agent_id,
                name=agent_data.get('name', 'Imported Agent'),
                system_prompt=prompt_config.get('prompt', ''),
                voice_id=tts_config.get('voice_id', ''),
                first_message=agent_config.get('first_message', ''),
                temperature=prompt_config.get('temperature', 0.7),
                max_duration_seconds=conversation_settings.get('max_duration_seconds', 1800),
                conversation_config=conv_config,  # Store full ElevenLabs config
                status=VoiceAgentStatus.ACTIVE,
            )

            logger.info(f"Imported ElevenLabs agent: {elevenlabs_agent_id}")

            return Response(
                VoiceAgentSerializer(agent).data,
                status=status.HTTP_201_CREATED
            )

        except Exception as e:
            logger.error(f"Failed to import agent: {e}")
            return Response(
                {"error": f"Failed to import agent: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class VoiceConversationViewSet(viewsets.ReadOnlyModelViewSet):
    """
    ViewSet for viewing voice conversations.

    Users can see:
    - Their own conversations
    - Conversations for agents they own (as the creator)
    """
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        # User's own conversations + conversations on their agents
        return VoiceConversation.active_objects.filter(
            Q(user=user) | Q(agent__user=user)
        ).select_related('agent', 'user')

    def get_serializer_class(self):
        if self.action == 'list':
            return VoiceConversationListSerializer
        return VoiceConversationSerializer
