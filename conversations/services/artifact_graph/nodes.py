"""
LangGraph Node Functions for Artifact Generation

Each function represents a node in the artifact generation graph.
These are checkpointed automatically by LangGraph at each transition.
"""

import logging
from typing import Dict, Any

from asgiref.sync import sync_to_async
from django.contrib.auth import get_user_model

from conversations.models import Message
from conversations.constants import ArtifactStatus
from core.services.llm_utils.artifact_tools import ArtifactTools
from core.prompts.artifact_prompts import (
    get_planning_prompt,
    get_generation_prompt,
    get_section_user_prompt,
    get_append_planning_prompt,
    get_append_generation_prompt,
)

from .state import ArtifactState
from .schemas import (
    get_artifact_plan_schema,
    get_modification_plan_schema,
    ArtifactInitEvent,
    ArtifactModifyInitEvent,
    ArtifactStreamEvent,
    ArtifactPauseEvent,
    ArtifactCompleteEvent,
    ArtifactErrorEvent,
)
from .db_helpers import (
    get_llm,
    get_conversation,
    get_artifact,
    get_conversation_history,
    create_artifact_db,
    create_artifact_version_db,
    update_artifact_db,
    create_checkpoint_db,
    check_artifact_paused,
)
from .context_helpers import retrieve_rag_context_for_artifact
from .ai_services import get_ai_service, get_structured_output_service


logger = logging.getLogger(__name__)


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

        # NOTE: Do NOT send artifact_init here for resume case.
        # handle_continue_artifact already sends artifact_resume with correct currentSection.
        # Sending artifact_init would reset the frontend UI to 0/N sections.
        return {
            "pending_events": [],  # No events - coordinator handles resume notification
            "status": "generating",
        }

    try:
        # Get LLM and conversation
        llm = await get_llm(state["llm_id"])
        conversation = await get_conversation(state["conversation_id"])

        # Get AI service that supports structured output
        User = get_user_model()
        user = None
        if state.get("user_id"):
            user = await sync_to_async(User.objects.get)(id=state["user_id"])

        # Get service for structured output (falls back to OpenAI/Claude if needed)
        planning_service, is_fallback, provider = await get_structured_output_service(llm, user)
        if is_fallback:
            logger.info(f"Plan node: Using fallback service for structured output")

        # Get conversation history for context (helps LLM understand references to previous artifacts)
        history = await get_conversation_history(conversation, limit=6)

        # Build planning prompt with conversation history
        system_prompt = get_planning_prompt()
        
        # Prepend custom system prompt if provided via artifact_context
        artifact_ctx = state.get("artifact_context")
        if artifact_ctx and artifact_ctx.get("system_prompt"):
            system_prompt = f"{artifact_ctx['system_prompt']}\n\n{system_prompt}"
        
        messages = [{"role": "system", "content": system_prompt}]

        # Add conversation history (excluding the current message which we'll add separately)
        # This gives the LLM context about previous artifacts and messages
        for hist_msg in history:
            # Skip if this is the current user message (avoid duplication)
            if hist_msg["role"] == "user" and hist_msg["content"].strip() == state["user_message"].strip():
                continue
            messages.append(hist_msg)

        # Add RAG context from embeddings/files if available
        rag_context = await retrieve_rag_context_for_artifact(
            artifact_context=state.get("artifact_context"),
            query_text=state["user_message"],
            user_id=state.get("user_id"),
        )
        if rag_context:
            logger.info(f"Plan node: Adding RAG context ({len(rag_context)} chars)")
            messages.append({"role": "user", "content": rag_context})

        # Add current user message
        messages.append({"role": "user", "content": state["user_message"]})

        # Use structured output for reliable planning (no more tool calling!)
        # Get provider-specific schema (OpenAI/Claude need additionalProperties, Gemini doesn't)
        schema = get_artifact_plan_schema(provider)
        
        try:
            plan = await planning_service.generate_structured_output(
                messages=messages,
                response_schema=schema,
                max_tokens=2000,
                temperature=0.7,
            )
            
            # Structured output guarantees these fields exist
            artifact_type = plan.get("artifact_type", "document")
            title = plan.get("title", "Untitled Document")
            outline = plan.get("outline", "1. Introduction\n2. Main Content\n3. Conclusion")
            estimated_sections = plan.get("estimated_sections", 3)
            language = plan.get("language")
            
            logger.info(f"Plan node: Structured output - title='{title}', type={artifact_type}, sections={estimated_sections}")
            
        except Exception as e:
            logger.error(f"Plan node: Structured output failed: {str(e)}, using defaults")
            # Fallback to defaults if structured output fails
            artifact_type = "document"
            title = "Untitled Document"
            outline = "1. Introduction\n2. Main Content\n3. Conclusion"
            estimated_sections = 3
            language = None

        
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

        # Include message_id in the init event for frontend linking
        init_event = ArtifactInitEvent(
            artifact_id=artifact.id,
            title=title,
            outline=outline,
            estimated_sections=estimated_sections,
            message_id=state.get("message_id"),
        )

        return {
            "artifact_id": artifact.id,
            "artifact_type": artifact_type,
            "title": title,
            "outline": outline,
            "estimated_sections": estimated_sections,
            "language": language,
            "status": "generating",
            "pending_events": [init_event],
        }
        
    except Exception as e:
        logger.exception(f"Plan node error: {str(e)}")
        return {
            "status": "error",
            "error": str(e),
        }


