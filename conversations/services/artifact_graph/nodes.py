"""
LangGraph Node Functions for Artifact Generation

Each function represents a node in the artifact generation graph.
These are checkpointed automatically by LangGraph at each transition.
"""

import logging
from typing import Dict, Any, Optional
from asgiref.sync import sync_to_async
from django.db import connection
from django.contrib.auth import get_user_model

from conversations.models import Artifact, ArtifactCheckpoint, Conversation, Message, LLM
from conversations.constants import ArtifactStatus, ArtifactType, Provider
from core.services.llm_utils.artifact_tools import ArtifactTools
from core.prompts.artifact_prompts import (
    get_planning_prompt,
    get_generation_prompt,
    get_section_user_prompt,
)
from core.services.api_key_service import get_provider_api_key, get_provider_api_key_for_user
from core.services.openai_service import OpenAIService
from core.services.claude_service import ClaudeService
from core.services.gemini_service import GeminiService
from core.services.llama_service import LlamaService
from core.services.custom_llm_service import CustomLLMService

from .state import ArtifactState

logger = logging.getLogger(__name__)


# ========== Database Helpers ==========

@sync_to_async
def get_llm(llm_id: int) -> LLM:
    """Get LLM from database."""
    return LLM.objects.get(id=llm_id)


@sync_to_async
def get_conversation(conversation_id: str) -> Conversation:
    """Get conversation from database."""
    return Conversation.active_objects.get(conversation_id=conversation_id)


@sync_to_async
def get_artifact(artifact_id: int) -> Artifact:
    """Get artifact from database."""
    return Artifact.active_objects.get(id=artifact_id)


@sync_to_async
def create_artifact_db(
    conversation: Conversation,
    message: Optional[Message],
    artifact_type: str,
    title: str,
    outline: str,
    estimated_sections: int,
    language: Optional[str] = None,
) -> Artifact:
    """Create artifact in database."""
    artifact = Artifact(
        conversation=conversation,
        message=message,
        artifact_type=artifact_type,
        title=title,
        outline=outline,
        estimated_sections=estimated_sections,
        current_section=0,
        status=ArtifactStatus.PLANNING,
        language=language,
    )
    artifact.save()
    return artifact


@sync_to_async
def update_artifact_db(
    artifact_id: int,
    **kwargs
) -> Artifact:
    """Update artifact in database."""
    artifact = Artifact.active_objects.get(id=artifact_id)
    for key, value in kwargs.items():
        setattr(artifact, key, value)
    artifact.save()
    return artifact


@sync_to_async
def create_checkpoint_db(
    artifact: Artifact,
    content_snapshot: str,
    current_section: int,
    iteration_count: int,
    state_data: Dict[str, Any],
) -> ArtifactCheckpoint:
    """Create checkpoint in database."""
    checkpoint = ArtifactCheckpoint(
        artifact=artifact,
        content_snapshot=content_snapshot,
        current_section=current_section,
        iteration_count=iteration_count,
        state_data=state_data,
    )
    checkpoint.save()
    return checkpoint


@sync_to_async
def check_artifact_paused(artifact_id: int) -> bool:
    """Check if artifact has been paused by user."""
    try:
        # Use select_for_update to ensure we read the latest committed data
        # and avoid reading stale cached data
        connection.ensure_connection()

        artifact = Artifact.active_objects.get(id=artifact_id)
        # Force refresh from database to get latest status
        artifact.refresh_from_db(fields=['status'])
        is_paused = artifact.status == ArtifactStatus.PAUSED
        logger.info(f"Check artifact paused: artifact_id={artifact_id}, status={artifact.status}, is_paused={is_paused}")
        return is_paused
    except Artifact.DoesNotExist:
        logger.warning(f"Check artifact paused: artifact_id={artifact_id} not found")
        return False


# ========== AI Service Helper ==========

