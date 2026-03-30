"""
Workflow cloning service for duplicating workflows with all nodes and edges.

Extracted from WorkflowViewSet.clone_workflow for better maintainability
and separation of concerns. Supports both same-user cloning and cross-user forking.

Note: Files are NOT copied during cross-user forks - users upload their own files.
"""

from django.contrib.contenttypes.models import ContentType

from prompts.models import Prompt
from workflows.models import (
    Workflow, WorkflowNode, WorkflowEdge,
    StartNodeData, StepNodeData, ChatOutputNodeData, StructuredOutputNodeData,
    NotesNodeData, FileNodeData,
)


class WorkflowCloningService:
    """
    Service for cloning workflows with all associated nodes and edges.

    Handles the complex logic of duplicating workflow structures including
    type-safe node data objects and maintaining relationships between components.
    """

    def clone_workflow(
        self,
        original: Workflow,
        target_user=None,
    ) -> Workflow:
        """Clone a complete workflow with all nodes and edges.

        Creates a new workflow as a copy of the original, including all nodes
        with their typed data objects and all connecting edges.

        For cross-user forks: prompts are cloned, but files are NOT copied.
        Users must upload their own files when running forked workflows.

        Args:
            original: The workflow to clone
            target_user: User who will own the clone. Defaults to original owner.

        Returns:
            Workflow: The newly created cloned workflow
        """
        user = target_user or original.user
        is_cross_user = user != original.user

        # Create the base cloned workflow
        cloned = self._create_cloned_workflow(original, user=user)

        # Clone all nodes with their data
        self._clone_nodes(original, cloned, target_user=user, is_cross_user=is_cross_user)

        # Clone all edges
        self._clone_edges(original, cloned)

        # Resolve root start node for the cloned workflow
        cloned.resolve_root_start_node()

        return cloned

    def _create_cloned_workflow(
        self,
        original: Workflow,
        user=None,
    ) -> Workflow:
        """Create the base cloned workflow with copied metadata.

        Args:
            original: Original workflow to copy from
            user: Owner of the new workflow

        Returns:
            Workflow: New workflow instance
        """
        return Workflow.objects.create(
            user=user or original.user,
            version=1,
            parent=original,
            viewport_x=original.viewport_x,
            viewport_y=original.viewport_y,
            viewport_zoom=original.viewport_zoom,
            manual_mode_enabled=original.manual_mode_enabled,
            output_display_mode=original.output_display_mode,
        )

    def _clone_nodes(
        self,
        original: Workflow,
        cloned: Workflow,
        target_user=None,
        is_cross_user: bool = False,
    ) -> None:
        """Clone all nodes and their associated data objects.

        Args:
            original: Original workflow with nodes to clone
            cloned: Target workflow to add cloned nodes to
            target_user: User who will own the cloned workflow
            is_cross_user: If True, prefix title with 'FORK OF' and clone prompts
        """
        for node in original.nodes.all():
            if node.data_object:
                cloned_data = self._clone_node_data(
                    node.data_object,
                    target_user=target_user,
                    is_cross_user=is_cross_user
                )
                if cloned_data:
                    self._create_cloned_node(node, cloned, cloned_data)

    def _clone_node_data(self, data_object, target_user=None, is_cross_user: bool = False):
        """Clone the typed data object based on its type.

        Args:
            data_object: The node data object to clone
            target_user: User who will own the cloned data (for prompt cloning)
            is_cross_user: If True, prefix start node title with 'FORK OF' and clone prompts

        Returns:
            Cloned data object instance
        """
        if isinstance(data_object, StartNodeData):
            prefix = "FORK OF" if is_cross_user else "COPY OF"
            title = data_object.title or "Untitled Workflow"
            return StartNodeData.objects.create(
                title=f"{prefix} - {title}",
                description=data_object.description,
                mode=data_object.mode
            )
        elif isinstance(data_object, StepNodeData):
            # For cross-user forks, clone the prompt instead of referencing original
            cloned_prompt = None
            if data_object.prompt:
                if is_cross_user:
                    if not target_user:
                        raise ValueError("target_user is required for cross-user workflow cloning")
                    # Create a copy of the prompt owned by the target user
                    prompt_title = data_object.prompt.title or "Untitled Prompt"
                    cloned_prompt = Prompt.active_objects.create(
                        user=target_user,
                        title=f"FORK OF - {prompt_title}",
                        content=data_object.prompt.content,
                        version=1,
                        parent=None  # No parent link for forked prompts
                    )
                else:
                    # Same-user clone: reference the original prompt
                    cloned_prompt = data_object.prompt

            cloned_data = StepNodeData.objects.create(
                label=data_object.label,
                agent=data_object.agent,
                prompt=cloned_prompt,
                llm=data_object.llm,
                max_tokens=data_object.max_tokens,
                temperature=data_object.temperature,
                max_context_snippets=data_object.max_context_snippets,
                document_similarity_threshold=data_object.document_similarity_threshold,
                use_previous_step_files=data_object.use_previous_step_files,
                use_previous_step_embeddings=data_object.use_previous_step_embeddings,
                text_input=data_object.text_input,
                enable_web_search=data_object.enable_web_search
            )
            # Clone file references only for same-user clones;
            # cross-user forks start with empty files so users upload their own
            if not is_cross_user:
                cloned_data.content_files.set(data_object.content_files.all())
                cloned_data.embedding_files.set(data_object.embedding_files.all())
            return cloned_data
        elif isinstance(data_object, ChatOutputNodeData):
            return ChatOutputNodeData.objects.create(
                label=data_object.label,
                status='',
                response='',
                error=''
            )
        elif isinstance(data_object, StructuredOutputNodeData):
            # For cross-user forks, clone the prompt instead of referencing original
            cloned_prompt = None
            if data_object.prompt:
                if is_cross_user:
                    if not target_user:
                        raise ValueError("target_user is required for cross-user workflow cloning")
                    prompt_title = data_object.prompt.title or "Untitled Prompt"
                    cloned_prompt = Prompt.active_objects.create(
                        user=target_user,
                        title=f"FORK OF - {prompt_title}",
                        content=data_object.prompt.content,
                        version=1,
                        parent=None
                    )
                else:
                    cloned_prompt = data_object.prompt

            return StructuredOutputNodeData.objects.create(
                label=data_object.label,
                prompt=cloned_prompt,
                routes=data_object.routes,
                require_human_validation=data_object.require_human_validation,
                llm=data_object.llm,
                text_input=data_object.text_input
            )
        elif isinstance(data_object, NotesNodeData):
            return NotesNodeData.objects.create(
                content=data_object.content
            )
        elif isinstance(data_object, FileNodeData):
            cloned_data = FileNodeData.objects.create(
                label=data_object.label,
                retrieval_mode=data_object.retrieval_mode,
                similarity_threshold=data_object.similarity_threshold,
                max_results=data_object.max_results,
                query_source=data_object.query_source,
                text_input=data_object.text_input,
                include_metadata=data_object.include_metadata,
            )
            if not is_cross_user:
                cloned_data.files.set(data_object.files.all())
            return cloned_data
        return None

    def _create_cloned_node(self, original_node: WorkflowNode, cloned_workflow: Workflow, cloned_data) -> None:
        """
        Create a cloned WorkflowNode with the cloned data object.

        Args:
            original_node: Original node to copy properties from
            cloned_workflow: Target workflow for the new node
            cloned_data: Cloned data object to associate with the node
        """
        WorkflowNode.objects.create(
            workflow=cloned_workflow,
            node_id=original_node.node_id,
            node_type=original_node.node_type,
            position_x=original_node.position_x,
            position_y=original_node.position_y,
            width=original_node.width,
            height=original_node.height,
            selected=False,  # Reset selection state
            dragging=False,  # Reset dragging state
            draggable=original_node.draggable,
            selectable=original_node.selectable,
            connectable=original_node.connectable,
            deletable=original_node.deletable,
            hidden=original_node.hidden,
            source_position=original_node.source_position,
            target_position=original_node.target_position,
            parent_id=original_node.parent_id,
            z_index=original_node.z_index,
            drag_handle=original_node.drag_handle,
            style=original_node.style,
            class_name=original_node.class_name,
            data_content_type=ContentType.objects.get_for_model(cloned_data),
            data_object_id=cloned_data.id
        )

    def _clone_edges(self, original: Workflow, cloned: Workflow) -> None:
        """
        Clone all edges from original workflow to cloned workflow.

        Args:
            original: Original workflow with edges to clone
            cloned: Target workflow to add cloned edges to
        """
        for edge in original.edges.all():
            WorkflowEdge.objects.create(
                workflow=cloned,
                edge_id=edge.edge_id,
                edge_type=edge.edge_type,
                source=edge.source,
                target=edge.target,
                source_handle=edge.source_handle,
                target_handle=edge.target_handle,
                data=edge.data,
                selected=False,  # Reset selection state
                animated=edge.animated,
                hidden=edge.hidden,
                deletable=edge.deletable,
                selectable=edge.selectable,
                z_index=edge.z_index,
                label=edge.label,
                style=edge.style,
                class_name=edge.class_name,
                marker_start=edge.marker_start,
                marker_end=edge.marker_end,
                path_options=edge.path_options
            )