async def modify_plan_node(state: ArtifactState) -> Dict[str, Any]:
    """
    Modification planning node - Creates NEW artifact version for modifications.

    This node is used for the modification flow (is_modification=True).
    
    KEY CHANGE: Instead of updating the artifact in-place, we CREATE A NEW
    artifact record as a child of the original. This preserves:
    - Original artifact linked to Message 1
    - New version linked to Message 2
    - Full version history via parent_artifact chain
    
    It:
    1. Loads existing artifact context (the parent)
    2. Calls LLM with append_sections prompt
    3. CREATES new artifact version (not in-place update!)
    4. New artifact has parent_artifact pointing to original
    5. Updates ArtifactGroup.latest_version

    Checkpointed: Yes - saves new artifact_id and version
    """
    logger.info(f"Modify plan node: Starting for artifact {state['artifact_id']}")

    try:
        # Get LLM and parent artifact
        llm = await get_llm(state["llm_id"])
        parent_artifact = await get_artifact(state["artifact_id"])
        
        # Get artifact_group_id from parent
        parent_artifact_group_id = await sync_to_async(lambda: parent_artifact.artifact_group_id)()

        # Get AI service that supports structured output
        User = get_user_model()
        user = None
        if state.get("user_id"):
            user = await sync_to_async(User.objects.get)(id=state["user_id"])

        # Get service for structured output (falls back to OpenAI/Claude if needed)
        planning_service, is_fallback, provider = await get_structured_output_service(llm, user)
        if is_fallback:
            logger.info(f"Modify plan node: Using fallback service for structured output")

        # Build append planning prompt with existing artifact context
        system_prompt = get_append_planning_prompt(
            title=state["title"],
            artifact_type=state["artifact_type"],
            outline=state["original_outline"],
            content_preview=state["original_content"],
            current_sections=state["original_sections"],
            user_message=state["user_message"],
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": state["user_message"]}
        ]

        # Use structured output for reliable modification planning
        # Get provider-specific schema
        schema = get_modification_plan_schema(provider)
        
        try:
            plan = await planning_service.generate_structured_output(
                messages=messages,
                response_schema=schema,
                max_tokens=2000,
                temperature=0.7,
            )
            
            # Structured output guarantees these fields exist
            new_sections_outline = plan.get("new_sections_outline", "")
            estimated_new_sections = plan.get("estimated_new_sections", 1)
            
            logger.info(f"Modify plan node: Structured output - new_sections={estimated_new_sections}")
            
        except Exception as e:
            logger.error(f"Modify plan node: Structured output failed: {str(e)}, using defaults")
            # Fallback to defaults if structured output fails
            next_section = state["original_sections"] + 1
            new_sections_outline = f"{next_section}. Additional Content - Based on user request"
            estimated_new_sections = 1

        # If no outline returned, create a default one
        if not new_sections_outline:
            next_section = state["original_sections"] + 1
            new_sections_outline = f"{next_section}. Additional Content - Based on user request"
            estimated_new_sections = 1
            logger.warning("Modify plan node: Empty outline from structured output, using default")

        # Build complete updated outline
        updated_outline = state["original_outline"]
        if updated_outline and not updated_outline.endswith("\n"):
            updated_outline += "\n"
        updated_outline += new_sections_outline

        # Get message object to link to new artifact
        message_obj = None
        message_id = state.get("message_id")
        if message_id:
            try:
                message_obj = await sync_to_async(Message.active_objects.get)(id=message_id)
            except Message.DoesNotExist:
                logger.warning(f"Modify plan node: Message {message_id} not found")

        # CREATE NEW ARTIFACT VERSION (key change!)
        # This creates a new artifact record linked to the parent
        new_artifact = await create_artifact_version_db(
            parent_artifact=parent_artifact,
            new_outline=updated_outline,
            estimated_new_sections=estimated_new_sections,
            message=message_obj,
        )

        new_version = await sync_to_async(lambda: new_artifact.version)()
        new_artifact_id = await sync_to_async(lambda: new_artifact.id)()
        new_artifact_group_id = await sync_to_async(lambda: new_artifact.artifact_group_id)()

        # Calculate new totals before creating event
        new_estimated_total = state["original_sections"] + estimated_new_sections

        logger.info(
            f"Modify plan node: Created NEW artifact {new_artifact_id} v{new_version} "
            f"(parent={state['artifact_id']}), adding {estimated_new_sections} new sections, "
            f"total={new_estimated_total}, starting from section {state['original_sections']}"
        )

        # Send modification init event to frontend with COMPLETE data
        # This ensures frontend doesn't need parent artifact in state
        init_event = ArtifactModifyInitEvent(
            artifact_id=new_artifact_id,  # NEW artifact ID
            parent_artifact_id=state["artifact_id"],  # Original artifact ID
            artifact_group_id=new_artifact_group_id or parent_artifact_group_id,
            title=state["title"],
            outline=new_sections_outline,  # New sections only
            full_outline=updated_outline,  # Complete outline
            new_sections_count=estimated_new_sections,
            total_estimated_sections=new_estimated_total,
            current_section=state["original_sections"],  # Start from parent's current
            existing_content=state["original_content"],  # Preserve parent content
            version=new_version,
            message_id=state.get("message_id"),
        )

        return {
            # IMPORTANT: Update artifact_id to the NEW artifact
            "artifact_id": new_artifact_id,
            "artifact_group_id": new_artifact_group_id or parent_artifact_group_id,
            "parent_artifact_id": state["artifact_id"],
            "outline": updated_outline,
            "estimated_sections": new_estimated_total,
            "new_sections_outline": new_sections_outline,
            "status": "generating",
            "version": new_version,
            "pending_events": [init_event],
        }

    except Exception as e:
        logger.exception(f"Modify plan node error: {str(e)}")
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
                pause_event = ArtifactPauseEvent(
                    artifact_id=state["artifact_id"],
                    current_section=state["current_section"],
                    sections_remaining=state["estimated_sections"] - state["current_section"],
                )
                return {
                    "status": "paused",
                    "pending_events": [pause_event],
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
        
        # Prepend custom system prompt if provided via artifact_context
        artifact_ctx = state.get("artifact_context")
        if artifact_ctx and artifact_ctx.get("system_prompt"):
            system_prompt = f"{artifact_ctx['system_prompt']}\n\n{system_prompt}"
        
        user_prompt = get_section_user_prompt(state["outline"], section_number)
        
        messages = [
            {"role": "system", "content": system_prompt},
        ]
        
        # Add RAG context from embeddings/files if available
        rag_context = await retrieve_rag_context_for_artifact(
            artifact_context=state.get("artifact_context"),
            query_text=f"{state['title']} - section {section_number}: {user_prompt}",
            user_id=state.get("user_id"),
        )
        if rag_context:
            logger.info(f"Generate section node: Adding RAG context ({len(rag_context)} chars)")
            messages.append({"role": "user", "content": rag_context})
        
        messages.append({"role": "user", "content": user_prompt})
        
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
                stream_event = ArtifactStreamEvent(
                    artifact_id=state["artifact_id"],
                    section=section_number,
                    progress=progress,
                    content=section_content,
                )
                pause_event = ArtifactPauseEvent(
                    artifact_id=state["artifact_id"],
                    current_section=section_number,
                    sections_remaining=state["estimated_sections"] - section_number,
                )
                return {
                    "content": new_content,
                    "current_section": section_number,
                    "status": "paused",
                    "pending_events": [stream_event, pause_event],
                }

        stream_event = ArtifactStreamEvent(
            artifact_id=state["artifact_id"],
            section=section_number,
            progress=progress,
            content=section_content,
        )
        return {
            "content": new_content,
            "current_section": section_number,
            "pending_events": [stream_event],
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
        pause_event = ArtifactPauseEvent(
            artifact_id=state["artifact_id"],
            current_section=state["current_section"],
            sections_remaining=sections_remaining,
        )
        
        return {
            "status": "paused",
            "pending_events": [pause_event],
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
        complete_event = ArtifactCompleteEvent(
            artifact_id=state["artifact_id"],
            total_words=word_count,
            estimated_sections=state.get("estimated_sections", 0),
        )
        
        return {
            "status": "completed",
            "pending_events": [complete_event],
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
            "pending_events": [ArtifactErrorEvent(error_message=state.get('error', 'Generation failed'))],
        }
        
    except Exception as e:
        logger.exception(f"Error node error: {str(e)}")
        return {
            "status": "error",
        }