async def get_ai_service(llm: LLM, user=None):
    """
    Get the appropriate AI service for an LLM.

    All services expect an LLM object and optional api_key override.
    """
    provider = llm.provider

    # Get API key (these are already async functions)
    if user:
        api_key = await get_provider_api_key_for_user(provider, user)
    else:
        api_key = await get_provider_api_key(provider)

    if not api_key:
        raise ValueError(f"No API key found for provider {provider}")

    # Return appropriate service - all take (llm, api_key) signature
    if provider == Provider.OPENAI.value:
        return OpenAIService(llm=llm, api_key=api_key)
    elif provider == Provider.CLAUDE.value:
        return ClaudeService(llm=llm, api_key=api_key)
    elif provider == Provider.GEMINI.value:
        return GeminiService(llm=llm, api_key=api_key)
    elif provider == Provider.LLAMA.value:
        return LlamaService(llm=llm, api_key=api_key)
    elif provider == Provider.CUSTOM.value:
        return CustomLLMService(llm=llm, api_key=api_key)
    else:
        # Default to OpenAI-compatible
        return OpenAIService(llm=llm, api_key=api_key)


# ========== Graph Nodes ==========

async def plan_node(state: ArtifactState) -> Dict[str, Any]:
    """
    Planning node - Creates the artifact with title and outline.

    This node:
    1. Calls LLM with planning prompt
    2. Parses create_artifact tool call
    3. Creates artifact in database
    4. Updates state with artifact info

    Checkpointed: Yes - saves artifact_id and outline

    If artifact_id is already set (resume case), skips planning and returns existing state.
    """
    logger.info(f"Plan node: Starting for conversation {state['conversation_id']}")

    # RESUME CHECK: If artifact already exists, skip planning and pass through
    if state.get("artifact_id") and state["artifact_id"] > 0:
        logger.info(f"Plan node: Skipping - artifact {state['artifact_id']} already exists (resume)")

        # Update artifact status to GENERATING in database for resume
        await update_artifact_db(state["artifact_id"], status=ArtifactStatus.GENERATING)
        logger.info(f"Plan node: Updated artifact {state['artifact_id']} status to GENERATING (resume)")

        # Return state with ARTIFACT_INIT chunk for frontend to know we're resuming
        message_id = state.get("message_id", "")
        init_chunk = f"__ARTIFACT_INIT__|{state['artifact_id']}|{state['title']}|{state['outline']}|{state['estimated_sections']}|{message_id}"

        return {
            "pending_chunks": [init_chunk],
            "status": "generating",
        }

    try:
        # Get LLM and conversation
        llm = await get_llm(state["llm_id"])
        conversation = await get_conversation(state["conversation_id"])

        # Get AI service
        User = get_user_model()
        user = None
        if state.get("user_id"):
            user = await sync_to_async(User.objects.get)(id=state["user_id"])
        
        ai_service = await get_ai_service(llm, user)
        
        # Build planning prompt
        system_prompt = get_planning_prompt()
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": state["user_message"]}
        ]
        
        # Get planning tools
        tools = ArtifactTools.get_planning_tools()
        
        # Call LLM for planning
        response_text = ""
        tool_calls = []
        
        async for chunk, usage in ai_service.stream_chat_completion(
            messages=messages,
            max_tokens=2000,
            temperature=0.7,
            tools=tools
        ):
            if chunk:
                response_text += chunk
            if usage and usage.get("tool_calls"):
                tool_calls.extend(usage["tool_calls"])
        
        # Parse tool call to get artifact details
        artifact_type = "document"
        title = "Untitled Document"
        outline = "1. Introduction\n2. Main Content\n3. Conclusion"
        estimated_sections = 3
        language = None
        
        for tool_call in tool_calls:
            logger.info(f"Plan node: Processing tool_call: {tool_call}")
            if tool_call.get("name") == ArtifactTools.CREATE_ARTIFACT:
                args = ArtifactTools.parse_tool_arguments(
                    tool_call.get("arguments", "{}")
                )
                logger.info(f"Plan node: Parsed create_artifact args: {args}")
                artifact_type = args.get("artifact_type", "document")
                title = args.get("title", title)
                outline = args.get("outline", outline)
                estimated_sections = args.get("estimated_sections", 3)
                language = args.get("language")
                logger.info(f"Plan node: Extracted title='{title}', type={artifact_type}, sections={estimated_sections}")
                break
        
        # If no tool call, try to parse from response text
        if not tool_calls and response_text:
            # Extract title if present
            if "Title:" in response_text:
                title_line = response_text.split("Title:")[1].split("\n")[0].strip()
                title = title_line or title
            
            # Count outline sections
            outline_lines = [l for l in response_text.split("\n") if l.strip().startswith(("1.", "2.", "3.", "4.", "5.", "6.", "7.", "8.", "9.", "10.", "-", "*"))]
            if outline_lines:
                outline = "\n".join(outline_lines)
                estimated_sections = len(outline_lines)
        
        # Get message object to link immediately (so artifactId appears in conversation history on reload)
        message_obj = None
        message_id = state.get("message_id")
        if message_id:
            try:
                message_obj = await sync_to_async(Message.active_objects.get)(id=message_id)
            except Message.DoesNotExist:
                logger.warning(f"Plan node: Message {message_id} not found for artifact linking")

        # Create artifact in database WITH message link
        artifact = await create_artifact_db(
            conversation=conversation,
            message=message_obj,  # Link immediately instead of None
            artifact_type=artifact_type,
            title=title,
            outline=outline,
            estimated_sections=estimated_sections,
            language=language,
        )

        # Update artifact status to GENERATING in database
        # This is crucial for pause detection to work correctly
        await update_artifact_db(artifact.id, status=ArtifactStatus.GENERATING)

        logger.info(f"Plan node: Created artifact {artifact.id} - {title}, linked to message {message_id}, status set to GENERATING")

        # Include message_id in the init chunk for frontend linking
        message_id = state.get("message_id") or ""

        return {
            "artifact_id": artifact.id,
            "artifact_type": artifact_type,
            "title": title,
            "outline": outline,
            "estimated_sections": estimated_sections,
            "language": language,
            "status": "generating",
            "pending_chunks": [f"__ARTIFACT_INIT__|{artifact.id}|{title}|{outline}|{estimated_sections}|{message_id}"],
        }
        
    except Exception as e:
        logger.exception(f"Plan node error: {str(e)}")
        return {
            "status": "error",
            "error": str(e),
        }


