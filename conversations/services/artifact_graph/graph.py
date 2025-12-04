"""
LangGraph Artifact Generation Workflow

Defines the state machine for artifact generation with automatic checkpointing.
"""

import logging
import asyncio
from typing import Optional, AsyncGenerator, Tuple, Dict, Any
from functools import lru_cache

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from django.conf import settings

from .state import ArtifactState, create_initial_state, create_resume_state
from .nodes import (
    plan_node,
    generate_section_node,
    checkpoint_node,
    pause_node,
    complete_node,
    error_node,
    should_continue_generating,
    should_continue_after_checkpoint,
)

logger = logging.getLogger(__name__)


def create_artifact_graph() -> StateGraph:
    """
    Create the artifact generation state graph.
    
    Graph Structure:
    ```
    START → plan → generate_section ←──┐
                        │              │
                        ├── checkpoint ┤
                        │              │
                        ├── pause → END
                        │
                        └── complete → END
                        
            error → END (from any node)
    ```
    
    Checkpoints are saved at every node transition by LangGraph.
    Additional manual checkpoints are created in checkpoint_node.
    
    Returns:
        Compiled StateGraph ready for execution
    """
    # Create the graph
    workflow = StateGraph(ArtifactState)
    
    # Add nodes
    workflow.add_node("plan", plan_node)
    workflow.add_node("generate_section", generate_section_node)
    workflow.add_node("checkpoint", checkpoint_node)
    workflow.add_node("pause", pause_node)
    workflow.add_node("complete", complete_node)
    workflow.add_node("error", error_node)
    
    # Set entry point
    workflow.set_entry_point("plan")
    
    # Add edges from plan
    workflow.add_edge("plan", "generate_section")
    
    # Add conditional edges from generate_section
    workflow.add_conditional_edges(
        "generate_section",
        should_continue_generating,
        {
            "generate_section": "generate_section",
            "checkpoint": "checkpoint",
            "complete": "complete",
            "pause": "pause",  # User-requested pause
            "error": "error",
        }
    )
    
    # Add conditional edges from checkpoint
    workflow.add_conditional_edges(
        "checkpoint",
        should_continue_after_checkpoint,
        {
            "generate_section": "generate_section",
            "pause": "pause",
            "complete": "complete",
        }
    )
    
    # Terminal nodes
    workflow.add_edge("pause", END)
    workflow.add_edge("complete", END)
    workflow.add_edge("error", END)
    
    return workflow


# Singleton checkpointer instance
_checkpointer = None


async def get_checkpointer():
    """
    Get or create the checkpointer based on database backend.

    - Development (SQLite): Uses MemorySaver (in-memory, no persistence across restarts)
    - Production (PostgreSQL): Uses AsyncPostgresSaver (persistent checkpoints)

    Returns:
        Checkpointer instance (MemorySaver or AsyncPostgresSaver)
    """
    global _checkpointer

    if _checkpointer is None:
        # Get database settings from Django
        db_settings = settings.DATABASES.get('default', {})
        db_engine = db_settings.get('ENGINE', '')

        if 'sqlite' in db_engine:
            # Development: Use in-memory checkpointer
            # Note: State is lost on server restart, but works for basic testing
            _checkpointer = MemorySaver()
            logger.info("LangGraph MemorySaver checkpointer initialized (development mode)")
        else:
            # Production: Use PostgreSQL checkpointer
            # Build connection string
            # Format: postgresql://user:password@host:port/database
            user = db_settings.get('USER', 'postgres')
            password = db_settings.get('PASSWORD', '')
            host = db_settings.get('HOST', 'localhost')
            port = db_settings.get('PORT', '5432')
            database = db_settings.get('NAME', 'dare')

            connection_string = f"postgresql://{user}:{password}@{host}:{port}/{database}"

            # Create checkpointer
            _checkpointer = AsyncPostgresSaver.from_conn_string(connection_string)

            # Set up tables (creates if not exists)
            await _checkpointer.setup()

            logger.info("LangGraph Postgres checkpointer initialized (production mode)")

    return _checkpointer


# Compiled graph singleton
_compiled_graph = None


