"""
Evidence graph — the project's sources, requests, and relationships as
nodes/edges for an Obsidian-style force-directed view.

Everything here is derived, deterministically, from data DARE already stores:
no LLM judgment, no invented scoring. Edge kinds:

- evidence:  source -> question, typed by the scout's evidence label and
             weighted by its confidence (both reviewed in the inbox).
- request:   source -> the scout request that surfaced it (run provenance).
- mention:   source -> source, when the full text we fetched for one source
             (the GatewayFetch corpus) contains another source's DOI — a real
             citation/reference observed in the document, not inferred.
"""

from mcp.models import GatewayFetch
from research.constants import StagingItemStatus
from research.models import ResearchStagingItem

QUESTION_NODE_ID = "question"


def _source_node(item):
    provenance = item.provenance or {}
    return {
        "id": f"source:{item.id}",
        "kind": "source",
        "label": item.title,
        "stagingItemId": item.id,
        "authors": item.authors,
        "year": item.year,
        "venue": item.venue,
        "doi": item.doi,
        "url": item.url,
        "status": item.status,
        "confidence": item.confidence,
        "evidenceLabel": item.evidence_label,
        "sourceTool": provenance.get("tool", ""),
    }


def _request_nodes_and_edges(items):
    """One hub node per scout request, linking each source to the ask that
    surfaced it — this is what clusters the graph instead of a flat star."""
    nodes, edges, seen = [], [], {}
    for item in items:
        provenance = item.provenance or {}
        run_id = provenance.get("runId")
        if not run_id:
            continue
        hub_id = f"request:{run_id}"
        if hub_id not in seen:
            seen[hub_id] = True
            query = str(provenance.get("query") or "scout request").strip()
            nodes.append(
                {
                    "id": hub_id,
                    "kind": "request",
                    "label": query[:120],
                    "runId": run_id,
                }
            )
            edges.append(
                {"source": hub_id, "target": QUESTION_NODE_ID, "kind": "request"}
            )
        edges.append(
            {"source": f"source:{item.id}", "target": hub_id, "kind": "request"}
        )
    return nodes, edges


def _fetched_texts(user, items):
    """Map staging-item id -> the full fetched text of that source from the
    gateway corpus, matched by URL or DOI."""
    fetches = GatewayFetch.active_objects.filter(user=user, tool="fetch_page")
    by_url = {f.url: f.content for f in fetches if f.url}
    by_doi = {f.doi.lower(): f.content for f in fetches if f.doi}
    texts = {}
    for item in items:
        content = by_url.get(item.url) or (
            by_doi.get(item.doi.lower()) if item.doi else None
        )
        if content:
            texts[item.id] = content.lower()
    return texts


def _mention_edges(user, items):
    """source -> source edges where one source's fetched full text contains
    another's DOI. Observed in the document itself — the corpus payoff."""
    texts = _fetched_texts(user, items)
    with_doi = [i for i in items if i.doi]
    edges = []
    for item_id, text in texts.items():
        for other in with_doi:
            if other.id == item_id:
                continue
            if other.doi.lower() in text:
                edges.append(
                    {
                        "source": f"source:{item_id}",
                        "target": f"source:{other.id}",
                        "kind": "mention",
                    }
                )
    return edges


def build_evidence_graph(project):
    """The project's evidence graph as {nodes, edges} (camelCase, FE-ready)."""
    items = list(
        ResearchStagingItem.active_objects.filter(project=project).exclude(
            status=StagingItemStatus.REJECTED
        )
    )
    nodes = [{"id": QUESTION_NODE_ID, "kind": "question", "label": project.question}]
    edges = []
    for item in items:
        nodes.append(_source_node(item))
        edges.append(
            {
                "source": f"source:{item.id}",
                "target": QUESTION_NODE_ID,
                "kind": "evidence",
                "label": item.evidence_label,
                "weight": item.confidence,
            }
        )
    request_nodes, request_edges = _request_nodes_and_edges(items)
    nodes.extend(request_nodes)
    edges.extend(request_edges)
    edges.extend(_mention_edges(project.user, items))
    return {"nodes": nodes, "edges": edges}