async def generate_section_node(state: ArtifactState) -> Dict[str, Any]:
    """
    Section generation node - Generates content for the next section.

    This node:
    1. Checks if user requested pause
    2. Gets current section from state
    3. Calls LLM with section prompt
    4. Appends content to artifact
    5. Updates progress

    Checkpointed: Yes - saves content and section number after each section
    """
    logger.info(f"Generate section node: Section {state['current_section'] + 1} of {state['estimated_sections']}")

    try:
        # Check if user requested pause before starting section
        if state.get("artifact_id"):
            is_paused = await check_artifact_paused(state["artifact_id"])
            if is_paused:
                logger.info(f"Generate section node: Artifact {state['artifact_id']} paused by user")
                return {
                    "status": "paused",
                    "pending_chunks": [f"__ARTIFACT_PAUSE__|{state['artifact_id']}|{state['current_section']}|{state['estimated_sections'] - state['current_section']}"],
                }

        # Get LLM
        llm = await get_llm(state["llm_id"])

        # Get user if available
        User = get_user_model()
        user = None
        if state.get("user_id"):
            user = await sync_to_async(User.objects.get)(id=state["user_id"])
        
        ai_service = await get_ai_service(llm, user)
        
        section_number = state["current_section"] + 1
        
        # Build generation prompt
        system_prompt = get_generation_prompt(
            title=state["title"],
            artifact_type=state["artifact_type"],
            outline=state["outline"],
            current_section=section_number,
            total_sections=state["estimated_sections"],
            content_preview=state["content"][-1000:] if state["content"] else ""
        )
        
        user_prompt = get_section_user_prompt(state["outline"], section_number)
        
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]
        
        tools = ArtifactTools.get_generation_tools()
        
        # Generate section content
        # Note: Pause is checked AFTER section completes (not mid-stream)
        # because the REST API updates the DB and we check DB after each section
        section_content = ""
        chunks = []

        async for chunk, usage in ai_service.stream_chat_completion(
            messages=messages,
            max_tokens=4000,
            temperature=0.7,
            tools=tools
        ):
            if chunk:
                section_content += chunk
                chunks.append(chunk)

            # Handle tool calls
            if usage and usage.get("tool_calls"):
                for tool_call in usage["tool_calls"]:
                    if tool_call.get("name") == ArtifactTools.UPDATE_ARTIFACT:
                        args = ArtifactTools.parse_tool_arguments(
                            tool_call.get("arguments", "{}")
                        )
                        content = args.get("content", "")
                        if content:
                            section_content = content
                            chunks = [content]
        
        # Update artifact in database
        new_content = state["content"] + ("\n\n" if state["content"] else "") + section_content
        await update_artifact_db(
            state["artifact_id"],
            content=new_content,
            current_section=section_number,
        )

        # Calculate progress
        progress = section_number / state["estimated_sections"]

        logger.info(f"Generate section node: Completed section {section_number}, progress {progress:.2%}")

        # Check if pause was requested DURING section generation
        # This ensures we stop after completing the current section
        if state.get("artifact_id"):
            is_paused = await check_artifact_paused(state["artifact_id"])
            if is_paused:
                logger.info(f"Generate section node: Pause detected after completing section {section_number}")
                return {
                    "content": new_content,
                    "current_section": section_number,
                    "status": "paused",
                    "pending_chunks": [
                        f"__ARTIFACT_STREAM__|{state['artifact_id']}|{section_number}|{progress}|{section_content}",
                        f"__ARTIFACT_PAUSE__|{state['artifact_id']}|{section_number}|{state['estimated_sections'] - section_number}",
                    ],
                }

        return {
            "content": new_content,
            "current_section": section_number,
            "pending_chunks": [f"__ARTIFACT_STREAM__|{state['artifact_id']}|{section_number}|{progress}|{section_content}"],
        }
        
    except Exception as e:
        logger.exception(f"Generate section node error: {str(e)}")
        return {
            "error": str(e),
            "retry_count": state.get("retry_count", 0) + 1,
        }


