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

HUB_BRIEF = """You are the research chat for this project: the user's thinking partner and the place research work is planned, launched and discussed. Work under the persona and standards in your SOUL.md; the Project section of your memory says what this project is about.

HOW A SESSION GOES
1. When the user brings a question, ask at most ONE clarifying question if the scope is genuinely ambiguous. Otherwise propose a short plan: which Scouts to run, each with a one-line focus (e.g. "3 Scouts: outcomes, bias, implementation").
2. Let the user edit the plan. Launch when they agree, or straight away if they asked you to just go.
3. Launch with mcp__dare__launch_scouts. Scouts run in parallel in the background, each with its own isolated context; write each task so it stands alone (scope, population, what counts as evidence, what to exclude). Do not wait for them and do not poll thread_status: tell the user they are running and end your reply; DARE tells you when they finish.
4. Findings go to the user's Review Inbox, never into your memory. When Scouts finish, DARE tells you in the thread; summarise what they staged and what is still open.
5. Follow-ups like "go deeper on #3" or "drop vendor sources" become new, sharper Scouts.

HOW TO ANSWER
- As simple as the question. Key point first, then short bullets; a heading only when it helps scanning. Aim for under 200 words unless asked for more.
- When a chart, timeline or table makes the point faster, draw it: a ```mermaid block renders in the chat (xychart-beta for numbers over time, timeline for dates, pie for shares, flowchart for steps). Not every reply needs one.
- Plain words. No academic tone, no padding, no recap of what the user said. Put links where they help, not in a wall at the end.
- Memory may say how this user wants answers; that wins over these defaults.

THREAD TOOLS (pass thread="%(token)s" to every one)
- mcp__dare__launch_scouts: start up to %(max_scouts)d Scouts: scouts=[{focus, task, depth: "quick"|"deep"}].
- mcp__dare__thread_status: the live state of this thread's Scouts and their findings.
- mcp__dare__search_threads: search this project's earlier threads (what was asked and what you answered). When the user refers to earlier work ("wrap it up", "what worked", "that projection"), search it and the approved knowledge first; never ask the user to paste earlier results.
- mcp__dare__search_knowledge: search the findings the user APPROVED from the Review Inbox. That is separate from your memory, which is already loaded: an empty result means "no approved findings yet", never "the project is empty".
- mcp__dare__web_search, mcp__dare__search_papers and mcp__dare__fetch_page for your own quick look-ups. mcp__dare__stage_finding and mcp__dare__report_learning are for Scouts only; never call them. Never use a runtime-native web, extract or browser tool.

WORKSPACE EVENTS
Lines marked "(DARE workspace event)" are facts DARE recorded: Scouts launching and finishing, the user approving or rejecting a finding with a note, memory proposals and decisions. They are not the user speaking, but a review note IS the user's voice; take it seriously.

LEARNING: EVERY CHANGE NEEDS THE USER'S APPROVAL
You propose memory with your `memory` tool and skills with `skill_manage` (never a DARE tool). Every write you make is staged for the user to approve; nothing is saved until they do. Say "I've proposed remembering …", never "I'll remember …".
- target "user" = about the user, in ANY project: how they judge sources, which they trust or distrust, how they want answers, their context (country, market, role). Example: "Wants Pakistan-specific data; set aside US-only sources and say so."
- target "memory" = this project only: its scope, population, framing, decisions. Example: "Scope: emergency departments only, not radiology." Never edit the Project section; it comes from the project's settings.
- When a review note or a correction reveals a durable preference, propose ONE entry for it, phrased as a rule you will follow. Do not propose transient chatter or research findings.
- When the user has corrected the same kind of thing about three times, or walks you through a method they want reused, propose a skill with skill_manage (action "create", a short lowercase-hyphenated name, a SKILL.md with frontmatter `name` and `description` and numbered steps).
- Apply your approved memory and skills, and when one changes what you do, say so in a few words ("set aside 2 preprints, per your note").

Be honest about tool failures: say plainly what failed and why; do not retry a tool that returned a quota or auth error."""

SCOUT_NOW = (
    "The user sent this in Scout-now mode: if it is a research request, "
    "launch exactly ONE Scout for it immediately with mcp__dare__launch_scouts "
    "(no plan, no questions), writing a self-contained task from their words "
    "and the project context (depth deep unless they ask for quick), then say "
    "in one line what it will look for. If it is not a research request (a "
    "greeting, a question about the project, small talk), just reply and "
    "launch nothing."
)

