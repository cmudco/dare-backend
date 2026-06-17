"""Workflow export service.

Builds a self-contained, deterministic execution graph (JSON) for a workflow so
it can be executed by an external runtime (e.g. SyftBox) without access to Dare's
database.

Design notes:
- File content and vector retrieval are delegated to Dare APIs at runtime, so we
  bake only references (file_ids + names) here, never raw file bytes or vectors.
- UI-only metadata (positions, sizes, styling, selection state) is stripped.
- Output is deterministic: nodes/edges are sorted by id and contain no timestamps
  or volatile fields, so the same workflow always yields the same JSON.
"""
import logging

from core.config.processing import DEFAULT_SIMILARITY_THRESHOLD, DEFAULT_TOP_K
from workflows.handlers.utils.constants import NodeType
from workflows.models.nodes import build_prefetched_node_file_relations

logger = logging.getLogger(__name__)

EXPORT_SCHEMA_VERSION = "v1"

RUNTIME_NODE_TYPE = {
    NodeType.START: "start",
    NodeType.STEP: "step",
    NodeType.STRUCTURED_OUTPUT: "router",
    NodeType.FILE: "file",
    NodeType.CHAT_OUTPUT: "output",
}

EXCLUDED_NODE_TYPES = {NodeType.NOTES}


class WorkflowExportService:
    """Serialize a Workflow into an execution-ready, self-contained JSON graph."""

    def export(self, workflow) -> dict:
        all_nodes = list(workflow.nodes.all())
        excluded_ids = {
            n.node_id for n in all_nodes if n.node_type in EXCLUDED_NODE_TYPES
        }

        nodes = sorted(
            (n for n in all_nodes if n.node_type not in EXCLUDED_NODE_TYPES),
            key=lambda n: n.node_id,
        )
        edges = sorted(workflow.edges.all(), key=lambda e: e.edge_id)

        relations = build_prefetched_node_file_relations(nodes)

        exported_nodes = []
        llm_config = {}
        files_index = {}

        for node in nodes:
            exported, used_llm, used_files = self._export_node(node, relations)
            exported_nodes.append(exported)
            if used_llm:
                llm_config[used_llm["identifier"]] = used_llm
            for ref in used_files:
                files_index[ref["file_id"]] = ref

        exported_edges = [
            self._export_edge(e)
            for e in edges
            if e.source not in excluded_ids and e.target not in excluded_ids
        ]

        return {
            "schema_version": EXPORT_SCHEMA_VERSION,
            "workflow_id": workflow.id,
            "title": workflow.title,
            "description": workflow.description,
            "mode": workflow.mode,
            "entry_node": self._resolve_entry_node(workflow, nodes),
            "nodes": exported_nodes,
            "edges": exported_edges,
            "llm_config": llm_config,
            "files": sorted(files_index.values(), key=lambda f: f["file_id"]),
            "rag_config": {
                "default_top_k": DEFAULT_TOP_K,
                "default_similarity_threshold": DEFAULT_SIMILARITY_THRESHOLD,
            },
        }

    # ------------------------------------------------------------------
    # Node / edge serialization
    # ------------------------------------------------------------------

    def _export_node(self, node, relations):
        """Return (node_dict, llm_dict_or_None, [file_ref, ...]) for a node."""
        runtime_type = RUNTIME_NODE_TYPE.get(node.node_type, node.node_type)
        base = {"id": node.node_id, "type": runtime_type}
        data_obj = node.data_object
        used_llm = None
        used_files = []

        if data_obj is None:
            base["data"] = {}
            return base, used_llm, used_files

        if node.node_type == NodeType.STEP:
            content_files = self._files(relations.get_step_content_files(data_obj.id))
            embedding_files = self._files(
                relations.get_step_embedding_files(data_obj.id)
            )
            tags = self._tags(relations.get_step_tags(data_obj.id))
            used_llm = self._llm(data_obj.llm)
            used_files = content_files + embedding_files
            base["data"] = {
                "label": data_obj.label,
                "prompt": self._prompt(data_obj.prompt),
                "agent_id": data_obj.agent_id,
                "llm": used_llm,
                "generation": {
                    "max_tokens": data_obj.max_tokens,
                    "temperature": data_obj.temperature,
                },
                "text_input": data_obj.text_input,
                "use_previous_context": data_obj.use_previous_context,
                "enable_web_search": data_obj.enable_web_search,
                "rag": {
                    "content_files": content_files,
                    "embedding_files": embedding_files,
                    "tags": tags,
                    "max_context_snippets": data_obj.max_context_snippets,
                    "document_similarity_threshold": (
                        data_obj.document_similarity_threshold
                    ),
                    "use_previous_step_files": data_obj.use_previous_step_files,
                    "use_previous_step_embeddings": (
                        data_obj.use_previous_step_embeddings
                    ),
                },
            }

        elif node.node_type == NodeType.STRUCTURED_OUTPUT:
            used_llm = self._llm(data_obj.llm)
            base["data"] = {
                "label": data_obj.label,
                "prompt": self._prompt(data_obj.prompt),
                "llm": used_llm,
                "routes": data_obj.get_routes(),
                "require_human_validation": data_obj.require_human_validation,
                "text_input": data_obj.text_input,
            }

        elif node.node_type == NodeType.FILE:
            used_files = self._files(relations.get_file_node_files(data_obj.id))
            base["data"] = {
                "label": data_obj.label,
                "files": used_files,
                "retrieval_mode": data_obj.retrieval_mode,
                "similarity_threshold": data_obj.similarity_threshold,
                "max_results": data_obj.max_results,
                "query_source": data_obj.query_source,
                "text_input": data_obj.text_input,
                "include_metadata": data_obj.include_metadata,
            }

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

        return base, used_llm, used_files

    def _export_edge(self, edge) -> dict:
        return {
            "id": edge.edge_id,
            "source": edge.source,
            "target": edge.target,
            "source_handle": edge.source_handle or None,
            "target_handle": edge.target_handle or None,
        }

    # ------------------------------------------------------------------
    # Reference resolvers
    # ------------------------------------------------------------------

    def _resolve_entry_node(self, workflow, nodes):
        if workflow.root_start_node_id and workflow.root_start_node:
            return workflow.root_start_node.node_id
        for node in nodes:
            if node.node_type == NodeType.START:
                return node.node_id
        return nodes[0].node_id if nodes else None

    def _llm(self, llm) -> dict | None:
        if not llm:
            return None
        return {
            "id": llm.id,
            "name": llm.name,
            "provider": llm.provider,
            "identifier": llm.identifier,
            "base_url": llm.base_url or None,
            "supports_temperature": llm.supports_temperature,
        }

    def _prompt(self, prompt) -> dict | None:
        if not prompt:
            return None
        return {
            "id": prompt.id,
            "title": prompt.title,
            "content": prompt.content,
        }

    def _files(self, refs) -> list:
        return [{"file_id": r.file_id, "name": r.file_name} for r in refs]

    def _tags(self, refs) -> list:
        # NodeFileReference reuses file_id/file_name for tag id/label.
        return [{"tag_id": r.file_id, "name": r.file_name} for r in refs]