async def checkpoint_node(state: ArtifactState) -> Dict[str, Any]:
    """
    Checkpoint node - Saves progress to database checkpoint.
    
    This creates a manual checkpoint in our database in addition to
    LangGraph's automatic checkpointing.
    
    Checkpointed: Yes
    """
    logger.info(f"Checkpoint node: Iteration {state['iteration_count'] + 1}")
    
    try:
        artifact = await get_artifact(state["artifact_id"])
        
        # Create checkpoint in database
        await create_checkpoint_db(
            artifact=artifact,
            content_snapshot=state["content"],
            current_section=state["current_section"],
            iteration_count=state["iteration_count"] + 1,
            state_data={
                "status": state["status"],
                "thread_id": state["thread_id"],
            }
        )
        
        return {
            "iteration_count": state["iteration_count"] + 1,
        }
        
    except Exception as e:
        logger.exception(f"Checkpoint node error: {str(e)}")
        # Don't fail the whole workflow for checkpoint error
        return {
            "iteration_count": state["iteration_count"] + 1,
        }


async def pause_node(state: ArtifactState) -> Dict[str, Any]:
    """
    Pause node - Pauses artifact generation for user continuation.
    
    This node:
    1. Updates artifact status to paused
    2. Creates final checkpoint
    3. Sends pause message
    
    Checkpointed: Yes - saves paused state
    """
    logger.info(f"Pause node: Pausing artifact {state['artifact_id']}")
    
    try:
        # Update artifact status
        await update_artifact_db(
            state["artifact_id"],
            status=ArtifactStatus.PAUSED,
        )
        
        # Create checkpoint
        artifact = await get_artifact(state["artifact_id"])
        await create_checkpoint_db(
            artifact=artifact,
            content_snapshot=state["content"],
            current_section=state["current_section"],
            iteration_count=state["iteration_count"],
            state_data={
                "status": "paused",
                "thread_id": state["thread_id"],
            }
        )
        
        sections_remaining = state["estimated_sections"] - state["current_section"]
        
        return {
            "status": "paused",
            "pending_chunks": [f"__ARTIFACT_PAUSE__|{state['artifact_id']}|{state['current_section']}|{sections_remaining}"],
        }
        
    except Exception as e:
        logger.exception(f"Pause node error: {str(e)}")
        return {
            "status": "paused",
            "error": str(e),
        }


