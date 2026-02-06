"""
Workflow Graph Service — node/edge upsert business logic.

Extracted from WorkflowViewSet to keep views thin and business logic testable.

NOTE: Serializers are imported inside methods to avoid circular imports.
Import chain: serializers → handlers → services → (this file) → serializers.
"""
import logging
from typing import List, Dict, Any

from workflows.models import Workflow

logger = logging.getLogger(__name__)


class WorkflowGraphService:
    """Handles create/update/delete of nodes and edges for a workflow."""

    @staticmethod
    def upsert_nodes(workflow: Workflow, nodes_payload: List[Dict[str, Any]]) -> None:
        """
        Upsert nodes for a workflow: create new, update existing, delete removed.

        Args:
            workflow: The parent workflow instance.
            nodes_payload: List of node dicts from the request body.
        """
        from workflows.api.serializers import WorkflowNodeSerializer

        existing_nodes = {n.node_id: n for n in workflow.nodes.all()}
        seen_ids: set[str] = set()

        for n in nodes_payload:
            node_id = n.get('node_id') or n.get('id')
            if not node_id:
                continue

            seen_ids.add(node_id)
            existing = existing_nodes.get(node_id)
            payload = {**n, 'workflow': workflow.id}

            if existing:
                ser = WorkflowNodeSerializer(existing, data=payload, partial=True)
                ser.is_valid(raise_exception=True)
                ser.save()
            else:
                ser = WorkflowNodeSerializer(data=payload)
                ser.is_valid(raise_exception=True)
                ser.save()

        # Delete nodes that are not in payload
        nodes_to_delete = workflow.nodes.exclude(node_id__in=seen_ids)
        if nodes_to_delete.exists():
            for n in nodes_to_delete:
                n.delete()

    @staticmethod
    def upsert_edges(workflow: Workflow, edges_payload: List[Dict[str, Any]]) -> None:
        """
        Upsert edges for a workflow: create new, update existing, delete removed.

        Args:
            workflow: The parent workflow instance.
            edges_payload: List of edge dicts from the request body.
        """
        from workflows.api.serializers import WorkflowEdgeSerializer

        existing_edges = {e.edge_id: e for e in workflow.edges.all()}
        seen_eids: set[str] = set()

        for e in edges_payload:
            edge_id = e.get('edge_id') or e.get('id')
            if not edge_id:
                continue

            seen_eids.add(edge_id)
            existing_e = existing_edges.get(edge_id)
            payload = {**e, 'workflow': workflow.id}

            if existing_e:
                ser = WorkflowEdgeSerializer(existing_e, data=payload, partial=True)
                ser.is_valid(raise_exception=True)
                ser.save()
            else:
                ser = WorkflowEdgeSerializer(data=payload)
                ser.is_valid(raise_exception=True)
                ser.save()

        # Delete edges not in payload
        edges_to_delete = workflow.edges.exclude(edge_id__in=seen_eids)
        if edges_to_delete.exists():
            for e in edges_to_delete:
                e.delete()
