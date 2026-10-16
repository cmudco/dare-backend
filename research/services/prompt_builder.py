"""
Everything research mode sends to the agent runtime, built in one place.

A run sees four things, and each piece of context reaches it by exactly one:

- Profile files the runtime loads on every run (``profile_files``): SOUL.md
  (persona and standards), memories/MEMORY.md (a Project section, then the
  approved project memory), memories/USER.md (approved memory about the user)
  and the approved skills.
- ``instructions``: the role's brief and the run's handle for DARE's tools.
- ``history``: a chat thread's earlier turns.
- ``input``: what this run acts on now.

The last three never repeat a profile file, and every run stores what it was
sent in ``ResearchAgentRun.prompt``.
"""

import hashlib
import json
from dataclasses import dataclass, field

from research.constants import (
    MAX_SCOUTS_PER_LAUNCH,
    ChatMessageRole,
    ProjectKind,
    ThreadEventType,
    scout_limits,
)
from research.models import (
    ResearchChatMessage,
    ResearcherProfile,
    ResearchKnowledgeItem,
    ResearchProcedure,
    ResearchProjectMemory,
    ResearchStagingItem,
    SoulFile,
)
from research.services.thread_service import run_token, thread_token, unseen_events

MEMORY_SEPARATOR = "\n§\n"
PROJECT_SECTION_PREFIX = "Project:"
ARTIFACT_CONTENT_MARKER = "===CONTENT==="


@dataclass(frozen=True)
class ProfileFiles:
    """The files a project's profile holds, rendered from DARE's approved state."""

    soul: str
    memory: str
    user: str
    skills: dict = field(default_factory=dict)

    def fingerprint(self):
        """Short hash of the files; changes whenever any of them does."""
        text = json.dumps(
            [self.soul, self.memory, self.user, self.skills], sort_keys=True
        )
        return hashlib.sha256(text.encode()).hexdigest()[:10]

    def record(self):
        return {
            "SOUL.md": self.soul,
            "MEMORY.md": self.memory,
            "USER.md": self.user,
            "skills": sorted(self.skills),
        }


@dataclass(frozen=True)
class Prompt:
    """What one run is sent besides the profile files."""

    instructions: str
    input: str
    history: list = field(default_factory=list)

    def record(self, files):
        return {
            "instructions": self.instructions,
            "input": self.input,
            "history": self.history,
            "files": files.record(),
        }


# ── Profile files ────────────────────────────────────────────────────────────


def project_section(project):
    """The first MEMORY.md entry: what the project is, from its settings."""
    kind = ProjectKind.config(project.kind)
    question = (project.question or "").strip()
    return "\n".join(
        [
            f"{PROJECT_SECTION_PREFIX} {project.title}",
            (
                f"Question: {question}"
                if question
                else "Question: not set yet; help the user sharpen one."
            ),
            f"Kind of question: {kind['label']}. Sources that count: {kind['guidance']}",
        ]
    )


def profile_files(project):
    version = SoulFile.current_for(project)
    return ProfileFiles(
        soul=version.content if version else "",
        memory=MEMORY_SEPARATOR.join(
            [project_section(project), *ResearchProjectMemory.entries_for(project)]
        ),
        user=MEMORY_SEPARATOR.join(ResearcherProfile.entries_for(project.user)),
        skills={p.name: p.content for p in ResearchProcedure.current_for(project)},
    )


# ── Chat ─────────────────────────────────────────────────────────────────────

