import logging
import os
import tempfile
from decimal import Decimal

import markdown
import weasyprint
from django.conf import settings
from django.db import transaction
from django.db.models import Count, OuterRef, Subquery
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404
from django.template.loader import render_to_string
from django.utils import timezone
from rest_framework import generics, mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from conversations.services.llm_filter_service import (
    filter_for_active_wallet,
    filter_for_bot,
    parse_scope,
)
from conversations.api.mixins import ConversationSharingMixin
from conversations.constants import (
    ArtifactStatus,
    SharingErrorCode,
    SharingErrorMessage,
)
from conversations.exceptions import SharingAPIException
from conversations.models import (
    Artifact,
    Conversation,
    ConversationSummary,
    Feedback,
    LLM,
    Message,
    ModelCardData,
    Snippet,
)
from core.services.sb_client import SocraticBooksClient
from conversations.services.conversation_preference_service import (
    ConversationPreferenceError,
    ConversationPreferenceService,
)
from conversations.services.sharing_service import (
    ConversationSharingService,
    SharingValidationError,
)
from conversations.services.socratic_dependency_service import SocraticDependencyService
from sharing.services.sharing_service import SharingService
from users.utils import detect_platform_from_request
from .serializers import (
    MessageSerializer,
    ConversationSerializer,
    LLMSerializer,
    ArtifactSerializer,
    ArtifactListSerializer,
    ArtifactCheckpointSerializer,
    FeedbackSerializer,
    ModelCardDataSerializer,
    ModelCardDataListSerializer,
    ConversationSummarySerializer,
)

logger = logging.getLogger(__name__)


