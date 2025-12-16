"""
Conditional Edge Functions for Artifact Generation Graph

These functions determine the next node to execute based on current state.
Used by LangGraph's add_conditional_edges().
"""

import logging
from .state import ArtifactState

logger = logging.getLogger(__name__)


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
        logger.info(f"Transition: error_handler (artifact_id={state.get('artifact_id')})")
        return "error_handler"

    # Check if paused (status set by generate_section_node when pause detected)
    if state.get("status") == "paused":
        logger.info(f"Transition: pause (artifact_id={state.get('artifact_id')})")
        return "pause"

    # Check if all sections complete
    current_section = state["current_section"]
    estimated_sections = state["estimated_sections"]
    if current_section >= estimated_sections:
        logger.info(f"Transition: complete (artifact_id={state.get('artifact_id')}, current_section={current_section}, estimated_sections={estimated_sections})")
        return "complete"

    # Check if we should checkpoint (end of iteration batch)
    sections_in_iteration = state["current_section"] % state["sections_per_iteration"]
    if sections_in_iteration == 0 and state["current_section"] > 0:
        logger.info(f"Transition: checkpoint (artifact_id={state.get('artifact_id')}, current_section={current_section})")
        return "checkpoint"

    # Continue generating
    logger.info(f"Transition: generate_section (artifact_id={state.get('artifact_id')}, current_section={current_section}/{estimated_sections})")
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
