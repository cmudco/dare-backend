"""Run a frozen question set against a graph's files, with and without the graph.

Live: this spends real tokens on query analysis and answers. Results are stored
on the graph so the page can show them with the raw answers.
"""

import json
from dataclasses import replace

from asgiref.sync import async_to_sync
from django.core.management.base import BaseCommand
from django.utils import timezone

from core.constants import BackgroundTask
from core.services.background_model_service import BackgroundModelService
from core.services.rag.dtos import RetrievalRequest
from core.services.rag.pipeline import RetrievalPipeline, build_pipeline
from knowledge_graph.models import KnowledgeGraph

TOP_K = 4
WIDE_TOP_K = 10
ANSWER_PROMPT = (
    "Answer the question from the sources only, in at most four sentences. Name "
    "every specific person, place, or title that answers it. If the sources do not "
    "say, reply that they do not say."
)
# Ids stay single words: the API camelizes nested keys, so a snake_case id would no
# longer match the config list it indexes.
CONFIGS = (
    {
        "id": "plain",
        "label": "Search only",
        "description": "Advanced RAG with no graph, 4 chunks.",
    },
    {
        "id": "wide",
        "label": "Search, more chunks",
        "description": "No graph, 10 chunks, the same room the graph can use.",
    },
    {
        "id": "hop",
        "label": "Earlier one-hop",
        "description": "What dev has today: one exact-name hop, 4 chunks.",
    },
    {
        "id": "graph",
        "label": "Graph walk",
        "description": "Aliases and PageRank, 4 chunks plus up to 6 linked.",
    },
)


def _pipeline(config_id: str) -> RetrievalPipeline:
    base = build_pipeline("document")
    if config_id in ("plain", "wide"):
        base.expander = None
        base.walker = None
    elif config_id == "hop":
        base.walker = None
    return base


class Command(BaseCommand):
    help = "Run a graph RAG eval and store the report on the graph."

    def add_arguments(self, parser):
        parser.add_argument("graph_id", type=int)
        parser.add_argument("questions", help="Path to the frozen question JSON.")

    def handle(self, *args, graph_id, questions, **options):
        graph = KnowledgeGraph.objects.get(pk=graph_id)
        file_ids = tuple(graph.files.values_list("id", flat=True))
        with open(questions, encoding="utf-8") as handle:
            items = json.load(handle)
        rows = []
        for item in items:
            results = {}
            for config in CONFIGS:
                results[config["id"]] = self._run(graph, file_ids, item, config["id"])
            rows.append(
                {
                    "id": item["id"],
                    "question": item["question"],
                    "kind": item["kind"],
                    "expected": item["expected"],
                    "results": results,
                }
            )
            self.stdout.write(
                f"{item['id']} "
                + " ".join(
                    f"{config_id}={'Y' if result['answer_correct'] else 'n'}"
                    f"{'+' if result['found'] else '-'}"
                    for config_id, result in results.items()
                )
            )
        summary = {
            config["id"]: {
                "found": sum(1 for r in rows if r["results"][config["id"]]["found"]),
                "answer_correct": sum(
                    1 for r in rows if r["results"][config["id"]]["answer_correct"]
                ),
                "total": len(rows),
            }
            for config in CONFIGS
        }
        graph.eval_report = {
            "generated_at": timezone.now().isoformat(),
            "configs": list(CONFIGS),
            "summary": summary,
            "rows": rows,
        }
        graph.save(update_fields=["eval_report", "updated_at"])
        self.stdout.write(json.dumps(summary))

    def _run(self, graph, file_ids, item, config_id):
        request = RetrievalRequest(
            query=item["question"],
            top_k=WIDE_TOP_K if config_id == "wide" else TOP_K,
            file_ids=file_ids,
            user_id=graph.user_id,
            payer_user_id=graph.user_id,
            trace=True,
        )
        retrieved = _pipeline(config_id).run(replace(request))
        files = sorted({chunk.file_name for chunk in retrieved.chunks})
        answer = async_to_sync(BackgroundModelService().complete_text)(
            user=graph.user,
            messages=[
                {"role": "system", "content": ANSWER_PROMPT},
                {
                    "role": "user",
                    "content": "\n\n".join(retrieved.blocks)
                    + f"\n\nQuestion: {item['question']}",
                },
            ],
            description="Graph RAG eval answer",
            max_tokens=4000,
            task=BackgroundTask.GRAPH_RELATIONS,
        ).value
        lowered = answer.lower()
        return {
            "found": all(
                any(gold.lower() in name.lower() for name in files)
                for gold in item["gold_files"]
            ),
            "answer_correct": all(
                term.lower() in lowered for term in item["must_include"]
            ),
            "files": files,
            "answer": answer,
            "route": retrieved.plan.route if retrieved.plan else "",
        }