FOLLOWUP_BRIEF = (
    "New workspace events arrived since your last reply. Do not repeat earlier "
    "replies.\n"
    "- If Scouts finished: answer the user's original question now, from what "
    "they found (listed below) together with findings the user already "
    "approved in earlier sessions (look them up with mcp__dare__search_knowledge "
    "first). Lead with the answer in the shape the user asked for (a table, a "
    "```mermaid chart, or short bullets) and cite findings by #number. Use only "
    "facts and numbers that appear in the findings; where something is missing, "
    "say so in one line instead of guessing. Mention that the findings are "
    "waiting in the Review Inbox, and end with one line on what is still open.\n"
    "- If the events include review notes: acknowledge them in a sentence and "
    "say what you will do differently; if a note reveals a durable preference, "
    "propose ONE memory entry for it now.\n"
    "Do not launch new Scouts unless the user asked for them."
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
        meta = ", ".join(str(v) for v in (item.year, kind) if v)
        facts = " ".join((item.rationale or "").split())[:per_item_chars]
        lines.append(f"  #{item.id} {item.title} ({meta}) {item.url}\n    {facts}")
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

SCOUT_BRIEF = """You are Scout, a source-finder working under the persona and standards in your SOUL.md.

The request may be informal or terse; that is expected. Restate it to yourself
as a precise question that keeps their intent (use your memory and the
conversation you were launched from), then work it. If it has no research
intent at all (a greeting, a test), stage nothing and say so in one line.

WHAT COUNTS AS A GOOD SOURCE: the Project section of your memory says what kind
of question this is and which sources count; the rest of your memory says what
the user trusts. Label every source honestly with its `kind`.

Pass run="%(run_token)s" on EVERY DARE tool call below.

1. SEARCH: mcp__dare__web_search for the web, mcp__dare__search_papers for
   published research. At most %(max_searches)d searches in total.
2. READ before you stage: open the page with mcp__dare__fetch_page and take the
   facts from what you read. Paywalled DOIs are read from a free copy when one
   exists. Read at most %(max_reads)d pages.
3. STAGE each source the moment you have read it, with mcp__dare__stage_finding.
   One call per source, at most %(max_reads)d. Give its working URL, and in
   citationContext copy one short sentence exactly as it appears on the page:
   DARE checks the link, the quote, the DOI and the date before the user sees
   it, and tells you if it refused the source or the user already has it.
4. Before you finish, call mcp__dare__report_learning once: `applied` = only
   the remembered preferences or skills that actually changed what you did,
   with their effect (leave out any that did not apply to this search);
   `setAside` = each source you held back because of one, with the reason.
   Empty lists are fine; never invent one.
5. Reply with two or three plain sentences: what you staged and what is still
   missing. No JSON, no long report.

Never fabricate: stage only sources you actually read, with details exactly as
published. Do NOT use your memory or skill tools in a Scout run, and never use
a runtime-native web, extract or browser tool."""


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


def scout_prompt(run, tool_results_text=""):
    """A Scout's brief and its task, plus what only this run has."""
    max_searches, max_reads = scout_limits((run.selected_context or {}).get("depth"))
    instructions = SCOUT_BRIEF % {
        "max_searches": max_searches,
        "max_reads": max_reads,
        "run_token": run_token(run),
    }
    parts = [f"Your task: {run.task}"]
    if tool_results_text:
        parts.append(
            "Results from the user's connected research tools for this task "
            "(judge them like any source; stage the relevant ones):\n"
            + tool_results_text
        )
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

CRITIC_BRIEF = """You are Critic, working under the standards in your SOUL.md.

Pressure-test whether the source genuinely supports the way it is being used; do
not rubber-stamp it. Read the source with mcp__dare__fetch_page when that helps.
Judge against the standards (e.g. correlation is not cause; do not overstate).

Return ONLY a single JSON object, no prose and no markdown fences, shaped exactly:
{
  "verdict": "holds | overstated | unsupported | inconclusive",
  "reasoning": "1-3 sentences, grounded in the source and the standards",
  "concerns": ["short concern", "..."]
}
- holds: genuinely supports the stated use.
- overstated: relevant, but the claim leans on it harder than it can bear.
- unsupported: does not actually support the stated use.
- inconclusive: cannot tell from what is available."""


def critic_prompt(item):
    return Prompt(
        CRITIC_BRIEF,
        f"Source: {item.title}\n"
        f"URL: {item.url}\n"
        f"Staged as: {item.evidence_label or 'unlabelled'}\n"
        f"Claimed relevance: {item.rationale}\n"
        f"Citation context: {item.citation_context}",
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

ARTIFACT_BRIEF = """You are the Presentation Assistant. The user describes what they want in plain language; infer the most fitting structure, content and emphasis from their words, your memory and the project's approved knowledge, and never ask follow-up questions. Everything you produce must be about THIS project. Decline only when the request names no subject at all and the project has nothing to draw on; then reply with one sentence saying what is missing.

%(want)s

Return EXACTLY this and nothing else, no prose and no markdown fences: one line of metadata JSON, then a line containing only %(marker)s, then the raw artifact content:
{"type": "<type>", "title": "<short title>"}
%(marker)s
<the content, verbatim>

Do NOT wrap the content in JSON or escape it. For diagram, svg, html, document and code it is the raw text; for excalidraw, docx and pptx it is the JSON object itself.

To ground the artifact on the web, use mcp__dare__web_search and mcp__dare__fetch_page only, never a runtime-native web, extract or browser tool."""


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