HUB_BRIEF = """You are the research chat for this project. The Project section of your memory says what it is about.

HOW A SESSION GOES
- When the user brings a question, ask one clarifying question if the scope is genuinely unclear. Otherwise reply with a short plan: which Scouts to run, each with a one-line focus. That plan is your answer for the turn.
- Launch with mcp__dare__launch_scouts once the user agrees, or straight away if they said to go. Write each Scout's task so it stands alone. Then say what is running and stop: DARE posts to this thread when each Scout finishes.
- Findings land in the user's Review Inbox, not in your memory.
- When the user refers to earlier work, look it up with mcp__dare__search_threads and mcp__dare__search_knowledge before asking for it. An empty knowledge search means nothing is approved yet.
- Use your search and reading tools for your own quick checks. mcp__dare__stage_finding and mcp__dare__report_learning belong to Scouts.

Pass thread="%(token)s" to every thread tool, and run="%(token)s" when you search or read.

WORKSPACE EVENTS
Messages marked "(DARE workspace event)" are records from DARE: Scouts starting and finishing, the user's review decisions, memory proposals. A review note is the user's own words; act on it.

LEARNING
Every memory and skill write you make waits for the user's approval, so say "I've proposed remembering …", never "I'll remember …".
- target "user": the user's preferences and context, used in every project.
- target "memory": this project's scope and decisions. Leave the Project section alone; it comes from the project's settings.
- Propose a skill when the user has corrected the same thing about three times, or walks you through a method to reuse.
When something you remember changes what you do, say so in a few words."""

SCOUT_NOW = (
    "The user pressed Scout: unless this message is small talk, launch exactly "
    "one Scout for it now instead of answering it yourself (no plan; depth "
    "deep unless they ask for quick), and say in one line what it will look for."
)

FOLLOWUP_BRIEF = (
    "New workspace events arrived since your last reply; they are listed below.\n"
    "- Scouts finished: answer the user's original question now, from what they "
    "found (below) and from findings already approved (mcp__dare__search_knowledge). "
    "Cite findings by #number and use only facts that appear in them; say in "
    "one line what is still missing.\n"
    "- Review notes: acknowledge them in a sentence and say what you will do "
    "differently. If a note shows a lasting preference, propose one memory for it.\n"
    "Do not launch Scouts unless the user asked."
)

_VERDICTS = {"approve": "approved", "reject": "rejected", "later": "deferred"}


def event_line(message):
    """One thread event as the agent reads it."""
    payload = message.payload or {}
    kind = message.event_type
    if kind == ThreadEventType.SCOUTS_LAUNCHED:
        return f"[Scouts launched] {message.content}"
    if kind == ThreadEventType.SCOUT_FINISHED:
        line = f"[Scout finished] {message.content}"
        if payload.get("titles"):
            line += "\n  Staged: " + "; ".join(payload["titles"][:8])
        for row in (payload.get("applied") or [])[:5]:
            line += (
                f"\n  Applied {row.get('memory', '')}: {row.get('effect', '')[:240]}"
            )
        for row in (payload.get("setAside") or [])[:8]:
            line += f"\n  Set aside “{row.get('title', '')}”: {row.get('reason', '')}"
        return line
    if kind == ThreadEventType.REVIEW_DECISION:
        line = (
            f"[Review] The user {_VERDICTS.get(payload.get('decision'), 'reviewed')} "
            f"finding #{payload.get('itemId')} “{payload.get('title', '')}”"
        )
        if payload.get("tags"):
            line += f" (reasons: {', '.join(payload['tags'])})"
        if payload.get("note"):
            line += f". Their note: “{payload['note']}”"
        return line
    if kind == ThreadEventType.MEMORY_PROPOSED:
        return f"[Memory proposed, awaiting the user] {message.content}"
    if kind == ThreadEventType.MEMORY_DECIDED:
        return (
            f"[Memory {payload.get('status', 'decided')} by the user"
            f" as {payload.get('layer', '')}] {message.content}"
        )
    return f"[{kind}] {message.content}"


def conversation_history(thread, before=None, max_messages=60, max_chars=60000):
    """The thread before ``before`` as turns for the runtime, oldest first.

    Events ride as user-role notes from DARE so the model reads them as facts
    about the workspace, not as things it said.
    """
    messages = ResearchChatMessage.active_objects.filter(session=thread)
    if before is not None:
        messages = messages.filter(created_at__lt=before)
    rows = list(messages.order_by("-created_at")[:max_messages])
    rows.reverse()
    history = []
    for message in rows:
        if message.role == ChatMessageRole.EVENT:
            content = f"(DARE workspace event) {event_line(message)}"
            role = ChatMessageRole.USER
        else:
            content, role = message.content, message.role
        if not content:
            continue
        if history and history[-1]["role"] == role == ChatMessageRole.USER:
            history[-1]["content"] += "\n" + content
        else:
            history.append({"role": role, "content": content})
    while sum(len(h["content"]) for h in history) > max_chars and len(history) > 2:
        history.pop(0)
    return history