async def complete_node(state: ArtifactState) -> Dict[str, Any]:
    """
    Complete node - Finalizes the artifact.
    
    This node:
    1. Updates artifact status to completed
    2. Sends completion message
    
    Checkpointed: Yes - saves completed state
    """
    logger.info(f"Complete node: Completing artifact {state['artifact_id']}")
    
    try:
        # Update artifact status
        artifact = await update_artifact_db(
            state["artifact_id"],
            status=ArtifactStatus.COMPLETED,
        )
        
        # Get word count
        word_count = len(state["content"].split())
        
        return {
            "status": "completed",
            "pending_chunks": [f"__ARTIFACT_COMPLETE__|{state['artifact_id']}|{word_count}"],
        }
        
    except Exception as e:
        logger.exception(f"Complete node error: {str(e)}")
        return {
            "status": "completed",
            "error": str(e),
        }


async def error_node(state: ArtifactState) -> Dict[str, Any]:
    """
    Error node - Handles errors in the workflow.
    
    This node:
    1. Updates artifact status to error
    2. Logs the error
    3. Sends error message
    
    Checkpointed: Yes
    """
    logger.error(f"Error node: {state.get('error', 'Unknown error')}")
    
    try:
        if state.get("artifact_id"):
            await update_artifact_db(
                state["artifact_id"],
                status=ArtifactStatus.ERROR,
            )
        
        return {
            "status": "error",
            "pending_chunks": [f"__ARTIFACT_ERROR__|{state.get('error', 'Generation failed')}"],
        }
        
    except Exception as e:
        logger.exception(f"Error node error: {str(e)}")
        return {
            "status": "error",
        }


# ========== Conditional Edge Functions ==========

def should_continue_generating(state: ArtifactState) -> str:
    """
    Determine what to do after generating a section.

    Returns:
        - "pause" if user requested pause
        - "generate_section" if more sections needed and within iteration limit
        - "checkpoint" if iteration batch complete
        - "complete" if all sections done
        - "error" if there was an error
    """
    # Check for errors
    if state.get("error") and state.get("retry_count", 0) >= 3:
        return "error"

    # Check if paused (status set by generate_section_node when pause detected)
    if state.get("status") == "paused":
        return "pause"

    # Check if all sections complete
    if state["current_section"] >= state["estimated_sections"]:
        return "complete"

    # Check if we should checkpoint (end of iteration batch)
    sections_in_iteration = state["current_section"] % state["sections_per_iteration"]
    if sections_in_iteration == 0 and state["current_section"] > 0:
        return "checkpoint"

    # Continue generating
    return "generate_section"


def should_continue_after_checkpoint(state: ArtifactState) -> str:
    """
    Determine what to do after checkpointing.
    
    Returns:
        - "pause" if max iterations reached
        - "generate_section" if more sections to generate
        - "complete" if all sections done
    """
    # Check if all sections complete
    if state["current_section"] >= state["estimated_sections"]:
        return "complete"
    
    # Check if max iterations reached
    if state["iteration_count"] >= state["max_iterations"]:
        return "pause"
    
    # Continue generating
    return "generate_section"

