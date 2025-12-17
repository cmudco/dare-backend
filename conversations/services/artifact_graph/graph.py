"""
LangGraph Artifact Generation Workflow

Defines the state machine for artifact generation with automatic checkpointing.
"""

import logging
from typing import Optional, AsyncGenerator, Tuple, Dict, Any

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from django.conf import settings

from .state import (
    ArtifactState,
    create_initial_state,
    create_resume_state,
    create_modification_state,
)
from .schemas import ArtifactMode
from .nodes import (
    plan_node,
    modify_plan_node,
    generate_section_node,
    checkpoint_node,
    pause_node,
    complete_node,
    error_node,
)
from .transitions import (
    should_continue_generating,
    should_continue_after_checkpoint,
)

logger = logging.getLogger(__name__)


# ========== Unified Graph Builder ==========


def create_artifact_graph(mode: ArtifactMode) -> StateGraph:
    """
    Create the artifact generation state graph.

    Graph Structure:
    ```
    START → [plan|modify_plan] → generate_section ←──┐
                                      │              │
                                      ├── checkpoint ┤
                                      │              │
                                      ├── pause → END
                                      │
                                      └── complete → END

                error_handler → END (from any node)
    ```

    Entry point varies by mode:
    - CREATE/RESUME: plan node
    - MODIFY: modify_plan node

    Checkpoints are saved at every node transition by LangGraph.
    Additional manual checkpoints are created in checkpoint_node.

    Args:
        mode: The artifact workflow mode

    Returns:
        Compiled StateGraph ready for execution
    """
    workflow = StateGraph(ArtifactState)

    # Add all nodes
    workflow.add_node("plan", plan_node)
    workflow.add_node("modify_plan", modify_plan_node)
    workflow.add_node("generate_section", generate_section_node)
    workflow.add_node("checkpoint", checkpoint_node)
    workflow.add_node("pause", pause_node)
    workflow.add_node("complete", complete_node)
    workflow.add_node("error_handler", error_node)

    # Set entry point based on mode
    if mode == ArtifactMode.MODIFY:
        workflow.set_entry_point("modify_plan")
        workflow.add_edge("modify_plan", "generate_section")
    else:  # CREATE or RESUME
        workflow.set_entry_point("plan")
        workflow.add_edge("plan", "generate_section")

    # Add conditional edges from generate_section
    workflow.add_conditional_edges(
        "generate_section",
        should_continue_generating,
        {
            "generate_section": "generate_section",
            "checkpoint": "checkpoint",
            "complete": "complete",
            "pause": "pause",
            "error": "error_handler",
        },
    )

    # Add conditional edges from checkpoint
    workflow.add_conditional_edges(
        "checkpoint",
        should_continue_after_checkpoint,
        {
            "generate_section": "generate_section",
            "pause": "pause",
            "complete": "complete",
        },
    )

    # Terminal nodes
    workflow.add_edge("pause", END)
    workflow.add_edge("complete", END)
    workflow.add_edge("error_handler", END)

    return workflow


# ========== Checkpointer Management ==========


_checkpointer = None
_checkpointer_cm = None  # Keep reference to context manager to prevent it from closing


async def get_checkpointer():
    """
    Get or create the checkpointer based on database backend.

    - Development (SQLite): Uses MemorySaver (in-memory, no persistence across restarts)
    - Production (PostgreSQL): Uses AsyncPostgresSaver (persistent checkpoints)

    Returns:
        Checkpointer instance (MemorySaver or AsyncPostgresSaver)
    """
    global _checkpointer, _checkpointer_cm

    if _checkpointer is None:
        db_settings = settings.DATABASES.get("default", {})
        db_engine = db_settings.get("ENGINE", "")

        logger.info(f"Checkpointer init: DB engine={db_engine}")

        if "sqlite" in db_engine:
            _checkpointer = MemorySaver()
            logger.info(
                "LangGraph MemorySaver checkpointer initialized (development mode)"
            )
        else:
            user = db_settings.get("USER", "postgres")
            password = db_settings.get("PASSWORD", "")
            host = db_settings.get("HOST", "localhost")
            port = db_settings.get("PORT", "5432")
            database = db_settings.get("NAME", "dare")

            # Log connection details (without password)
            logger.info(f"Checkpointer PostgreSQL: host={host}, port={port}, db={database}, user={user}")

            connection_string = f"postgresql://{user}:{password}@{host}:{port}/{database}"
            # AsyncPostgresSaver.from_conn_string() returns an async context manager
            # We need to enter it and keep it alive for the app lifetime
            try:
                _checkpointer_cm = AsyncPostgresSaver.from_conn_string(connection_string)
                logger.info("Checkpointer: Created AsyncPostgresSaver context manager")
                _checkpointer = await _checkpointer_cm.__aenter__()
                logger.info("Checkpointer: Entered async context")
                # Setup the database tables
                await _checkpointer.setup()
                logger.info("Checkpointer: Setup complete")

                logger.info(
                    f"LangGraph Postgres checkpointer initialized (production mode) - type={type(_checkpointer).__name__}"
                )
            except Exception as e:
                logger.exception(f"Checkpointer: Failed to initialize PostgreSQL checkpointer: {e}")
                # Fallback to MemorySaver on error
                _checkpointer = MemorySaver()
                logger.warning("Checkpointer: Falling back to MemorySaver due to PostgreSQL error")

    return _checkpointer


