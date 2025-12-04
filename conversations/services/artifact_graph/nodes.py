"""
LangGraph Node Functions for Artifact Generation

Each function represents a node in the artifact generation graph.
These are checkpointed automatically by LangGraph at each transition.
"""

import logging
from typing import Dict, Any, Optional
from asgiref.sync import sync_to_async

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


# ========== AI Service Helper ==========

async def get_ai_service(llm: LLM, user=None):
    """Get the appropriate AI service for an LLM."""
    provider = llm.provider
    
    # Get API key
    if user:
        api_key = await sync_to_async(get_provider_api_key_for_user)(user, provider)
    else:
        api_key = await sync_to_async(get_provider_api_key)(provider)
    
    if not api_key:
        raise ValueError(f"No API key found for provider {provider}")
    
    # Get base URL for custom providers
    base_url = getattr(llm, 'base_url', None)
    
    # Return appropriate service
    if provider == Provider.OPENAI.value:
        return OpenAIService(api_key=api_key, model=llm.identifier)
    elif provider == Provider.CLAUDE.value:
        return ClaudeService(api_key=api_key, model=llm.identifier)
    elif provider == Provider.GEMINI.value:
        return GeminiService(api_key=api_key, model=llm.identifier)
    elif provider == Provider.LLAMA.value:
        return LlamaService(model=llm.identifier)
    elif provider == Provider.CUSTOM.value and base_url:
        return CustomLLMService(api_key=api_key, model=llm.identifier, base_url=base_url)
    else:
        # Default to OpenAI-compatible
        return OpenAIService(api_key=api_key, model=llm.identifier)


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
    """
    logger.info(f"Plan node: Starting for conversation {state['conversation_id']}")
    
    try:
        # Get LLM and conversation
        llm = await get_llm(state["llm_id"])
        conversation = await get_conversation(state["conversation_id"])
        
        # Get AI service
        from django.contrib.auth import get_user_model
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
            if tool_call.get("name") == ArtifactTools.CREATE_ARTIFACT:
                args = ArtifactTools.parse_tool_arguments(
                    tool_call.get("arguments", "{}")
                )
                artifact_type = args.get("artifact_type", "document")
                title = args.get("title", title)
                outline = args.get("outline", outline)
                estimated_sections = args.get("estimated_sections", 3)
                language = args.get("language")
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
        
        # Create artifact in database
        artifact = await create_artifact_db(
            conversation=conversation,
            message=None,  # Will be set later
            artifact_type=artifact_type,
            title=title,
            outline=outline,
            estimated_sections=estimated_sections,
            language=language,
        )
        
        logger.info(f"Plan node: Created artifact {artifact.id} - {title}")
        
        return {
            "artifact_id": artifact.id,
            "artifact_type": artifact_type,
            "title": title,
            "outline": outline,
            "estimated_sections": estimated_sections,
            "language": language,
            "status": "generating",
            "pending_chunks": [f"__ARTIFACT_INIT__|{artifact.id}|{title}|{outline}|{estimated_sections}"],
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
    1. Gets current section from state
    2. Calls LLM with section prompt
    3. Appends content to artifact
    4. Updates progress
    
    Checkpointed: Yes - saves content and section number after each section
    """
    logger.info(f"Generate section node: Section {state['current_section'] + 1} of {state['estimated_sections']}")
    
    try:
        # Get LLM
        llm = await get_llm(state["llm_id"])
        
        # Get user if available
        from django.contrib.auth import get_user_model
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
        - "generate_section" if more sections needed and within iteration limit
        - "checkpoint" if iteration batch complete
        - "complete" if all sections done
        - "error" if there was an error
    """
    # Check for errors
    if state.get("error") and state.get("retry_count", 0) >= 3:
        return "error"
    
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