class ConversationViewSet(ConversationSharingMixin, viewsets.ModelViewSet):
    """Endpoint for listing, retrieving, creating and updating chat conversations."""

    serializer_class = ConversationSerializer
    permission_classes = [AllowAny]  # Allow both authenticated and anonymous access
    lookup_field = "conversation_id"

    @staticmethod
    def _annotate_fallback_llm(queryset):
        """Annotate `_fallback_llm_id` from the latest message's llm.

        The conversation's own `selected_model` was historically not
        updated when users sent messages, so older conversations read
        back as null and the UI re-prompts for a model. Every Message
        still records the llm it used, so we surface the most recent
        non-null one as a per-row fallback the serializer can use.
        """
        latest_llm = (
            Message.active_objects.filter(
                conversation=OuterRef("pk"),
                llm__isnull=False,
            )
            .order_by("-created_at")
            .values("llm_id")[:1]
        )
        return queryset.annotate(_fallback_llm_id=Subquery(latest_llm))

    def get_queryset(self):
        platform_source = detect_platform_from_request(self.request)

        # Shared conversations: published by other users
        shared = self.request.query_params.get("shared", None)
        if shared == "true":
            if (
                hasattr(self.request, "user")
                and self.request.user
                and self.request.user.is_authenticated
            ):
                queryset = Conversation.active_objects.filter(
                    is_published=True, source=platform_source
                ).exclude(user=self.request.user)
            else:
                queryset = Conversation.active_objects.filter(
                    is_published=True, source=platform_source
                )
            return self._annotate_fallback_llm(
                queryset.select_related("selected_model", "prompt", "user")
            ).order_by("-published_at")

        anonymous_session_id = self.request.query_params.get(
            "anonymous_session_id", None
        )

        if anonymous_session_id:
            queryset = Conversation.active_objects.filter(
                anonymous_session_id=anonymous_session_id, source=platform_source
            )
        else:
            if (
                hasattr(self.request, "user")
                and self.request.user
                and hasattr(self.request.user, "is_authenticated")
                and self.request.user.is_authenticated
            ):
                queryset = Conversation.active_objects.filter(
                    user=self.request.user, source=platform_source
                )
            else:
                queryset = Conversation.active_objects.none()

        bot_id = self.request.query_params.get("bot_id", None)
        if bot_id is not None:
            queryset = queryset.filter(bot_id=bot_id)

        return self._annotate_fallback_llm(
            queryset.select_related("selected_model", "prompt")
        ).order_by("sort_order", "-created_at")

    def perform_create(self, serializer):
        platform_source = detect_platform_from_request(self.request)
        # For public bots, user can be null
        user = None
        if (
            hasattr(self.request, "user")
            and self.request.user
            and self.request.user.is_authenticated
        ):
            user = self.request.user
        serializer.save(user=user, source=platform_source)
        if user and hasattr(user, "default_prompt") and user.default_prompt:
            serializer.instance.prompt = user.default_prompt
            serializer.instance.save()

    @action(detail=False, methods=["patch"], url_path="update-sort-order")
    def update_sort_order(self, request):
        """
        Update the sort order of multiple conversations.
        Expected payload: [{"conversation_id": "ABC123", "sort_order": 1}, ...]
        """
        try:
            updates = request.data
            if not isinstance(updates, list):
                return Response(
                    {"error": "Expected a list of conversation updates"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            conversation_ids = [update.get("conversation_id") for update in updates]
            conversations = Conversation.active_objects.filter(
                user=request.user, conversation_id__in=conversation_ids
            )

            conversation_map = {conv.conversation_id: conv for conv in conversations}

            for update in updates:
                conversation_id = update.get("conversation_id")
                sort_order = update.get("sort_order")

                if conversation_id in conversation_map and sort_order is not None:
                    conversation_map[conversation_id].sort_order = sort_order
                    conversation_map[conversation_id].save(update_fields=["sort_order"])

            return Response(status=status.HTTP_204_NO_CONTENT)

        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=False, methods=["post"], url_path="bulk-delete")
    def bulk_delete(self, request):
        """
        Bulk delete multiple conversations using DRF's built-in delete method for each conversation.
        Expected payload: {"conversation_ids": ["ABC123", "DEF456", ...]}
        """
        conversation_ids = request.data.get("conversation_ids", [])

        if not conversation_ids:
            return Response(
                {"error": "No conversation IDs provided."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not isinstance(conversation_ids, list):
            return Response(
                {"error": "conversation_ids must be a list."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        conversations = Conversation.active_objects.filter(
            conversation_id__in=conversation_ids, user=request.user
        )

        if not conversations.exists():
            return Response(
                {"error": "No valid conversations found to delete."},
                status=status.HTTP_404_NOT_FOUND,
            )

        deleted_conversations = []
        failed_conversations = []

        for conversation in conversations:
            try:
                conversation_data = {
                    "conversation_id": conversation.conversation_id,
                    "title": conversation.title,
                }
                self.perform_destroy(conversation)
                deleted_conversations.append(conversation_data)
            except Exception as e:
                logger.error(
                    f"Error deleting conversation ID {conversation.conversation_id}: {str(e)}"
                )
                failed_conversations.append(
                    {"conversation_id": conversation.conversation_id, "error": str(e)}
                )

        response_data = {
            "status": "Bulk delete completed",
            "deleted_count": len(deleted_conversations),
            "failed_count": len(failed_conversations),
            "requested_count": len(conversation_ids),
        }

        if failed_conversations:
            response_data["failed_conversations"] = failed_conversations

        return Response(response_data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="clone")
    def clone_conversation(self, request, conversation_id=None):
        """
        Clone a conversation with all its messages, files, tags, and snippets.
        Simply clones everything - no options needed.
        """
        try:
            with transaction.atomic():
                instance = self.get_object()

                # Use the model's built-in clone method
                cloned_conversation = instance.clone()

                # Prepare response data
                serializer = self.get_serializer(cloned_conversation)
                return Response(serializer.data, status=status.HTTP_201_CREATED)

        except Exception as e:

            logger.error(f"Error cloning conversation {conversation_id}: {str(e)}")
            return Response(
                {"error": f"Failed to clone conversation: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    def retrieve(self, request, *args, **kwargs):
        """
        Override retrieve to allow fetching published conversations
        even if the user is not the owner.
        """
        try:
            instance = self.get_object()
        except (Http404, NotFound):
            # Fallback: allow published or directly shared conversations
            conversation_id = kwargs.get("conversation_id") or self.kwargs.get(
                "conversation_id"
            )
            instance = self._annotate_fallback_llm(
                Conversation.active_objects.filter(
                    conversation_id=conversation_id
                ).select_related("selected_model", "prompt", "user")
            ).first()
            if not instance:
                raise NotFound("Conversation not found")
            if not instance.is_published and not SharingService.can_access(
                request.user,
                "conversation",
                conversation_id,
            ):
                raise NotFound("Conversation not found")
        self._heal_selected_model_if_owner(instance, request.user)
        serializer = self.get_serializer(instance)
        return Response(serializer.data)

    @staticmethod
    def _heal_selected_model_if_owner(conversation: Conversation, user) -> None:
        """Align `selected_model` with the latest message's llm for the owner.

        Serialization already uses the annotated `_fallback_llm_id`, so the UI
        works without this. But internal code paths (e.g.
        `get_conversation_default_descriptor`) still read `selected_model` directly —
        they'd pick `LLM.objects.first()` for null rows or the stale value
        when the user switched models mid-conversation. One-time write per
        conversation whose stored value drifted; non-owners never mutate the row.
        """
        fallback_llm_id = getattr(conversation, "_fallback_llm_id", None)
        if not fallback_llm_id:
            return
        if conversation.selected_model_id == fallback_llm_id:
            return
        if not (user and user.is_authenticated and conversation.user_id == user.id):
            return
        conversation.selected_model_id = fallback_llm_id
        conversation.save(update_fields=["selected_model"])

    @action(detail=True, methods=["post"], url_path="publish")
    def publish_conversation(self, request, conversation_id=None):
        """
        Toggle the published status of a conversation.
        Only the owner can publish/unpublish.
        """
        return self.handle_sharing_operation(
            lambda: ConversationSharingService.toggle_publish(
                self.get_object(), request.user
            )
        )

    @action(detail=True, methods=["post"], url_path="fork")
    def fork_conversation(self, request, conversation_id=None):
        """
        Fork a published conversation for the current user.
        Creates a clone owned by the requesting user.
        """
        return self.handle_sharing_operation(
            lambda: ConversationSharingService.fork(conversation_id, request.user),
            success_status=status.HTTP_201_CREATED,
        )

    @action(
        detail=True,
        methods=["post"],
        url_path="favorite",
        permission_classes=[IsAuthenticated],
    )
    def toggle_favorite(self, request, conversation_id=None):
        """Toggle the favorite flag for an owned conversation."""
        conversation = self.get_object()

        try:
            updated_conversation = ConversationPreferenceService.toggle_favorite(
                conversation,
                request.user,
            )
        except ConversationPreferenceError as exc:
            return Response(
                {"error": str(exc)},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = self.get_serializer(updated_conversation)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["get"], url_path="messages")
    def list_messages(self, request, conversation_id=None):
        """
        List messages for a conversation.
        Allows access if user is the owner or the conversation is published.
        Used for read-only message loading of shared conversations.
        """
        conversation = Conversation.active_objects.filter(
            conversation_id=conversation_id
        ).first()

        if not conversation:
            raise SharingAPIException(
                SharingValidationError(
                    SharingErrorMessage.CONVERSATION_NOT_FOUND,
                    SharingErrorCode.NOT_FOUND,
                )
            )

        if not ConversationSharingService.can_view_messages(conversation, request.user):
            raise SharingAPIException(
                SharingValidationError(
                    SharingErrorMessage.PERMISSION_DENIED,
                    SharingErrorCode.PERMISSION_DENIED,
                )
            )

        messages = (
            Message.active_objects.filter(conversation=conversation)
            .select_related("llm")
            .prefetch_related("files", "tags", "snippets__file", "web_search_sources")
            .order_by("created_at")
        )

        serializer = MessageSerializer(
            messages, many=True, context={"request": request}
        )
        return Response(serializer.data)

    @action(detail=True, methods=["get"], url_path="artifacts")
    def list_artifacts(self, request, conversation_id=None):
        """
        List all artifacts for a conversation.

        Query params:
        - status: Filter by artifact status (planning, generating, paused, completed, error)
        - artifact_type: Filter by type (document, code, diagram)
        """
        conversation = self.get_object()

        queryset = (
            Artifact.active_objects.filter(conversation=conversation)
            .select_related("conversation", "message")
            .order_by("-created_at")
        )

        # Optional status filter
        status_filter = request.query_params.get("status")
        if status_filter:
            queryset = queryset.filter(status=status_filter)

        # Optional type filter
        type_filter = request.query_params.get("artifact_type")
        if type_filter:
            queryset = queryset.filter(artifact_type=type_filter)

        serializer = ArtifactListSerializer(queryset, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=["get"], url_path="artifacts/(?P<artifact_id>[^/.]+)")
    def artifact_detail(self, request, conversation_id=None, artifact_id=None):
        """Get detailed information about a specific artifact."""
        conversation = self.get_object()

        try:
            artifact = Artifact.active_objects.get(
                id=artifact_id, conversation=conversation
            )
        except Artifact.DoesNotExist:
            return Response(
                {"error": "Artifact not found"}, status=status.HTTP_404_NOT_FOUND
            )

        serializer = ArtifactSerializer(artifact)
        return Response(serializer.data)

    @action(
        detail=True,
        methods=["get"],
        url_path="artifacts/(?P<artifact_id>[^/.]+)/checkpoints",
    )
    def artifact_checkpoints(self, request, conversation_id=None, artifact_id=None):
        """Get all checkpoints for an artifact."""
        conversation = self.get_object()

        try:
            artifact = Artifact.active_objects.get(
                id=artifact_id, conversation=conversation
            )
        except Artifact.DoesNotExist:
            return Response(
                {"error": "Artifact not found"}, status=status.HTTP_404_NOT_FOUND
            )

        checkpoints = artifact.checkpoints.order_by("-created_at")
        serializer = ArtifactCheckpointSerializer(checkpoints, many=True)
        return Response(serializer.data)

    @action(
        detail=True,
        methods=["get"],
        url_path="artifacts/(?P<artifact_id>[^/.]+)/versions",
    )
    def artifact_versions(self, request, conversation_id=None, artifact_id=None):
        """
        Get version history for an artifact.

        Returns all versions in the artifact's group, ordered by version number.
        """
        conversation = self.get_object()

        try:
            artifact = Artifact.active_objects.get(
                id=artifact_id, conversation=conversation
            )
        except Artifact.DoesNotExist:
            return Response(
                {"error": "Artifact not found"}, status=status.HTTP_404_NOT_FOUND
            )

        # Get all versions in this artifact's group
        if not artifact.artifact_group:
            # Single artifact with no group - just return itself
            versions = [artifact]
        else:
            versions = artifact.artifact_group.versions.filter(is_active=True).order_by(
                "version"
            )

        serializer = ArtifactListSerializer(versions, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=["get"], url_path="export-pdf")
    def export_pdf(self, request, conversation_id=None):
        """Export conversation history as a PDF."""
        try:
            conversation = self.get_object()

            # Get all messages in the conversation, ordered by creation date
            messages = (
                conversation.messages.filter(is_active=True, is_deleted=False)
                .select_related("llm")
                .prefetch_related(
                    "files__tags", "tags", "snippets__file", "web_search_sources"
                )
                .order_by("created_at")
            )

            # Convert markdown to HTML for each message
            processed_messages = []
            total_input_tokens = 0
            total_output_tokens = 0
            total_cost = Decimal("0.000000")
            models_counter = {}
            unique_files = {}
            unique_tags = set()

            for message in messages:
                processed_message = message
                # Convert markdown to HTML if the message contains markdown
                if message.message:
                    processed_message.message = markdown.markdown(
                        message.message,
                        extensions=[
                            "markdown.extensions.fenced_code",
                            "markdown.extensions.tables",
                            "markdown.extensions.nl2br",
                        ],
                    )

                # Aggregate usage metrics
                if message.input_tokens:
                    total_input_tokens += int(message.input_tokens)
                if message.output_tokens:
                    total_output_tokens += int(message.output_tokens)
                if message.cost:
                    try:
                        total_cost += Decimal(message.cost)
                    except Exception:
                        pass

                # Count models for AI messages
                if message.llm is not None and message.sender_type == 2:
                    key = message.llm_id
                    if key not in models_counter:
                        models_counter[key] = {
                            "name": message.llm.name,
                            "provider": message.llm.provider,
                            "identifier": message.llm.identifier,
                            "count": 0,
                        }
                    models_counter[key]["count"] += 1

                # Collect unique files
                for f in message.files.all():
                    unique_files[f.id] = f
                    # collect file tags as part of tag summary
                    for tg in (
                        getattr(f, "tags", []).all() if hasattr(f, "tags") else []
                    ):
                        unique_tags.add(tg.label)

                # Collect message tags
                for tag in message.tags.all():
                    unique_tags.add(tag.label)

                processed_messages.append(processed_message)

            # Prepare context for template
            context = {
                "conversation": conversation,
                "messages": processed_messages,
                "generated_at": timezone.now(),
                "user": conversation.user,
                # Aggregates / summary
                "total_input_tokens": total_input_tokens,
                "total_output_tokens": total_output_tokens,
                "total_cost": total_cost,
                "models_summary": list(models_counter.values()),
                "files_summary": list(unique_files.values()),
                "files_count": len(unique_files),
                "tags_summary": sorted(list(unique_tags)),
            }

            # Render HTML template
            html_content = render_to_string(
                "conversations/conversation_export.html", context
            )

            # Generate PDF
            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_file:
                weasyprint.HTML(
                    string=html_content, base_url=request.build_absolute_uri("/")
                ).write_pdf(tmp_file.name)

                # Read PDF content
                with open(tmp_file.name, "rb") as pdf_file:
                    pdf_content = pdf_file.read()

                # Clean up temporary file
                os.unlink(tmp_file.name)

            # Generate filename
            safe_title = "".join(
                c
                for c in (conversation.title or "Conversation")
                if c.isalnum() or c in (" ", "-", "_")
            ).rstrip()
            filename = f"{safe_title}_{conversation.conversation_id}.pdf"

            # Create HTTP response
            response = HttpResponse(pdf_content, content_type="application/pdf")
            response["Content-Disposition"] = f'attachment; filename="{filename}"'
            response["Content-Length"] = len(pdf_content)

            return response

        except Exception as e:

            logger.error(
                f"Error exporting conversation {conversation_id} to PDF: {str(e)}"
            )
            return Response(
                {"error": f"Failed to export conversation: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class ConversationSummaryViewSet(viewsets.ReadOnlyModelViewSet):
    """Read-only endpoint for the current user's per-conversation summaries."""

    serializer_class = ConversationSummarySerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return (
            ConversationSummary.active_objects.filter(
                conversation__user=self.request.user
            )
            .select_related("conversation", "llm")
            .order_by("-updated_at", "-created_at")
        )


class MessageViewSet(viewsets.ModelViewSet):
    """Endpoint for creating/retrieving messages within a conversation."""

    serializer_class = MessageSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return (
            Message.active_objects.filter(conversation__user=self.request.user)
            .select_related("llm", "conversation")
            .prefetch_related("files", "tags", "snippets__file", "web_search_sources")
        )

    def get_serializer_context(self):
        context = super().get_serializer_context()
        return context

    @action(detail=True, methods=["post"], url_path="soft-delete")
    def soft_delete_message(self, request, pk=None):
        """
        Soft delete a message by setting is_deleted to True.
        This is a non-destructive operation that hides the message from conversation history.
        """
        try:
            message = self.get_object()

            message.soft_delete()

            return Response(
                {
                    "status": "Message soft deleted successfully",
                    "message_id": str(message.id),
                },
                status=status.HTTP_200_OK,
            )

        except Message.DoesNotExist:
            return Response(
                {"error": "Message not found"}, status=status.HTTP_404_NOT_FOUND
            )
        except Exception as e:

            logger.error(f"Error soft deleting message {pk}: {str(e)}")
            return Response(
                {"error": f"Failed to delete message: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class ArtifactStatusView(APIView):
    """
    Update artifact status via REST API.

    Used for pause/resume when WebSocket is blocked by streaming.
    The generation loop polls the database after each section and will
    pick up the updated status.
    """

    permission_classes = [IsAuthenticated]

    def patch(self, request, artifact_id):
        """
        Update artifact status.

        Request body: {"status": "paused"|"generating"|"completed"|"error"}
        """
        new_status = request.data.get("status")

        # Validate status
        valid_statuses = [s.value for s in ArtifactStatus]
        if new_status not in valid_statuses:
            return Response(
                {"error": f"Invalid status. Must be one of: {valid_statuses}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Get artifact
        artifact = get_object_or_404(Artifact.active_objects, id=artifact_id)

        # Verify user owns this artifact's conversation
        if artifact.conversation.user != request.user:
            return Response(
                {"error": "You do not have permission to modify this artifact"},
                status=status.HTTP_403_FORBIDDEN,
            )

        # Update status
        artifact.status = new_status
        artifact.save(update_fields=["status", "updated_at"])

        logger.info(
            f"Artifact {artifact_id} status updated to {new_status} via REST API"
        )

        return Response(
            {
                "id": artifact.id,
                "status": artifact.status,
                "currentSection": artifact.current_section,
                "estimatedSections": artifact.estimated_sections,
            }
        )


class ArtifactContentView(APIView):
    """
    Update artifact content via REST API.

    Used for direct manual editing of artifact content.
    Creates a new version to preserve history.
    """

    permission_classes = [IsAuthenticated]

    def patch(self, request, artifact_id):
        """
        Update artifact content by creating a new version.

        Request body: {"content": "new markdown content"}

        Returns the newly created artifact version.
        """

        new_content = request.data.get("content")
        if new_content is None:
            return Response(
                {"error": "Content field is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Get artifact
        artifact = get_object_or_404(Artifact.active_objects, id=artifact_id)

        # Verify user owns this artifact's conversation
        if artifact.conversation.user != request.user:
            return Response(
                {"error": "You do not have permission to modify this artifact"},
                status=status.HTTP_403_FORBIDDEN,
            )

        # Create new version with updated content
        with transaction.atomic():
            new_artifact = artifact.create_new_version()
            new_artifact.content = new_content
            new_artifact.status = ArtifactStatus.COMPLETED
            new_artifact.save(update_fields=["content", "status", "updated_at"])

        logger.info(
            f"Artifact {artifact_id} content updated via manual edit, "
            f"created new version {new_artifact.id} (v{new_artifact.version})"
        )

        # Return the new artifact using the serializer
        serializer = ArtifactSerializer(new_artifact)
        return Response(serializer.data, status=status.HTTP_200_OK)


class LLMViewSet(viewsets.ModelViewSet):
    """Endpoint for listing available LLM models."""

    serializer_class = LLMSerializer
    permission_classes = [IsAuthenticated]
    queryset = LLM.objects.all().order_by("tier", "name")

    def get_queryset(self):
        """
        Filter LLM models based on the user's Access Code Group -> Model Group mapping.
        Rules:
        - If the user has no access code group, return ALL models.
        - If the access code group has no model group (or is inactive), return ALL models.
        - Otherwise, return the allowed models from the group's model list.
        """
        user = self.request.user

        # No access code group: all models
        if not getattr(user, "access_code_group", None):
            return LLM.objects.all().order_by("tier", "name")

        acg = user.access_code_group
        # ACG without model group or inactive group: all models
        if not getattr(acg, "model_group", None):
            return LLM.objects.all().order_by("tier", "name")
        if not acg.model_group.is_active:
            return LLM.objects.all().order_by("tier", "name")

        # Restrict to allowed models from the access code group's model group
        return acg.model_group.allowed_models.all().order_by("tier", "name")

    def list(self, request, *args, **kwargs):
        """
        Standard list returns the access-code-group filtered catalog.

        When `?wallet_scope=active` or `?wallet_scope=bot:<id>` is supplied,
        the response is wrapped as `{models: [...], wallet: {...}}` filtered
        by the wallet that will pay (per the wallet router). Legacy callers
        omitting the param get the historical flat list shape unchanged.
        """
        scope = parse_scope(request.query_params.get("wallet_scope"))
        if scope is None:
            return super().list(request, *args, **kwargs)

        base_qs = self.get_queryset()
        if scope.kind == "active":
            models, meta = filter_for_active_wallet(request.user, base_qs)
        else:  # scope.kind == "bot"
            config = SocraticBooksClient.get_bot_billing_config(scope.bot_id)
            if config is None or config.owner_dare_user_id != getattr(
                request.user, "id", None
            ):
                return Response(
                    {"detail": "Not authorized for this bot."},
                    status=status.HTTP_403_FORBIDDEN,
                )
            models, meta = filter_for_bot(scope.bot_id, request.user, base_qs)

        # Uniform flat shape — every entry has a string ``id`` (DB-PK or
        # ``litellm:<key>:<model>``) opaque to the FE; the BE inverts it on
        # dispatch via ``parse_model_id``. The FE just renders & echoes back.
        return Response({"models": models, "wallet": meta.to_dict()})

    @action(detail=False, methods=["get"])
    def all_models(self, request):
        """
        Return all LLM models without filtering by user's groups.
        This is used for displaying model names in historical conversations.
        """
        queryset = LLM.objects.all().order_by("tier", "name")
        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)

    def destroy(self, request, *args, **kwargs):
        """
        Override destroy to check for Socratic Books bot dependencies before deletion.

        If dependent bots exist and ?confirm=true is not provided, returns a 409 Conflict
        response with the list of dependent bots instead of deleting.

        If ?confirm=true is provided, proceeds with deletion regardless of dependencies.
        """
        instance = self.get_object()
        confirm = request.query_params.get("confirm", "").lower() == "true"

        if not confirm:
            dependency_data = SocraticDependencyService.get_dependent_bots(instance.id)

            if dependency_data and dependency_data.get("dependent_bots_count", 0) > 0:
                return Response(
                    {
                        "warning": True,
                        "message": (
                            f"This model is used by {dependency_data['dependent_bots_count']} "
                            f"Socratic Books bot(s). Deleting it will break these bots."
                        ),
                        "model_id": instance.id,
                        "model_name": instance.name,
                        "dependent_bots": dependency_data["dependent_bots"],
                        "confirm_url": f"/api/conversations/llms/{instance.id}/?confirm=true",
                    },
                    status=status.HTTP_409_CONFLICT,
                )

        return super().destroy(request, *args, **kwargs)


class FeedbackViewSet(mixins.CreateModelMixin, viewsets.GenericViewSet):
    """
    ViewSet for submitting general user feedback from the FAB widget.

    POST /api/conversations/feedback/
    """

    serializer_class = FeedbackSerializer
    permission_classes = [IsAuthenticated]
    queryset = Feedback.active_objects.all()

    def perform_create(self, serializer):
        """Save the feedback with the current user."""
        serializer.save(user=self.request.user)

        logger.info(
            f"Feedback submitted by {self.request.user.email}: "
            f"{serializer.data.get('emotion')} - {serializer.data.get('category')}"
        )


class ModelCardDataViewSet(viewsets.ReadOnlyModelViewSet):
    """Read-only endpoint for Model Card data."""

    queryset = ModelCardData.objects.all()
    permission_classes = [AllowAny]
    lookup_field = "slug"

    def get_serializer_class(self):
        if self.action == "list":
            return ModelCardDataListSerializer
        return ModelCardDataSerializer

    def get_object(self):
        slug = self.kwargs.get("slug")

        # Try exact slug match first
        try:
            return ModelCardData.objects.get(slug=slug)
        except ModelCardData.DoesNotExist:
            pass

        # Fallback: search name_variants
        for card in ModelCardData.objects.all():
            if slug in [v.lower().replace(" ", "-") for v in card.name_variants]:
                return card

        raise NotFound("Model card not found")


class AnonymousConversationsView(APIView):
    """
    Endpoint to fetch anonymous conversations for a public bot.

    Used by SocraticBots backend to display anonymous public bot conversations.
    Requires JWT authentication (professor's token).

    Query params:
        bot_id (required): Socratic Bot ID to filter by

    Returns:
        List of anonymous conversations with conversationId, createdAt, messageCount, sessionId
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        # Get required bot_id param
        bot_id = request.query_params.get("bot_id")
        if not bot_id:
            return Response(
                {"error": "bot_id query parameter is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            bot_id = int(bot_id)
        except ValueError:
            return Response(
                {"error": "bot_id must be an integer"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Build queryset for anonymous SocraticBots conversations
        queryset = (
            Conversation.active_objects.filter(
                bot_id=bot_id,
                source="SocraticBots",
                user__isnull=True,  # Only anonymous conversations
                anonymous_session_id__isnull=False,
            )
            .annotate(message_count=Count("messages"))
            .order_by("-created_at")
        )

        # Build response data
        conversations = []
        for conv in queryset:
            conversations.append(
                {
                    "conversation_id": conv.conversation_id,
                    "title": conv.title
                    or f"Anonymous Session {conv.anonymous_session_id[:8]}",
                    "created_at": conv.created_at.isoformat(),
                    "message_count": conv.message_count,
                    "session_id": conv.anonymous_session_id,
                    "bot_id": conv.bot_id,
                }
            )

        return Response(
            {
                "conversations": conversations,
                "total_count": len(conversations),
            }
        )
