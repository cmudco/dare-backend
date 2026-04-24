"""
Message preparation utilities for workflow handlers.

Builds the final text payload sent to the LLM for each node type. Step nodes
emit a provider-agnostic, XML-tagged structure so upstream context, the
step's task, and the step's system instruction are never merged into raw
concatenated text.
"""

import logging
from typing import Dict, List, Optional

from .constants import ContextDefaults, PromptTemplate, WorkflowContextTag
from .step_context import ContextRenderer, StepContextBuilder

logger = logging.getLogger(__name__)


# ============================================================================
# Step Message Preparer
# ============================================================================


class StepMessagePreparer:
    """
    Assemble a step's LLM prompt from its static configuration and the
    upstream dependency payload.

    The rendered prompt contains up to three XML-tagged sections, in the
    order Anthropic recommends (role/instructions → data → final task)::

        <instructions> ... </instructions>           (role + task instructions)
        <workflow_context> ... </workflow_context>   (upstream outputs)
        <task> ... </task>                           (per-run imperative)

    Any section whose source field is empty is omitted. When all three are
    empty the renderer falls back to ``DEFAULT_TASK_MESSAGE``.
    """

    @staticmethod
    async def prepare_message(
        prompt_content: str,
        text_input: str,
        previous_results: Dict[str, Dict],
        include_context: bool = True,
    ) -> str:
        """
        Build the structured prompt for a step node.

        Args:
            prompt_content: Step's ``prompt`` field — rendered first as
                ``<instructions>`` because it typically carries the role,
                persona, and primary task instructions for the step.
            text_input: Step's ``text_input`` field — rendered last as
                ``<task>`` since it is the per-run imperative variable.
            previous_results: Dependency payload from
                ``execution_routing.get_dep_results`` — keyed by the producer
                node id with ``{output, metadata, node_type, ...}`` values.
            include_context: When False, skip the ``<workflow_context>`` block
                even if upstream results are present. Wired to the per-node
                ``use_previous_context`` toggle in Phase 2.

        Returns:
            The final prompt string sent to the LLM.
        """
        sections: List[str] = []

        instructions = (prompt_content or "").strip()
        if instructions:
            sections.append(
                f"<{WorkflowContextTag.INSTRUCTIONS}>\n{instructions}\n"
                f"</{WorkflowContextTag.INSTRUCTIONS}>"
            )

        if include_context:
            rendered = ContextRenderer.render_xml(
                StepContextBuilder.build(previous_results)
            )
            if rendered:
                sections.append(rendered)

        task = (text_input or "").strip()
        if task:
            sections.append(
                f"<{WorkflowContextTag.TASK}>\n{task}\n" f"</{WorkflowContextTag.TASK}>"
            )

        if not sections:
            return ContextDefaults.DEFAULT_TASK_MESSAGE

        return "\n\n".join(sections)


# ============================================================================
# Structured Output Message Preparer
# ============================================================================


class StructuredOutputMessagePreparer:
    """
    Append routing instructions to a message when the selected LLM provider
    does not support native structured output.
    """

    @staticmethod
    def add_route_instruction_to_message(
        base_message: str,
        available_routes: List[str],
        default_route: Optional[str] = None,
    ) -> str:
        """
        Append an instruction listing the allowed route values.

        Args:
            base_message: Message to augment.
            available_routes: Allowed route values.
            default_route: Fallback when the LLM is uncertain. Defaults to
                the first route.

        Returns:
            Message with routing instructions appended
        """
        if default_route is None and available_routes:
            default_route = available_routes[0]

        route_values = ", ".join(f'"{route}"' for route in available_routes)

        instruction = PromptTemplate.STRUCTURED_OUTPUT_INSTRUCTION.format(
            route_values=route_values,
            default_route=default_route or "first option"
        )

        return f"{base_message}\n\n{instruction}"


# ============================================================================
# File Context Preparer
# ============================================================================


class FileContextPreparer:
    """
    Placeholder for file-context preparation. Intended to produce retrieval
    snippets from uploaded files for inclusion in a step's prompt.
    """

    @staticmethod
    async def prepare_file_context(
        uploaded_files,
        similarity_threshold: float = 0.7,
        max_snippets: int = 3,
    ) -> Optional[str]:
        logger.debug("File context preparation not yet implemented")
        return None


# ============================================================================
# Public API
# ============================================================================


__all__ = [
    "StepMessagePreparer",
    "StructuredOutputMessagePreparer",
    "FileContextPreparer",
]
