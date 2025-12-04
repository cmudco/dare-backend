"""
Artifact Service

Orchestrates the generation of long-form artifacts (documents, code, diagrams)
with section-by-section streaming, checkpointing, and pause/resume capability.
"""

import json
import logging
from typing import AsyncGenerator, Tuple, Dict, Any, Optional, Callable

from channels.db import database_sync_to_async

from conversations.models import Artifact, ArtifactCheckpoint, Conversation, Message, LLM
from conversations.constants import (
    ArtifactStatus,
    ArtifactType,
    DEFAULT_ARTIFACT_SECTIONS_PER_ITERATION,
    DEFAULT_ARTIFACT_MAX_ITERATIONS,
)
from core.services.llm_service import LLMService
from core.services.api_key_service import get_provider_api_key, get_provider_api_key_for_user
from core.services.openai_service import OpenAIService
from core.services.claude_service import ClaudeService
from core.services.gemini_service import GeminiService
from core.services.llama_service import LlamaService
from core.services.custom_llm_service import CustomLLMService
from core.services.llm_utils.artifact_tools import ArtifactTools
from core.prompts.artifact_prompts import (
    get_planning_prompt,
    get_generation_prompt,
    get_section_user_prompt,
)
from conversations.constants import Provider

logger = logging.getLogger(__name__)


