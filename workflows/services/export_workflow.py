
import logging

from workflows.handlers.utils.constants import (
    EXCLUDED_NODE_TYPES,
    RUNTIME_NODE_TYPE,
    NodeType,
)
from workflows.models.nodes import build_prefetched_node_file_relations

logger = logging.getLogger(__name__)


class WorkflowExportService:
    """Serializes a Workflow into a self-contained, execution-ready JSON dict."""

    def export(self, workflow) -> dict:
        """Build and return the export dict for the given workflow.

        Args:
            workflow: The Workflow instance to export.

        Returns:
            dict: Self-contained JSON-serializable workflow representation.
        """
        all_nodes = list(workflow.nodes.all())
        excluded_ids = {n.node_id for n in all_nodes if n.node_type in EXCLUDED_NODE_TYPES}

        nodes = sorted(
            (n for n in all_nodes if n.node_type not in EXCLUDED_NODE_TYPES),
            key=lambda n: n.node_id,
        )
        edges = sorted(workflow.edges.all(), key=lambda e: e.edge_id)
        relations = build_prefetched_node_file_relations(nodes)

        return {
            "workflow_id": workflow.id,
            "title": workflow.title,
            "description": workflow.description,
            "mode": workflow.mode,
            "entry_node": self._resolve_entry_node(workflow, nodes),
            "nodes": [self._export_node(node, relations) for node in nodes],
            "edges": [
                self._export_edge(e)
                for e in edges
                if e.source not in excluded_ids and e.target not in excluded_ids
            ],
        }

    def _export_node(self, node, relations) -> dict:
        """Serialize a single node into its export representation.

        Args:
            node: WorkflowNode instance.
            relations: Prefetched file relations for all nodes.

        Returns:
            dict: Node dict with id, type, and data fields.
        """
        base = {"id": node.node_id, "type": RUNTIME_NODE_TYPE.get(node.node_type, node.node_type)}
        data_obj = node.data_object

        if data_obj is None:
            base["data"] = {}
            return base

        if node.node_type == NodeType.STEP:
            data = {
                "label": data_obj.label,
                "prompt": self._prompt(data_obj.prompt),
                "llm": self._llm(data_obj.llm),
                "generation": {
                    "max_tokens": data_obj.max_tokens,
                    "temperature": data_obj.temperature,
                },
                "text_input": data_obj.text_input,
                "use_previous_context": data_obj.use_previous_context,
                "enable_web_search": data_obj.enable_web_search,
                "use_previous_step_files": data_obj.use_previous_step_files,
                "use_previous_step_embeddings": data_obj.use_previous_step_embeddings,
            }
            if relations.get_step_content_files(data_obj.id):
                data["needs_content_files"] = True
            if relations.get_step_embedding_files(data_obj.id):
                data["needs_embedding_files"] = True
                data["retrieval"] = {
                    "max_context_snippets": data_obj.max_context_snippets,
                    "similarity_threshold": data_obj.document_similarity_threshold,
                }
            base["data"] = data

        elif node.node_type == NodeType.STRUCTURED_OUTPUT:
            base["data"] = {
                "label": data_obj.label,
                "prompt": self._prompt(data_obj.prompt),
                "llm": self._llm(data_obj.llm),
                "routes": data_obj.get_routes(),
                "require_human_validation": data_obj.require_human_validation,
                "text_input": data_obj.text_input,
            }

        elif node.node_type == NodeType.FILE:
            data = {
                "label": data_obj.label,
                "retrieval": {
                    "mode": data_obj.retrieval_mode,
                    "similarity_threshold": data_obj.similarity_threshold,
                    "max_results": data_obj.max_results,
                    "query_source": data_obj.query_source,
                    "include_metadata": data_obj.include_metadata,
                },
                "text_input": data_obj.text_input,
            }
            if relations.get_file_node_files(data_obj.id):
                data["needs_files"] = True
            base["data"] = data

        elif node.node_type == NodeType.START:
            base["data"] = {
                "title": data_obj.title,
                "description": data_obj.description,
                "mode": data_obj.mode,
            }

        elif node.node_type == NodeType.CHAT_OUTPUT:
            base["data"] = {"label": data_obj.label}

        else:
            base["data"] = {}

        return base

    def _export_edge(self, edge) -> dict:
        """Serialize a single edge into its export representation."""
        return {
            "id": edge.edge_id,
            "source": edge.source,
            "target": edge.target,
            "source_handle": edge.source_handle or None,
            "target_handle": edge.target_handle or None,
        }

    def _resolve_entry_node(self, workflow, nodes):
        """Return the node_id of the workflow's start node."""
        if workflow.root_start_node_id and workflow.root_start_node:
            return workflow.root_start_node.node_id
        for node in nodes:
            if node.node_type == NodeType.START:
                return node.node_id
        return nodes[0].node_id if nodes else None

    def _llm(self, llm) -> dict | None:
        """Serialize an LLM instance, or return None if not set."""
        if not llm:
            return None
        return {
            "name": llm.name,
            "provider": llm.provider,
            "identifier": llm.identifier,
            "base_url": llm.base_url or None,
            "supports_temperature": llm.supports_temperature,
        }

    def _prompt(self, prompt) -> dict | None:
        """Serialize a Prompt instance, or return None if not set."""
        if not prompt:
            return None
        return {
            "title": prompt.title,
            "content": prompt.content,
        }