async def get_artifact_app():
    """
    Get the compiled artifact generation app with checkpointing.
    
    Returns:
        Compiled LangGraph app ready for execution
    """
    global _compiled_graph
    
    if _compiled_graph is None:
        # Create graph
        workflow = create_artifact_graph()
        
        # Get checkpointer
        checkpointer = await get_checkpointer()
        
        # Compile with checkpointer
        _compiled_graph = workflow.compile(checkpointer=checkpointer)
        
        logger.info("LangGraph artifact workflow compiled")
    
    return _compiled_graph


async def run_artifact_generation(
    conversation_id: str,
    user_message: str,
    llm_id: int,
    llm_provider: str,
    thread_id: str,
    user_id: Optional[int] = None,
    message_id: Optional[int] = None,
    send_callback=None,
) -> AsyncGenerator[Tuple[str, Optional[Dict]], None]:
    """
    Run artifact generation workflow.

    Args:
        conversation_id: ID of the conversation
        user_message: User's request
        llm_id: ID of the LLM to use
        llm_provider: Provider name
        thread_id: Unique thread ID for this generation
        user_id: Optional user ID
        message_id: Optional AI message ID to link artifact to
        send_callback: Async callback for sending messages

    Yields:
        Tuple of (chunk: str, metadata: dict)
    """
    app = await get_artifact_app()

    # Create initial state
    initial_state = create_initial_state(
        conversation_id=conversation_id,
        user_message=user_message,
        llm_id=llm_id,
        llm_provider=llm_provider,
        thread_id=thread_id,
        user_id=user_id,
        message_id=message_id,
    )
    
    # Configuration for this thread
    config = {"configurable": {"thread_id": thread_id}}
    
    try:
        # Track chunks we've already sent to avoid duplicates
        # (pending_chunks accumulates due to Annotated[List, add])
        sent_chunk_count = 0

        # Stream execution
        async for event in app.astream(initial_state, config, stream_mode="values"):
            # Yield control to event loop to allow pause requests to be processed
            await asyncio.sleep(0)

            # Process only NEW pending chunks (skip already sent ones)
            pending_chunks = event.get("pending_chunks", [])
            new_chunks = pending_chunks[sent_chunk_count:]

            for chunk_data in new_chunks:
                # Parse chunk type and data
                parsed = await _parse_chunk(chunk_data, send_callback)
                if parsed:
                    yield parsed

            # Update sent count
            sent_chunk_count = len(pending_chunks)

            # Check for completion or error
            status = event.get("status")
            if status in ("completed", "paused", "error"):
                yield "", {"status": status, "artifact_id": event.get("artifact_id")}
                break

    except Exception as e:
        logger.exception(f"Error in artifact generation: {str(e)}")
        yield f"Error: {str(e)}", {"error": str(e)}


async def resume_artifact_generation(
    artifact_id: int,
    conversation_id: str,
    thread_id: str,
    content: str,
    current_section: int,
    estimated_sections: int,
    iteration_count: int,
    llm_id: int,
    llm_provider: str,
    title: str,
    outline: str,
    artifact_type: str = "document",
    user_id: Optional[int] = None,
    language: Optional[str] = None,
    send_callback=None,
) -> AsyncGenerator[Tuple[str, Optional[Dict]], None]:
    """
    Resume a paused artifact generation.
    
    Args:
        artifact_id: ID of the artifact to resume
        conversation_id: ID of the conversation
        thread_id: Same thread ID used previously
        content: Content generated so far
        current_section: Section to resume from
        estimated_sections: Total sections
        iteration_count: Previous iteration count
        llm_id: ID of the LLM
        llm_provider: Provider name
        title: Artifact title
        outline: Artifact outline
        artifact_type: Type of artifact
        user_id: Optional user ID
        language: Optional language for code
        send_callback: Async callback for sending messages
        
    Yields:
        Tuple of (chunk: str, metadata: dict)
    """
    app = await get_artifact_app()
    
    # Create resume state
    resume_state = create_resume_state(
        artifact_id=artifact_id,
        conversation_id=conversation_id,
        thread_id=thread_id,
        content=content,
        current_section=current_section,
        estimated_sections=estimated_sections,
        iteration_count=iteration_count,
        llm_id=llm_id,
        llm_provider=llm_provider,
        title=title,
        outline=outline,
        artifact_type=artifact_type,
        user_id=user_id,
        language=language,
    )
    
    # Configuration - use same thread_id to resume from checkpoint
    config = {"configurable": {"thread_id": thread_id}}
    
    try:
        # Track chunks we've already sent to avoid duplicates
        sent_chunk_count = 0

        # Resume from generate_section (skip plan since artifact exists)
        async for event in app.astream(resume_state, config, stream_mode="values"):
            # Yield control to event loop to allow pause requests to be processed
            await asyncio.sleep(0)

            # Process only NEW pending chunks
            pending_chunks = event.get("pending_chunks", [])
            new_chunks = pending_chunks[sent_chunk_count:]

            for chunk_data in new_chunks:
                parsed = await _parse_chunk(chunk_data, send_callback)
                if parsed:
                    yield parsed

            sent_chunk_count = len(pending_chunks)

            # Check for completion or error
            status = event.get("status")
            if status in ("completed", "paused", "error"):
                yield "", {"status": status, "artifact_id": event.get("artifact_id")}
                break

    except Exception as e:
        logger.exception(f"Error resuming artifact: {str(e)}")
        yield f"Error: {str(e)}", {"error": str(e)}


