"""
Conditional Edge Functions for Artifact Generation Graph

These functions determine the next node to execute based on current state.
Used by LangGraph's add_conditional_edges().
"""

from .state import ArtifactState


def should_continue_generating(state: ArtifactState) -> str:
    """
    Determine what to do after generating a section.

    Returns:
        - "pause" if user requested pause
        - "generate_section" if more sections needed and within iteration limit
        - "checkpoint" if iteration batch complete
        - "complete" if all sections done
        - "error_handler" if there was an error
    """
    # Check for errors
    if state.get("error") and state.get("retry_count", 0) >= 3:
        return "error_handler"

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
