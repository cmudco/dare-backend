"""
DARE tools the research chat calls over the MCP gateway.

Authorized by the signed thread token in the arguments, not by the MCP
connection's identity: the runtime may share one connection across project
profiles, so only the token reliably says which thread — and so which
user and project — a call acts for.
"""

import json
import re
from dataclasses import dataclass

from research.constants import (
    EVIDENCE_LABELS,
    MAX_SCOUTS_PER_LAUNCH,
    SOURCE_KINDS,
    AgentRole,
    ChatMessageRole,
)
from research.models import ResearchAgentRun, ResearchChatMessage, ResearchKnowledgeItem
from research.services.cost_service import SpendCapReached, check_spend_cap
from research.services.staging_service import merge_learning, stage_candidate
from research.services.thread_service import (
    ThreadTokenInvalid,
    launch_scouts,
    resolve_run_token,
    resolve_thread_token,
    scout_summary,
)

_THREAD_PARAM = {
    "type": "string",
    "description": "This thread's id, exactly as given in your instructions (e.g. t123-0a1b2c3d4e).",
}

TOOL_DEFS = [
    {
        "name": "launch_scouts",
        "description": (
            "Start Scouts for this thread: parallel background searches, each "
            "with an isolated context, whose findings land in the user's "
            "Review Inbox. Returns immediately; DARE reports back in the "
            "thread when each one finishes."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "thread": _THREAD_PARAM,
                "scouts": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": MAX_SCOUTS_PER_LAUNCH,
                    "items": {
                        "type": "object",
                        "properties": {
                            "focus": {
                                "type": "string",
                                "description": "2-6 word label shown to the user.",
                            },
                            "task": {
                                "type": "string",
                                "description": "A self-contained brief for this Scout.",
                            },
                            "depth": {"type": "string", "enum": ["quick", "deep"]},
                        },
                        "required": ["focus", "task"],
                    },
                },
            },
            "required": ["thread", "scouts"],
        },
    },
    {
        "name": "thread_status",
        "description": (
            "Status of this thread's Scouts and what each staged. Call it only "
            "when the user asks how the Scouts are doing — never in a loop "
            "to wait for them: DARE posts to the thread when they finish."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"thread": _THREAD_PARAM},
            "required": ["thread"],
        },
    },
    {
        "name": "search_threads",
        "description": (
            "Search this project's earlier chat threads: what the user asked and "
            "what you answered before (projections, tables, plans). Use it when "
            "the user refers to earlier work, instead of asking them to paste it."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "thread": _THREAD_PARAM,
                "query": {"type": "string"},
            },
            "required": ["thread", "query"],
        },
    },
    {
        "name": "search_knowledge",
        "description": (
            "Search the user's APPROVED knowledge for this project (sources "
            "they promoted from the Review Inbox). Empty query lists the latest."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "thread": _THREAD_PARAM,
                "query": {"type": "string"},
            },
            "required": ["thread"],
        },
    },
]

_RUN_PARAM = {
    "type": "string",
    "description": "Your Scout run id, exactly as given in your instructions (e.g. r123-0a1b2c3d4e).",
}

SCOUT_TOOL_DEFS = [
    {
        "name": "stage_finding",
        "description": (
            "Scouts only: put one checked source into the user's Review "
            "Inbox now. Call once per source, as soon as you have read it."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "run": _RUN_PARAM,
                "title": {"type": "string"},
                "url": {"type": "string"},
                "authors": {"type": "string", "description": "Semicolon-separated."},
                "year": {"type": "integer"},
                "venue": {"type": "string"},
                "doi": {"type": "string"},
                "kind": {
                    "type": "string",
                    "enum": sorted(SOURCE_KINDS),
                    "description": "What sort of source this is.",
                },
                "evidenceLabel": {"type": "string", "enum": sorted(EVIDENCE_LABELS)},
                "confidence": {"type": "number", "description": "0.0 to 1.0"},
                "rationale": {
                    "type": "string",
                    "description": "Why it matters: design, n, the key numbers.",
                },
                "citationContext": {
                    "type": "string",
                    "description": "A short passage from what you read.",
                },
            },
            "required": ["run", "title", "rationale"],
        },
    },
    {
        "name": "report_learning",
        "description": (
            "Scouts only, once before finishing: what the user's remembered "
            "preferences and skills changed in this run."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "run": _RUN_PARAM,
                "applied": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "memory": {"type": "string"},
                            "effect": {"type": "string"},
                        },
                    },
                },
                "setAside": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                            "url": {"type": "string"},
                            "reason": {"type": "string"},
                        },
                    },
                },
            },
            "required": ["run"],
        },
    },
]

TOOL_DEFS = TOOL_DEFS + SCOUT_TOOL_DEFS
TOOL_NAMES = {tool["name"] for tool in TOOL_DEFS}


class ToolInputError(ValueError):
    """The agent sent arguments DARE cannot act on; the message says why."""


@dataclass(frozen=True)
class ScoutRequest:
    focus: str
    task: str
    depth: str


def _scouts(arguments):
    raw = arguments.get("scouts")
    if not isinstance(raw, list) or not raw:
        raise ToolInputError("'scouts' must be a non-empty list.")
    if len(raw) > MAX_SCOUTS_PER_LAUNCH:
        raise ToolInputError(f"Launch at most {MAX_SCOUTS_PER_LAUNCH} Scouts at once.")
    scouts = []
    for entry in raw:
        if not isinstance(entry, dict):
            raise ToolInputError("Each Scout must be an object with focus and task.")
        focus = str(entry.get("focus") or "").strip()[:80]
        task = str(entry.get("task") or "").strip()[:4000]
        if not focus or not task:
            raise ToolInputError("Each Scout needs a non-empty focus and task.")
        depth = "quick" if entry.get("depth") == "quick" else "deep"
        scouts.append(ScoutRequest(focus, task, depth))
    return scouts