# ========== Compiled Graph Cache ==========

# Cache compiled graphs by mode to avoid recompilation
_compiled_graphs: Dict[ArtifactMode, Any] = {}


async def get_artifact_app(mode: ArtifactMode):
    """
    Get the compiled artifact app for a specific mode.

    Args:
        mode: The artifact workflow mode

    Returns:
        Compiled LangGraph app ready for execution
    """
    global _compiled_graphs

    if mode not in _compiled_graphs:
        workflow = create_artifact_graph(mode)
        checkpointer = await get_checkpointer()
        _compiled_graphs[mode] = workflow.compile(checkpointer=checkpointer)
        logger.info(f"LangGraph artifact workflow compiled for mode={mode.value}")

    return _compiled_graphs[mode]


# ========== Unified Workflow Runner ==========


async def run_artifact_workflow(
    mode: ArtifactMode,
    # Common required params
    conversation_id: str,
    llm_id: int,
    llm_provider: str,
    thread_id: str,
    # Common optional params
    user_id: Optional[int] = None,
    message_id: Optional[int] = None,
    send_callback=None,
    # CREATE mode params
    user_message: Optional[str] = None,
    # RESUME mode params
    artifact_id: Optional[int] = None,
    content: Optional[str] = None,
    current_section: Optional[int] = None,
    estimated_sections: Optional[int] = None,
    iteration_count: Optional[int] = None,
    title: Optional[str] = None,
    outline: Optional[str] = None,
    artifact_type: Optional[str] = None,
    language: Optional[str] = None,
    # MODIFY mode params (uses artifact_id, title, artifact_type, language from above)
    original_outline: Optional[str] = None,
    original_content: Optional[str] = None,
    original_sections: Optional[int] = None,
    version: Optional[int] = None,
    # Context for RAG, files, and system prompt (all modes)
    artifact_context: Optional[Dict[str, Any]] = None,
) -> AsyncGenerator[Tuple[str, Optional[Dict]], None]:
    """
    Unified artifact workflow runner.

    Handles all three modes:
    - CREATE: New artifact from user message
    - RESUME: Continue paused artifact
    - MODIFY: Append sections to existing artifact

    Args:
        mode: Workflow mode (CREATE, RESUME, MODIFY)
        conversation_id: ID of the conversation
        llm_id: ID of the LLM to use
        llm_provider: Provider name
        thread_id: Unique thread ID for this execution
        user_id: Optional user ID
        message_id: Optional AI message ID to link artifact to
        send_callback: Async callback for sending messages

        # CREATE mode:
        user_message: User's request

        # RESUME mode:
        artifact_id: ID of artifact to resume
        content: Content generated so far
        current_section: Section to resume from
        estimated_sections: Total sections
        iteration_count: Previous iteration count
        title: Artifact title
        outline: Artifact outline
        artifact_type: Type of artifact
        language: Optional language for code

        # MODIFY mode (uses artifact_id, title, artifact_type, language):
        original_outline: Existing outline
        original_content: Existing content
        original_sections: Number of existing sections
        version: Current version number

    Yields:
        Tuple of (chunk: str, metadata: dict)
    """
    app = await get_artifact_app(mode)

    # Create appropriate initial state based on mode
    if mode == ArtifactMode.CREATE:
        initial_state = create_initial_state(
            conversation_id=conversation_id,
            user_message=user_message or "",
            llm_id=llm_id,
            llm_provider=llm_provider,
            thread_id=thread_id,
            user_id=user_id,
            message_id=message_id,
            artifact_context=artifact_context,
        )
    elif mode == ArtifactMode.RESUME:
        initial_state = create_resume_state(
            artifact_id=artifact_id,
            conversation_id=conversation_id,
            thread_id=thread_id,
            content=content or "",
            current_section=current_section or 0,
            estimated_sections=estimated_sections or 0,
            iteration_count=iteration_count or 0,
            llm_id=llm_id,
            llm_provider=llm_provider,
            title=title or "",
            outline=outline or "",
            artifact_type=artifact_type or "document",
            user_id=user_id,
            language=language,
            artifact_context=artifact_context,
        )
    else:  # MODIFY
        initial_state = create_modification_state(
            artifact_id=artifact_id,
            conversation_id=conversation_id,
            user_message=user_message or "",
            llm_id=llm_id,
            llm_provider=llm_provider,
            thread_id=thread_id,
            title=title or "",
            artifact_type=artifact_type or "document",
            original_outline=original_outline or "",
            original_content=original_content or "",
            original_sections=original_sections or 0,
            version=version or 1,
            user_id=user_id,
            message_id=message_id,
            language=language,
            artifact_context=artifact_context,
        )

    config = {"configurable": {"thread_id": thread_id}}

    try:
        sent_event_count = 0
        checkpointer_type = type(_checkpointer).__name__ if _checkpointer else "None"
        logger.info(f"Workflow starting: mode={mode.value}, thread_id={thread_id}, checkpointer={checkpointer_type}")

        async for event in app.astream(initial_state, config, stream_mode="values"):
            # Check for completion or error FIRST, but process events before breaking
            status = event.get("status")
            is_terminal = status in ("completed", "paused", "error")

            # Process only NEW pending events
            pending_events = event.get("pending_events", [])
            new_events = pending_events[sent_event_count:]

            # Debug logging for pending_events
            if new_events:
                event_types = [getattr(e, 'type', type(e).__name__) for e in new_events]
                logger.info(f"Workflow event: status={status}, pending_events_total={len(pending_events)}, new_events={len(new_events)}, types={event_types}")

            # Extra debug for terminal states
            if is_terminal:
                logger.info(f"Workflow terminal state: status={status}, pending_events={len(pending_events)}, sent_so_far={sent_event_count}, new_to_process={len(new_events)}")
                # Log the actual event objects for debugging
                for i, evt in enumerate(new_events):
                    logger.info(f"  Event {i}: type={getattr(evt, 'type', 'unknown')}, class={type(evt).__name__}, evt={evt}")

            for artifact_event in new_events:
                result = await _process_event(artifact_event, send_callback)
                if result:
                    yield result

            sent_event_count = len(pending_events)

            # Now break after processing all events
            if is_terminal:
                yield_meta = {
                    "status": status,
                    "artifact_id": event.get("artifact_id"),
                }
                # Include version for MODIFY mode
                if mode == ArtifactMode.MODIFY:
                    yield_meta["version"] = event.get("version")
                logger.info(f"Workflow completed: status={status}, artifact_id={event.get('artifact_id')}")
                yield "", yield_meta
                break

    except Exception as e:
        logger.exception(f"Error in artifact workflow (mode={mode.value}): {str(e)}")
        yield f"Error: {str(e)}", {"error": str(e)}