async def _safe_send(send_callback, msg) -> bool:
    """Safely send a message via callback, handling disconnection gracefully."""
    if not send_callback:
        return True
    try:
        await send_callback(msg)
        return True
    except Exception as e:
        logger.debug(f"Failed to send artifact message (client may have disconnected): {type(e).__name__}")
        return False


async def _parse_chunk(chunk_data: str, send_callback=None) -> Optional[Tuple[str, Optional[Dict]]]:
    """Parse chunk data and optionally send via callback."""
    if not chunk_data:
        return None

    parts = chunk_data.split("|", 1)
    chunk_type = parts[0] if parts else ""

    if chunk_type == "__ARTIFACT_INIT__":
        # Format: __ARTIFACT_INIT__|artifact_id|title|outline|estimated_sections|message_id
        data_parts = chunk_data.split("|")
        if len(data_parts) >= 5:
            msg = {
                "type": "artifact_init",
                "artifactId": data_parts[1],
                "title": data_parts[2],
                "outline": data_parts[3],
                "estimatedSections": int(data_parts[4]),
            }
            # Include messageId if present (6th element)
            if len(data_parts) >= 6 and data_parts[5]:
                msg["messageId"] = data_parts[5]
            await _safe_send(send_callback, msg)
            return "", {"type": "artifact_init", "artifact_id": data_parts[1]}
    
    elif chunk_type == "__ARTIFACT_STREAM__":
        # Format: __ARTIFACT_STREAM__|artifact_id|section|progress|content
        data_parts = chunk_data.split("|", 4)
        if len(data_parts) >= 5:
            content = data_parts[4]
            msg = {
                "type": "artifact_stream",
                "artifactId": data_parts[1],
                "section": int(data_parts[2]),
                "progress": float(data_parts[3]),
                "chunk": content,
            }
            await _safe_send(send_callback, msg)
            return content, {"type": "artifact_stream", "section": int(data_parts[2])}

    elif chunk_type == "__ARTIFACT_PAUSE__":
        # Format: __ARTIFACT_PAUSE__|artifact_id|current_section|sections_remaining
        data_parts = chunk_data.split("|")
        if len(data_parts) >= 4:
            msg = {
                "type": "artifact_pause",
                "artifactId": data_parts[1],
                "currentSection": int(data_parts[2]),
                "sectionsRemaining": int(data_parts[3]),
            }
            await _safe_send(send_callback, msg)
            return "", {"type": "artifact_pause"}

    elif chunk_type == "__ARTIFACT_COMPLETE__":
        # Format: __ARTIFACT_COMPLETE__|artifact_id|total_words
        data_parts = chunk_data.split("|")
        if len(data_parts) >= 3:
            msg = {
                "type": "artifact_complete",
                "artifactId": data_parts[1],
                "totalWords": int(data_parts[2]),
            }
            await _safe_send(send_callback, msg)
            return "", {"type": "artifact_complete"}

    elif chunk_type == "__ARTIFACT_ERROR__":
        # Format: __ARTIFACT_ERROR__|error_message
        error_msg = chunk_data.replace("__ARTIFACT_ERROR__|", "")
        msg = {
            "type": "error",
            "errorCode": "ARTIFACT_ERROR",
            "errorMessage": error_msg,
        }
        await _safe_send(send_callback, msg)
        return "", {"type": "error", "error": error_msg}
    
    # Return raw content if not a special chunk
    return chunk_data, None