def _findings_block(run_id, per_item_chars=600):
    """What a finished Scout staged, with the facts, so chat can answer from it."""
    items = ResearchStagingItem.active_objects.filter(run_id=run_id).order_by("id")
    lines = []
    for item in items:
        kind = (item.provenance or {}).get("kind") or "source"
        meta = ", ".join(str(v) for v in (item.year, kind, item.evidence_label) if v)
        finding = " ".join((item.finding or item.rationale or "").split())
        line = f"  #{item.id} {item.title} ({meta}) {item.url}\n    {finding[:per_item_chars]}"
        if item.finding and item.rationale:
            line += f"\n    Limits: {' '.join(item.rationale.split())[:300]}"
        lines.append(line)
    return "\n".join(lines)


def _followup_input(events):
    text = FOLLOWUP_BRIEF + "\n\nNEW SINCE YOUR LAST REPLY:\n"
    text += "\n".join(f"- {event_line(e)}" for e in events)
    findings = [
        _findings_block(e.payload["runId"])
        for e in events
        if e.event_type == ThreadEventType.SCOUT_FINISHED
        and (e.payload or {}).get("runId")
    ]
    findings = [block for block in findings if block]
    if findings:
        text += "\n\nWHAT THE SCOUTS FOUND (your evidence):\n" + "\n".join(findings)
    return text


def chat_prompt(thread, message=None, *, scout_now=False):
    """A chat turn answering ``message``, or with none, a follow-up on events.

    History stops where the input starts, so the user's message and the events
    a follow-up answers are each sent once.
    """
    instructions = HUB_BRIEF % {
        "token": thread_token(thread),
        "max_scouts": MAX_SCOUTS_PER_LAUNCH,
    }
    if scout_now:
        instructions += "\n\n" + SCOUT_NOW
    if message is not None:
        return Prompt(
            instructions,
            message.content,
            conversation_history(thread, before=message.created_at),
        )
    events = unseen_events(thread)
    before = events[0].created_at if events else None
    return Prompt(
        instructions,
        _followup_input(events),
        conversation_history(thread, before=before),
    )


# ── Scout ────────────────────────────────────────────────────────────────────

SCOUT_BRIEF = """You are a Scout: you find and check sources for one task. The Project section of your memory says what kind of question this is and which sources count; the rest of your memory says what the user trusts.

Restate the task as a precise question that keeps the user's intent, using the conversation below it. If it has no research intent, stage nothing and say so.

Pass run="%(run_token)s" on every DARE tool call.
1. Search with the search tools you have, DARE's and any the user connected: at most %(max_searches)d searches.
2. Read a source with mcp__dare__fetch_page before you stage it: at most %(max_reads)d pages.
3. Stage each finding with mcp__dare__stage_finding as soon as you have read its source: what the source establishes for your task, the sentence on the page that backs it (copied exactly), and its working URL. A finding says what a source shows, never what it fails to show: a page without the evidence (a landing page, an image, a paywall, the wrong document) goes under setAside in step 4. DARE checks the link, the quote, the DOI and the date, and tells you when it refuses a source or the user already has it.
4. Before you finish, call mcp__dare__report_learning once: the remembered preferences or skills that changed what you did, and each source you set aside because of one. Empty lists are fine.
5. Reply in two or three sentences: what you staged and what is still missing.

Stage only what you read. Do not write memory or skills in a Scout run."""


def thread_digest(thread, max_chars=4000):
    """A compact brief of the thread for a Scout's isolated context."""
    lines = [
        f"{'User' if turn['role'] == ChatMessageRole.USER else 'Research chat'}: "
        f"{turn['content']}"
        for turn in conversation_history(thread, max_messages=20)
    ]
    return "\n".join(lines)[-max_chars:]


