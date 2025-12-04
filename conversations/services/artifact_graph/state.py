"""
Artifact State Schema for LangGraph

Defines the state that flows through the artifact generation graph.
This state is automatically checkpointed by LangGraph at each node transition.
"""

from typing import TypedDict, Optional, List, Any, Annotated
from operator import add


class ArtifactState(TypedDict, total=False):
    """
    State schema for artifact generation workflow.
    
    This state is persisted at each node transition, enabling:
    - Crash recovery from exact failure point
    - Pause/resume from any checkpoint
    - State reconstruction on reconnect
    
    Attributes:
        # Identifiers
        artifact_id: Database ID of the artifact
        conversation_id: ID of the parent conversation
        message_id: ID of the AI message associated with artifact
        thread_id: LangGraph thread ID for checkpointing
        
        # User context
        user_id: ID of the user (None for public bots)
        user_message: Original user request
        
        # LLM context
        llm_id: ID of the LLM being used
        llm_provider: Provider name (openai, claude, etc.)
        
        # Artifact metadata
        artifact_type: Type (document, code, diagram)
        title: Artifact title
        outline: Section outline
        language: Programming language (for code artifacts)
        
        # Content tracking
        content: Accumulated content so far
        current_section: Current section being generated (1-indexed)
        estimated_sections: Total estimated sections
        
        # Generation state
        status: Current status (planning, generating, paused, completed, error)
        iteration_count: Number of generation iterations completed
        sections_per_iteration: Sections to generate per iteration
        max_iterations: Maximum iterations before auto-pause
        
        # Streaming
        pending_chunks: Chunks waiting to be sent to client
        
        # Error handling
        error: Error message if any
        retry_count: Number of retries attempted
        
        # Metadata
        metadata: Additional metadata dict
    """
    
    # Identifiers
    artifact_id: Optional[int]
    conversation_id: str
    message_id: Optional[int]
    thread_id: str
    
    # User context
    user_id: Optional[int]
    user_message: str
    
    # LLM context
    llm_id: int
    llm_provider: str
    
    # Artifact metadata
    artifact_type: str
    title: str
    outline: str
    language: Optional[str]
    
    # Content tracking
    content: str
    current_section: int
    estimated_sections: int
    
    # Generation state
    status: str
    iteration_count: int
    sections_per_iteration: int
    max_iterations: int
    
    # Streaming - use Annotated with add for list accumulation
    pending_chunks: Annotated[List[str], add]
    
    # Error handling
    error: Optional[str]
    retry_count: int
    
    # Metadata
    metadata: dict


def create_initial_state(
    conversation_id: str,
    user_message: str,
    llm_id: int,
    llm_provider: str,
    thread_id: str,
    user_id: Optional[int] = None,
    message_id: Optional[int] = None,
    sections_per_iteration: int = 3,
    max_iterations: int = 10,
) -> ArtifactState:
    """
    Create initial state for a new artifact generation.

    Args:
        conversation_id: ID of the conversation
        user_message: User's request message
        llm_id: ID of the LLM to use
        llm_provider: Provider name
        thread_id: LangGraph thread ID
        user_id: Optional user ID
        message_id: Optional AI message ID to link artifact to
        sections_per_iteration: Sections per iteration
        max_iterations: Max iterations before pause

    Returns:
        Initial ArtifactState
    """
    return ArtifactState(
        # Identifiers
        artifact_id=None,
        conversation_id=conversation_id,
        message_id=message_id,
        thread_id=thread_id,
        
        # User context
        user_id=user_id,
        user_message=user_message,
        
        # LLM context
        llm_id=llm_id,
        llm_provider=llm_provider,
        
        # Artifact metadata (set during planning)
        artifact_type="document",
        title="",
        outline="",
        language=None,
        
        # Content tracking
        content="",
        current_section=0,
        estimated_sections=0,
        
        # Generation state
        status="planning",
        iteration_count=0,
        sections_per_iteration=sections_per_iteration,
        max_iterations=max_iterations,
        
        # Streaming
        pending_chunks=[],
        
        # Error handling
        error=None,
        retry_count=0,
        
        # Metadata
        metadata={},
    )


def create_resume_state(
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
    sections_per_iteration: int = 3,
    max_iterations: int = 10,
) -> ArtifactState:
    """
    Create state for resuming a paused artifact.
    
    Args:
        artifact_id: ID of the artifact to resume
        conversation_id: ID of the conversation
        thread_id: LangGraph thread ID
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
        sections_per_iteration: Sections per iteration
        max_iterations: Max iterations
        
    Returns:
        ArtifactState configured for resumption
    """
    return ArtifactState(
        # Identifiers
        artifact_id=artifact_id,
        conversation_id=conversation_id,
        message_id=None,
        thread_id=thread_id,
        
        # User context
        user_id=user_id,
        user_message="Continue generating",
        
        # LLM context
        llm_id=llm_id,
        llm_provider=llm_provider,
        
        # Artifact metadata
        artifact_type=artifact_type,
        title=title,
        outline=outline,
        language=language,
        
        # Content tracking
        content=content,
        current_section=current_section,
        estimated_sections=estimated_sections,
        
        # Generation state - set to generating for resume
        status="generating",
        iteration_count=iteration_count,
        sections_per_iteration=sections_per_iteration,
        max_iterations=max_iterations,
        
        # Streaming
        pending_chunks=[],
        
        # Error handling
        error=None,
        retry_count=0,
        
        # Metadata
        metadata={},
    )