# ========== Event Processing Helpers ==========


async def _safe_send(send_callback, msg) -> bool:
    """Safely send a message via callback, handling disconnection gracefully."""
    if not send_callback:
        return True
    try:
        await send_callback(msg)
        return True
    except Exception as e:
        logger.debug(
            f"Failed to send artifact message (client may have disconnected): {type(e).__name__}"
        )
        return False


async def _process_event(
    artifact_event, send_callback=None
) -> Optional[Tuple[str, Optional[Dict]]]:
    """Process a typed artifact event and send to client."""
    if not artifact_event:
        return None

    # Send WebSocket message using the event's built-in conversion
    msg = artifact_event.to_websocket_message()
    await _safe_send(send_callback, msg)

    # Return appropriate tuple based on event type
    event_type = artifact_event.type

    if event_type == "artifact_init":
        return "", {"type": "artifact_init", "artifact_id": artifact_event.artifact_id}

    elif event_type == "artifact_modify_init":
        return "", {
            "type": "artifact_modify_init",
            "artifact_id": artifact_event.artifact_id,
            "version": artifact_event.version,
        }

    elif event_type == "artifact_stream":
        return artifact_event.content, {
            "type": "artifact_stream",
            "section": artifact_event.section,
        }

    elif event_type == "artifact_pause":
        return "", {"type": "artifact_pause"}

    elif event_type == "artifact_complete":
        logger.info(f"Sending artifact_complete event: artifact_id={artifact_event.artifact_id}, totalWords={artifact_event.total_words}, estimatedSections={artifact_event.estimated_sections}")
        return "", {
            "type": "artifact_complete",
            "artifact_id": artifact_event.artifact_id,
            "total_words": artifact_event.total_words,
            "estimated_sections": artifact_event.estimated_sections,
        }

    elif event_type == "error":
        return "", {"type": "error", "error": artifact_event.error_message}

    # Unknown event type - shouldn't happen with typed events
    return None