class ArtifactService:
    """Service for orchestrating artifact generation."""

    def __init__(
        self,
        conversation: Conversation,
        user=None,
        send_callback: Optional[Callable] = None,
    ):
        """
        Initialize the artifact service.

        Args:
            conversation: The conversation this artifact belongs to
            user: User object (None for public bots)
            send_callback: Async callback for sending WebSocket messages
        """
        self.conversation = conversation
        self.user = user
        self.send_callback = send_callback
        self.llm_service = LLMService()

    async def send(self, data: Dict[str, Any]):
        """Send data through callback if available."""
        if self.send_callback:
            await self.send_callback(data)

    async def execute(
        self,
        message: str,
        llm: LLM,
        message_obj: Message,
        artifact_id: Optional[str] = None,
    ) -> AsyncGenerator[Tuple[str, Optional[Dict]], None]:
        """
        Execute artifact generation flow.

        Args:
            message: User's message/request
            llm: LLM to use for generation
            message_obj: The AI message object to associate with artifact
            artifact_id: Optional existing artifact ID for continuation

        Yields:
            Tuple of (chunk: str, usage: Dict) for streaming responses
        """
        try:
            if artifact_id:
                # Resume existing artifact
                artifact = await self._get_artifact(artifact_id)
                if not artifact:
                    yield "Error: Artifact not found", None
                    return

                if artifact.status == ArtifactStatus.COMPLETED:
                    yield "Error: Artifact is already complete", None
                    return

                async for chunk, usage in self._continue_artifact(artifact, llm, message_obj):
                    yield chunk, usage
            else:
                # Create new artifact
                async for chunk, usage in self._create_artifact(message, llm, message_obj):
                    yield chunk, usage

        except Exception as e:
            logger.exception(f"Error in artifact execution: {str(e)}")
            yield f"Error: {str(e)}", None

    async def _create_artifact(
        self,
        message: str,
        llm: LLM,
        message_obj: Message,
    ) -> AsyncGenerator[Tuple[str, Optional[Dict]], None]:
        """
        Create a new artifact: plan then generate.

        Args:
            message: User's request
            llm: LLM to use
            message_obj: AI message object

        Yields:
            Tuple of (chunk: str, usage: Dict)
        """
        # Phase 1: Planning - get the LLM to create an outline using create_artifact tool
        artifact = await self._plan_artifact(message, llm, message_obj)

        if not artifact:
            yield "I'll help you with that request.", None
            return

        # Send artifact_init to frontend
        await self.send({
            "type": "artifact_init",
            "artifactId": str(artifact.id),
            "title": artifact.title,
            "outline": artifact.outline,
            "estimatedSections": artifact.estimated_sections,
        })

        # Phase 2: Generation - generate sections
        async for chunk, usage in self._generate_sections(artifact, llm, message_obj):
            yield chunk, usage

    async def _plan_artifact(
        self,
        message: str,
        llm: LLM,
        message_obj: Message,
    ) -> Optional[Artifact]:
        """
        Plan the artifact by asking LLM to create outline.

        Args:
            message: User's request
            llm: LLM to use
            message_obj: AI message object

        Returns:
            Created Artifact or None if planning failed
        """
        ai_service = await self._get_ai_service(llm)
        planning_prompt = get_planning_prompt(message)

        messages = [
            {"role": "system", "content": planning_prompt},
            {"role": "user", "content": message}
        ]

        tools = ArtifactTools.get_planning_tools()

        # Stream to collect tool calls
        full_response = ""
        tool_calls = []

        try:
            async for chunk, usage in ai_service.stream_chat_completion(
                messages=messages,
                max_tokens=2000,
                temperature=0.7,
                tools=tools
            ):
                if chunk:
                    full_response += chunk

                # Check for tool calls in usage
                if usage and usage.get("tool_calls"):
                    tool_calls.extend(usage["tool_calls"])

            # Process tool calls to create artifact
            for tool_call in tool_calls:
                if tool_call.get("name") == ArtifactTools.CREATE_ARTIFACT:
                    args = ArtifactTools.parse_tool_arguments(
                        tool_call.get("arguments", "{}")
                    )
                    return await self._create_artifact_from_tool_call(args, message_obj)

            # If no tool call, try to parse from response (fallback)
            artifact = await self._parse_artifact_from_response(full_response, message_obj)
            return artifact

        except Exception as e:
            logger.exception(f"Error in artifact planning: {str(e)}")
            return None

    async def _generate_sections(
        self,
        artifact: Artifact,
        llm: LLM,
        message_obj: Message,
    ) -> AsyncGenerator[Tuple[str, Optional[Dict]], None]:
        """
        Generate artifact sections iteratively.

        Args:
            artifact: The artifact to generate content for
            llm: LLM to use
            message_obj: AI message object

        Yields:
            Tuple of (chunk: str, usage: Dict)
        """
        iteration_count = 0
        sections_per_iteration = DEFAULT_ARTIFACT_SECTIONS_PER_ITERATION
        max_iterations = DEFAULT_ARTIFACT_MAX_ITERATIONS

        # Update status to generating
        await self._update_artifact_status(artifact, ArtifactStatus.GENERATING)

        while artifact.current_section < artifact.estimated_sections:
            iteration_count += 1

            # Check if we should pause
            if iteration_count > max_iterations:
                await self._pause_artifact(artifact, iteration_count)
                return

            # Generate next batch of sections
            sections_to_generate = min(
                sections_per_iteration,
                artifact.estimated_sections - artifact.current_section
            )

            for _ in range(sections_to_generate):
                section_num = artifact.current_section + 1

                async for chunk, usage in self._generate_single_section(
                    artifact, llm, section_num
                ):
                    if chunk:
                        yield chunk, usage

                # Update artifact progress
                artifact = await self._increment_section(artifact)

                # Send progress update
                await self.send({
                    "type": "artifact_stream",
                    "artifactId": str(artifact.id),
                    "chunk": "",  # Content is in the main stream
                    "section": artifact.current_section,
                    "progress": artifact.progress,
                })

            # Checkpoint after each iteration
            await self._create_checkpoint(artifact, iteration_count)

        # Finalize artifact
        await self._finalize_artifact(artifact, message_obj)

        # Send completion message
        await self.send({
            "type": "artifact_complete",
            "artifactId": str(artifact.id),
            "totalWords": artifact.word_count,
        })

    async def _generate_single_section(
        self,
        artifact: Artifact,
        llm: LLM,
        section_number: int,
    ) -> AsyncGenerator[Tuple[str, Optional[Dict]], None]:
        """
        Generate content for a single section.

        Args:
            artifact: The artifact
            llm: LLM to use
            section_number: Section number to generate

        Yields:
            Tuple of (chunk: str, usage: Dict)
        """
        ai_service = await self._get_ai_service(llm)

        # Refresh artifact to get latest content
        artifact = await self._refresh_artifact(artifact)

        system_prompt = get_generation_prompt(
            title=artifact.title,
            artifact_type=artifact.artifact_type,
            outline=artifact.outline,
            current_section=section_number,
            total_sections=artifact.estimated_sections,
            content_preview=artifact.content[-1000:] if artifact.content else ""
        )

        user_prompt = get_section_user_prompt(artifact.outline, section_number)

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]

        tools = ArtifactTools.get_generation_tools()

        section_content = ""

        try:
            async for chunk, usage in ai_service.stream_chat_completion(
                messages=messages,
                max_tokens=4000,
                temperature=0.7,
                tools=tools
            ):
                if chunk:
                    section_content += chunk
                    yield chunk, usage

                # Handle tool calls for update_artifact
                if usage and usage.get("tool_calls"):
                    for tool_call in usage["tool_calls"]:
                        if tool_call.get("name") == ArtifactTools.UPDATE_ARTIFACT:
                            args = ArtifactTools.parse_tool_arguments(
                                tool_call.get("arguments", "{}")
                            )
                            content = args.get("content", "")
                            if content:
                                await self._append_content(artifact, content)
                                section_content = ""  # Reset since content was handled

            # If we have accumulated content but no tool call, append it
            if section_content.strip():
                await self._append_content(artifact, section_content)

        except Exception as e:
            logger.exception(f"Error generating section {section_number}: {str(e)}")
            yield f"\n\n[Error generating section {section_number}]\n\n", None

    async def _continue_artifact(
        self,
        artifact: Artifact,
        llm: LLM,
        message_obj: Message,
    ) -> AsyncGenerator[Tuple[str, Optional[Dict]], None]:
        """
        Continue generating a paused artifact.

        Args:
            artifact: The artifact to continue
            llm: LLM to use
            message_obj: AI message object

        Yields:
            Tuple of (chunk: str, usage: Dict)
        """
        # Load latest checkpoint to get iteration count
        checkpoint = await self._get_latest_checkpoint(artifact)
        iteration_count = checkpoint.iteration_count if checkpoint else 0

        # Update status
        await self._update_artifact_status(artifact, ArtifactStatus.GENERATING)

        # Continue generation
        async for chunk, usage in self._generate_sections(artifact, llm, message_obj):
            yield chunk, usage

    async def _pause_artifact(
        self,
        artifact: Artifact,
        iteration_count: int,
    ):
        """
        Pause artifact generation.

        Args:
            artifact: The artifact to pause
            iteration_count: Current iteration count
        """
        await self._update_artifact_status(artifact, ArtifactStatus.PAUSED)
        await self._create_checkpoint(artifact, iteration_count)

        # Send pause message
        await self.send({
            "type": "artifact_pause",
            "artifactId": str(artifact.id),
            "currentSection": artifact.current_section,
            "sectionsRemaining": artifact.sections_remaining,
        })

    async def _finalize_artifact(
        self,
        artifact: Artifact,
        message_obj: Message,
    ):
        """
        Finalize the artifact.

        Args:
            artifact: The artifact to finalize
            message_obj: AI message object
        """
        await self._update_artifact_status(artifact, ArtifactStatus.COMPLETED)

        # Link artifact to message
        @database_sync_to_async
        def link_to_message():
            artifact.message = message_obj
            artifact.save(update_fields=['message'])

        await link_to_message()

    # ========== Database Operations ==========

    @database_sync_to_async
    def _create_artifact_from_tool_call(
        self,
        args: Dict[str, Any],
        message_obj: Message,
    ) -> Artifact:
        """Create artifact from tool call arguments."""
        artifact_type = args.get("artifact_type", ArtifactType.DOCUMENT)
        if artifact_type not in [t.value for t in ArtifactType]:
            artifact_type = ArtifactType.DOCUMENT

        return Artifact.objects.create(
            conversation=self.conversation,
            message=message_obj,
            artifact_type=artifact_type,
            title=args.get("title", "Untitled Artifact"),
            outline=args.get("outline", ""),
            language=args.get("language"),
            estimated_sections=args.get("estimated_sections", 10),
            status=ArtifactStatus.PLANNING,
        )

    @database_sync_to_async
    def _parse_artifact_from_response(
        self,
        response: str,
        message_obj: Message,
    ) -> Optional[Artifact]:
        """Parse artifact details from LLM response (fallback)."""
        # Try to extract title and outline from response
        lines = response.strip().split('\n')

        title = "Generated Document"
        outline = ""
        estimated_sections = 10

        for i, line in enumerate(lines):
            line_lower = line.lower().strip()
            if line_lower.startswith("title:"):
                title = line.split(":", 1)[1].strip()
            elif "outline" in line_lower or line.strip().startswith("1."):
                # Capture remaining lines as outline
                outline = "\n".join(lines[i:])
                # Count numbered items
                section_count = sum(
                    1 for l in lines[i:] if l.strip() and l.strip()[0].isdigit()
                )
                if section_count > 0:
                    estimated_sections = section_count
                break

        if not outline:
            # No outline found, create a basic one
            outline = "1. Introduction\n2. Main Content\n3. Conclusion"
            estimated_sections = 3

        return Artifact.objects.create(
            conversation=self.conversation,
            message=message_obj,
            artifact_type=ArtifactType.DOCUMENT,
            title=title,
            outline=outline,
            estimated_sections=estimated_sections,
            status=ArtifactStatus.PLANNING,
        )

    @database_sync_to_async
    def _get_artifact(self, artifact_id: str) -> Optional[Artifact]:
        """Get artifact by ID."""
        return Artifact.active_objects.filter(
            id=artifact_id,
            conversation=self.conversation
        ).first()

    @database_sync_to_async
    def _refresh_artifact(self, artifact: Artifact) -> Artifact:
        """Refresh artifact from database."""
        artifact.refresh_from_db()
        return artifact

    @database_sync_to_async
    def _update_artifact_status(self, artifact: Artifact, status: str):
        """Update artifact status."""
        artifact.status = status
        artifact.save(update_fields=['status'])

    @database_sync_to_async
    def _append_content(self, artifact: Artifact, content: str):
        """Append content to artifact."""
        artifact.refresh_from_db()
        artifact.content += content
        artifact.save(update_fields=['content'])

    @database_sync_to_async
    def _increment_section(self, artifact: Artifact) -> Artifact:
        """Increment current section counter."""
        artifact.refresh_from_db()
        artifact.current_section += 1
        artifact.save(update_fields=['current_section'])
        return artifact

    @database_sync_to_async
    def _create_checkpoint(self, artifact: Artifact, iteration_count: int):
        """Create a checkpoint for the artifact."""
        return ArtifactCheckpoint.objects.create(
            artifact=artifact,
            content_snapshot=artifact.content,
            current_section=artifact.current_section,
            iteration_count=iteration_count,
            state_data={
                "iteration_count": iteration_count,
                "status": artifact.status,
            }
        )

    @database_sync_to_async
    def _get_latest_checkpoint(
        self,
        artifact: Artifact
    ) -> Optional[ArtifactCheckpoint]:
        """Get the latest checkpoint for an artifact."""
        return artifact.checkpoints.order_by('-created_at').first()

    async def _get_ai_service(self, llm: LLM):
        """Get the appropriate AI service for the LLM."""
        if self.user:
            api_key = await get_provider_api_key_for_user(llm.provider, self.user)
        else:
            api_key = await get_provider_api_key(llm.provider)

        if llm.provider == Provider.OPENAI.value:
            return OpenAIService(llm=llm, api_key=api_key)
        elif llm.provider == Provider.CLAUDE.value:
            return ClaudeService(llm=llm, api_key=api_key)
        elif llm.provider == Provider.GEMINI.value:
            return GeminiService(llm=llm, api_key=api_key)
        elif llm.provider == Provider.LLAMA.value:
            return LlamaService(llm=llm, api_key=api_key)
        elif llm.provider == Provider.CUSTOM.value:
            return CustomLLMService(llm=llm, api_key=api_key)
        return ClaudeService(llm=llm, api_key=api_key)

    # ========== Public Methods for MessageCoordinator ==========

    @database_sync_to_async
    def get_active_artifact(self) -> Optional[Artifact]:
        """Get the active (paused or generating) artifact for the conversation."""
        return Artifact.active_objects.filter(
            conversation=self.conversation,
            status__in=[ArtifactStatus.PAUSED, ArtifactStatus.GENERATING]
        ).order_by('-created_at').first()