def _approved_titles(project, limit=30):
    items = (
        ResearchKnowledgeItem.active_objects.filter(project=project)
        .select_related("source_staging_item")
        .order_by("-approved_at")[:limit]
    )
    return [i.source_staging_item.title for i in items if i.source_staging_item]


def scout_prompt(run):
    """A Scout's brief and its task, plus what only this run has."""
    max_searches, max_reads = scout_limits((run.selected_context or {}).get("depth"))
    instructions = SCOUT_BRIEF % {
        "max_searches": max_searches,
        "max_reads": max_reads,
        "run_token": run_token(run),
    }
    parts = [f"Your task: {run.task}"]
    titles = _approved_titles(run.project)
    if titles:
        parts.append(
            "Already approved by the user (do not stage these again; find what "
            "adds to them):\n" + "\n".join(f"- {t}" for t in titles)
        )
    if run.thread_id:
        digest = thread_digest(run.thread)
        if digest:
            parts.append(
                "The conversation this Scout was launched from (context for what "
                "the user wants; your task above is what to do):\n" + digest
            )
    return Prompt(instructions, "\n\n".join(parts))


# ── Critic ───────────────────────────────────────────────────────────────────

CRITIC_BRIEF = """You are the Critic. Decide whether this source really establishes the finding it was staged with, under the standards in your SOUL.md. Read it with mcp__dare__fetch_page when that helps, and do not rubber-stamp it.

Reply with only this JSON object:
{"verdict": "holds | overstated | unsupported | inconclusive", "reasoning": "1-3 sentences", "concerns": ["short concern"]}
holds: the source establishes the finding. overstated: relevant, but the finding claims more than the source shows. unsupported: the source does not establish it. inconclusive: you cannot tell from what is available."""


def critic_prompt(item):
    return Prompt(
        CRITIC_BRIEF,
        f"Source: {item.title}\n"
        f"URL: {item.url}\n"
        f"Task it was found for: {item.run.task if item.run else ''}\n"
        f"Finding: {item.finding or item.rationale}\n"
        f"Staged as: {item.evidence_label or 'unlabelled'}\n"
        f"Why it matters: {item.rationale}\n"
        f"Quote from the page: {item.citation_context}",
    )


# ── Artifacts ────────────────────────────────────────────────────────────────

ARTIFACT_TYPE_BRIEFS = {
    "diagram": "a Mermaid diagram (content = the mermaid source)",
    "svg": "an SVG figure (content = raw <svg>…</svg> markup)",
    "html": "a self-contained HTML page (content = the full HTML)",
    "excalidraw": 'an Excalidraw scene (content = the scene JSON, {"type":"excalidraw","version":2,"elements":[…]})',
    "code": "a code snippet (content = the code)",
    "document": "a written document (content = GitHub-flavored Markdown)",
    "docx": (
        "a Word document (content = a JSON object "
        '{"title": "...", "blocks": [...]} where each block is one of: '
        '{"type": "heading", "level": 1-4, "text": "..."}, '
        '{"type": "paragraph", "text": "...", "alignment"?: "left|center|right"}, '
        '{"type": "list", "items": ["..."], "ordered"?: true}, '
        '{"type": "table", "headers": ["..."], "rows": [["..."]]}, '
        '{"type": "blockquote", "text": "..."})'
    ),
    "pptx": (
        "a PowerPoint deck (content = a JSON object "
        '{"title": "...", "subtitle"?: "...", "slides": [...]} with 5-10 slides). '
        "Required slide fields BY LAYOUT: title/section: title (+ optional "
        "subtitle, body); bullets/summary: title + bullets (3-5 strings, each "
        "under 120 chars); twoColumn: title + leftBullets + rightBullets "
        "(+ optional leftTitle, rightTitle); table: title + headers + rows; "
        "quote: quote (+ optional attribution). Start with a title slide; every "
        "layout supports speakerNotes: put citations and detail there, not on "
        "the slide"
    ),
}