def _launch(thread, arguments):
    scouts = _scouts(arguments)
    try:
        check_spend_cap(thread.project)
    except SpendCapReached as exc:
        raise ToolInputError(str(exc)) from exc
    runs = launch_scouts(
        thread, [{"focus": s.focus, "task": s.task, "depth": s.depth} for s in scouts]
    )
    return {
        "launched": [
            {"runId": run.id, "focus": scout.focus} for run, scout in zip(runs, scouts)
        ],
        "note": "Running in the background; DARE will post results to this thread.",
    }


def _status(thread, arguments):
    runs = ResearchAgentRun.active_objects.filter(
        thread=thread, role=AgentRole.SCOUT
    ).order_by("-created_at")[:12]
    return {"scouts": [scout_summary(run) for run in runs]}


_STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "what",
    "how",
    "are",
    "was",
    "this",
    "that",
    "from",
    "my",
    "of",
    "in",
    "to",
    "a",
    "an",
    "on",
    "is",
    "it",
}


def _query_words(query):
    """Meaningful words of a free-text query; a phrase match finds almost nothing."""
    words = re.findall(r"[a-z0-9£$%.-]+", query.lower())
    return [w for w in words if len(w) > 1 and w not in _STOPWORDS][:12]


def _rank(rows, text_of, words, limit):
    """Rows mentioning the most query words first, then the newest."""
    scored = []
    for index, row in enumerate(rows):
        text = text_of(row).lower()
        hits = sum(1 for w in words if w in text)
        if hits:
            scored.append((-hits, index, row))
    return [row for _, _, row in sorted(scored)[:limit]]


def _search(thread, arguments):
    query = str(arguments.get("query") or "").strip()[:200]
    items = list(
        ResearchKnowledgeItem.active_objects.filter(project=thread.project)
        .select_related("source_staging_item")
        .order_by("-approved_at")[:200]
    )
    words = _query_words(query)
    if words:
        items = _rank(
            items,
            lambda i: " ".join(
                [
                    i.content or "",
                    i.rationale or "",
                    getattr(i.source_staging_item, "title", "") or "",
                ]
            ),
            words,
            10,
        )
    results = []
    for item in items[:10]:
        source = item.source_staging_item
        results.append(
            {
                "id": item.id,
                "title": source.title if source else "",
                "authors": source.authors if source else "",
                "year": source.year if source else None,
                "url": source.url if source else "",
                "evidenceLabel": source.evidence_label if source else "",
                "summary": (item.content or item.rationale or "")[:600],
            }
        )
    reply = {"query": query, "results": results}
    if not results:
        reply["note"] = (
            "No approved findings match. Approved knowledge is only what the "
            "user promoted from the Review Inbox — it is separate from the "
            "project memory and standards already in your context, so do not "
            "describe the project as empty."
        )
    return reply


def _search_threads(thread, arguments):
    words = _query_words(str(arguments.get("query") or "")[:200])
    if not words:
        raise ToolInputError("'query' needs a few words to search for.")
    messages = list(
        ResearchChatMessage.active_objects.filter(
            project=thread.project,
            role__in=[ChatMessageRole.USER, ChatMessageRole.ASSISTANT],
        )
        .exclude(session=thread)
        .select_related("session")
        .order_by("-created_at")[:400]
    )
    hits = _rank(messages, lambda m: m.content or "", words, 8)
    return {
        "results": [
            {
                "thread": m.session.title,
                "date": m.created_at.date().isoformat(),
                "who": "user" if m.role == ChatMessageRole.USER else "you",
                "text": " ".join((m.content or "").split())[:1200],
            }
            for m in hits
        ]
        or "No earlier thread mentions that."
    }


_HANDLERS = {
    "launch_scouts": _launch,
    "search_threads": _search_threads,
    "thread_status": _status,
    "search_knowledge": _search,
}


def _stage(run, arguments):
    return stage_candidate(run, arguments).message


def _report(run, arguments):
    learning = merge_learning(
        run, arguments.get("applied") or [], arguments.get("setAside") or []
    )
    return (
        f"Recorded {len(learning['applied'])} applied and "
        f"{len(learning['setAside'])} set aside."
    )


_SCOUT_HANDLERS = {"stage_finding": _stage, "report_learning": _report}


def _call_scout_tool(name, arguments):
    try:
        run = resolve_run_token(arguments.get("run"))
    except ThreadTokenInvalid as exc:
        return (
            f"Not done: {exc} Pass run exactly as written in your instructions "
            "(it looks like r123-0a1b2c3d4e).",
            False,
        )
    return _SCOUT_HANDLERS[name](run, arguments), False


def call_thread_tool(name, arguments):
    """Run a thread tool; returns ``(text, is_error)`` for the MCP result.

    A bad argument is answered as guidance, never as an MCP error: the runtime
    trips a per-server circuit breaker after three errors, which would take
    web search and every other DARE tool down with it for a minute.
    """
    if name in _SCOUT_HANDLERS:
        return _call_scout_tool(name, arguments)
    try:
        thread = resolve_thread_token(arguments.get("thread"))
    except ThreadTokenInvalid:
        return (
            "Not done: that thread value is not valid. Pass thread exactly as "
            "written in your instructions (it looks like t123-0a1b2c3d4e).",
            False,
        )
    try:
        result = _HANDLERS[name](thread, arguments)
    except ToolInputError as exc:
        return f"Not done: {exc}", False
    return json.dumps(result, default=str), False
