"""
Workflow cloning service for duplicating workflows with all nodes and edges.

Extracted from WorkflowViewSet.clone_workflow for better maintainability
and separation of concerns.
"""

from django.contrib.contenttypes.models import ContentType

from workflows.models import (
    Workflow, WorkflowNode, WorkflowEdge,
    StartNodeData, StepNodeData, ChatOutputNodeData, ConditionalNodeData, StructuredOutputNodeData
)


class WorkflowCloningService:
    """
    Service for cloning workflows with all associated nodes and edges.

    Handles the complex logic of duplicating workflow structures including
    type-safe node data objects and maintaining relationships between components.
    """

    def clone_workflow(self, original: Workflow) -> Workflow:
        """
        Clone a complete workflow with all nodes and edges.

        Creates a new workflow as a copy of the original, including all nodes
        with their typed data objects and all connecting edges. The cloned
        workflow maintains the same structure but is independent of the original.

        Args:
            original: The workflow to clone

        Returns:
            Workflow: The newly created cloned workflow
        """
        # Create the base cloned workflow
        cloned = self._create_cloned_workflow(original)

        # Clone all nodes with their data
        self._clone_nodes(original, cloned)

        # Clone all edges
        self._clone_edges(original, cloned)

        return cloned

    def _create_cloned_workflow(self, original: Workflow) -> Workflow:
        """
        Create the base cloned workflow with copied metadata.

        Args:
            original: Original workflow to copy from

        Returns:
            Workflow: New workflow instance
        """
        return Workflow.objects.create(
            user=original.user,
            version=1,
            parent=original,
            viewport_x=original.viewport_x,
            viewport_y=original.viewport_y,
            viewport_zoom=original.viewport_zoom
        )

    def _clone_nodes(self, original: Workflow, cloned: Workflow) -> None:
        """
        Clone all nodes and their associated data objects.

        Args:
            original: Original workflow with nodes to clone
            cloned: Target workflow to add cloned nodes to
        """
        for node in original.nodes.all():
            if node.data_object:
                cloned_data = self._clone_node_data(node.data_object)
                if cloned_data:
                    self._create_cloned_node(node, cloned, cloned_data)

    def _clone_node_data(self, data_object):
        """
        Clone the typed data object based on its type.

        Args:
            data_object: The node data object to clone

        Returns:
            Cloned data object instance
        """
        if isinstance(data_object, StartNodeData):
            return StartNodeData.objects.create(
                title=f"COPY OF - {data_object.title}",
                description=data_object.description,
                mode=data_object.mode
            )
        elif isinstance(data_object, StepNodeData):
            cloned_data = StepNodeData.objects.create(
                prompt=data_object.prompt,
                llm=data_object.llm,
                step_number=data_object.step_number,
                max_tokens=data_object.max_tokens,
                temperature=data_object.temperature,
                max_context_snippets=data_object.max_context_snippets,
                document_similarity_threshold=data_object.document_similarity_threshold,
                use_previous_step_files=data_object.use_previous_step_files,
                use_previous_step_embeddings=data_object.use_previous_step_embeddings,
                text_input=data_object.text_input,
                use_structured_output_node=data_object.use_structured_output_node
            )
            # Clone many-to-many relationships
            cloned_data.content_files.set(data_object.content_files.all())
            cloned_data.embedding_files.set(data_object.embedding_files.all())
            return cloned_data
        elif isinstance(data_object, ChatOutputNodeData):
            return ChatOutputNodeData.objects.create(
                step_number=data_object.step_number,
                status='',
                response='',
                error=''
            )
        elif isinstance(data_object, ConditionalNodeData):
            return ConditionalNodeData.objects.create(
                prompt=data_object.prompt,
                llm=data_object.llm,
                routes=data_object.routes,
                require_human_validation=data_object.require_human_validation,
                step_number=data_object.step_number
            )
        elif isinstance(data_object, StructuredOutputNodeData):
            return StructuredOutputNodeData.objects.create(
                prompt=data_object.prompt,
                routes=data_object.routes,
                step_number=data_object.step_number,
                require_human_validation=data_object.require_human_validation,
                llm=data_object.llm
            )
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