ARTIFACT_BRIEF = """You are the Presenter. Turn the user's request into one artifact about this project, grounded in your memory and in the approved knowledge listed after the request. Decide the structure and emphasis yourself rather than asking. If the request names no subject and the project has nothing to draw on, reply with one sentence saying what is missing.

%(want)s

Reply with exactly one line of metadata JSON, then a line containing only %(marker)s, then the raw content:
{"type": "<type>", "title": "<short title>"}
%(marker)s
<content>

Do not wrap or escape the content. For diagram, svg, html, document and code it is the raw text; for excalidraw, docx and pptx it is the JSON object. Use mcp__dare__web_search and mcp__dare__fetch_page when you need to check something."""


def _knowledge_block(project, per_item_chars=300, max_items=12):
    items = ResearchKnowledgeItem.active_objects.filter(project=project).select_related(
        "source_staging_item"
    )[:max_items]
    lines = []
    for k in items:
        src = k.source_staging_item
        cite = f"{src.title} ({src.authors}, {src.year})" if src else "Your note"
        body = (k.content or k.rationale or "").strip()[:per_item_chars]
        lines.append(f"- {cite}: {body}")
    return "\n".join(lines)


def artifact_prompt(run, content_schema=None):
    """The Presenter's brief for ``run``; ``content_schema`` is the JSON Schema
    a structured type's content must follow (docx, pptx)."""
    artifact_type = (run.selected_context or {}).get("artifactType", "")
    if artifact_type in ARTIFACT_TYPE_BRIEFS:
        want = (
            f"Produce {ARTIFACT_TYPE_BRIEFS[artifact_type]}; set "
            f'"type" to "{artifact_type}".'
        )
    else:
        want = (
            'Produce the most fitting renderable artifact; set "type" to one of '
            "diagram (Mermaid), svg, html, or document (Markdown)."
        )
    instructions = ARTIFACT_BRIEF % {"want": want, "marker": ARTIFACT_CONTENT_MARKER}
    if content_schema:
        instructions += (
            "\n\nThe content object MUST conform to this JSON Schema:\n"
            + json.dumps(content_schema)
        )
    task = run.task
    knowledge = _knowledge_block(run.project)
    if knowledge:
        task += (
            "\n\nGround the artifact in the user's approved project "
            f"knowledge:\n{knowledge}"
        )
    return Prompt(instructions, task)


# ── For reading in the app ───────────────────────────────────────────────────


def role_briefs():
    """Every brief a run can be given, as the app shows them.

    Handles for DARE's tools are shown as placeholders; a Scout's limits are
    the deep ones.
    """
    max_searches, max_reads = scout_limits("deep")
    return [
        {
            "key": "chat",
            "label": "Chat",
            "part": "instructions",
            "when": "Every chat turn.",
            "text": HUB_BRIEF
            % {"token": "<thread>", "max_scouts": MAX_SCOUTS_PER_LAUNCH},
        },
        {
            "key": "scout-now",
            "label": "Scout now",
            "part": "instructions",
            "when": "Added to the chat brief when you send in Scout mode.",
            "text": SCOUT_NOW,
        },
        {
            "key": "follow-up",
            "label": "Follow-up",
            "part": "input",
            "when": (
                "The input of a turn DARE starts when Scouts finish or you "
                "review findings, followed by the new events and what the "
                "Scouts found."
            ),
            "text": FOLLOWUP_BRIEF,
        },
        {
            "key": "scout",
            "label": "Scout",
            "part": "instructions",
            "when": "Every Scout run. Its input is the task, plus the thread so far.",
            "text": SCOUT_BRIEF
            % {
                "max_searches": max_searches,
                "max_reads": max_reads,
                "run_token": "<run>",
            },
        },
        {
            "key": "critic",
            "label": "Critic",
            "part": "instructions",
            "when": "When you ask the Critic about a finding. Its input is the finding.",
            "text": CRITIC_BRIEF,
        },
        {
            "key": "presenter",
            "label": "Presenter",
            "part": "instructions",
            "when": "When you generate an artifact. Its input is your request.",
            "text": ARTIFACT_BRIEF
            % {
                "want": "Produce the most fitting renderable artifact.",
                "marker": ARTIFACT_CONTENT_MARKER,
            },
        },
    ]
