"""
Constants for the Research app (Research Mode / the "epistemic ecosystem").

Naming follows the house style: choice groups are plain classes exposing a
``choices()`` classmethod (see ``mcp/constants.py``).

Only ``ResearchProjectStatus`` is enforced at the DB level — it is a workflow
state DARE itself sets. ``StandardsTemplate`` is kept here as a canonical
reference set for the UI/docs. Enabled tools are NOT modelled here: they are
stored as free-form slugs in ``ResearchProject.enabled_tools`` (the user's
connected MCP integrations, plus built-ins like web search), so there is no
hardcoded tool enum to keep in sync.
"""


class ResearchProjectStatus:
    """Workflow status for a ResearchProject (DARE-owned)."""

    ACTIVE = "active"
    ARCHIVED = "archived"

    @classmethod
    def choices(cls):
        return [
            (cls.ACTIVE, "Active"),
            (cls.ARCHIVED, "Archived"),
        ]


class StandardsTemplate:
    """Known soul-file starter templates (free-form; NOT enforced at the DB level)."""

    RESEARCH_ETHICS = "research-ethics"
    EMPIRICAL_RIGOR = "empirical-rigor"
    CUSTOM = "custom"

    @classmethod
    def choices(cls):
        return [
            (cls.RESEARCH_ETHICS, "Research Ethics"),
            (cls.EMPIRICAL_RIGOR, "Empirical Rigor"),
            (cls.CUSTOM, "Custom"),
        ]


class ResearchSessionMode:
    """
    The delegation modes (DARE-owned, enforced). Each maps 1:1 to a Hermes
    session per project: a persistent 'scout' session, a persistent 'chat'
    session, and a persistent 'artifact' session (the Presentation Assistant —
    kept separate so artifact work never mixes into Scout's session memory).
    """

    SCOUT = "scout"
    CHAT = "chat"
    ARTIFACT = "artifact"

    @classmethod
    def choices(cls):
        return [
            (cls.SCOUT, "Scout"),
            (cls.CHAT, "Chat"),
            (cls.ARTIFACT, "Artifact"),
        ]


class ResearchSessionStatus:
    """Session lifecycle (DARE-owned, enforced)."""

    ACTIVE = "active"
    ARCHIVED = "archived"

    @classmethod
    def choices(cls):
        return [
            (cls.ACTIVE, "Active"),
            (cls.ARCHIVED, "Archived"),
        ]


class AgentRunStatus:
    """
    Run lifecycle. Produced/advanced by Hermes, so stored free-form (NOT enforced
    at the DB level); this is the canonical set for the UI/docs.
    """

    STARTED = "started"
    RUNNING = "running"
    QUEUED = "queued"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    STOPPING = "stopping"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    OUTCOME_UNKNOWN = "outcome_unknown"

    @classmethod
    def choices(cls):
        return [
            (cls.STARTED, "Started"),
            (cls.RUNNING, "Running"),
            (cls.QUEUED, "Queued"),
            (cls.WAITING_FOR_APPROVAL, "Waiting for approval"),
            (cls.STOPPING, "Stopping"),
            (cls.COMPLETED, "Completed"),
            (cls.FAILED, "Failed"),
            (cls.CANCELLED, "Cancelled"),
            (cls.OUTCOME_UNKNOWN, "Outcome unknown"),
        ]


class AgentToolCallStatus:
    """Outcome of a single tool call (recorded; stored free-form)."""

    SUCCESS = "success"
    ERROR = "error"

    @classmethod
    def choices(cls):
        return [
            (cls.SUCCESS, "Success"),
            (cls.ERROR, "Error"),
        ]


class SourceType:
    """Kind of source record (free-form; the canonical set for the UI/docs)."""

    UPLOAD = "upload"
    PAPER = "paper"
    BOOK = "book"
    ARTICLE = "article"
    OTHER = "other"

    @classmethod
    def choices(cls):
        return [
            (cls.UPLOAD, "Upload"),
            (cls.PAPER, "Paper"),
            (cls.BOOK, "Book"),
            (cls.ARTICLE, "Article"),
            (cls.OTHER, "Other"),
        ]


class SoulFileOrigin:
    """Where a soul-file version's content came from (provenance)."""

    TEMPLATE = "template"
    UPLOAD = "upload"
    EMPTY = "empty"
    EDIT = "edit"


# Starter soul-file content per standards template. Plain text (one rule per
# line) so it renders cleanly; the scholar can edit it into richer markdown.
SOUL_TEMPLATES = {
    "research-ethics": (
        "1. Never fabricate — every statistic and citation must be real and "
        "checkable.\n"
        "2. Signal uncertainty honestly — separate what the data shows from "
        "what it merely suggests.\n"
        "3. Do not overstate sources — a cross-country correlation is not "
        "proof of cause or transfer.\n"
        "4. Separate values from evidence — flag normative or ideological "
        "claims distinctly from empirical findings."
    ),
    "empirical-rigor": (
        "1. Prefer primary sources and pre-registered studies.\n"
        "2. Always surface the method — sample size, design and effect size.\n"
        "3. Flag replication status where it is known.\n"
        "4. Distinguish correlation from causation explicitly."
    ),
}


def soul_template_content(standards_template):
    """Return (content, origin) for a project's initial soul-file version."""
    if standards_template in SOUL_TEMPLATES:
        return SOUL_TEMPLATES[standards_template], (
            f"{SoulFileOrigin.TEMPLATE}:{standards_template}"
        )
    return "", SoulFileOrigin.EMPTY


class MemoryType:
    """Scope of a memory (free-form; the canonical set for the UI/docs)."""

    PROJECT_MEMORY = "projectMemory"
    ROLE_MEMORY = "roleMemory"
    RUN_SCRATCHPAD = "runScratchpad"

    @classmethod
    def choices(cls):
        return [
            (cls.PROJECT_MEMORY, "Project memory"),
            (cls.ROLE_MEMORY, "Role memory"),
            (cls.RUN_SCRATCHPAD, "Run scratchpad"),
        ]


class ProjectMemorySource:
    """Where a project-memory snapshot came from (free-form)."""

    MANUAL = "manual"
    PROPOSAL = "proposal"
    AGENT = "agent"

    @classmethod
    def choices(cls):
        return [
            (cls.MANUAL, "Manual"),
            (cls.PROPOSAL, "Accepted proposal"),
            (cls.AGENT, "Agent"),
        ]


class MemoryProposalStatus:
    """Proposal lifecycle (DARE-owned, enforced)."""

    PROPOSED = "proposed"
    ACCEPTED = "accepted"
    REJECTED = "rejected"

    @classmethod
    def choices(cls):
        return [
            (cls.PROPOSED, "Proposed"),
            (cls.ACCEPTED, "Accepted"),
            (cls.REJECTED, "Rejected"),
        ]


class ChatMessageRole:
    """Author of a chat message (DARE-owned, enforced)."""

    USER = "user"
    ASSISTANT = "assistant"

    @classmethod
    def choices(cls):
        return [
            (cls.USER, "User"),
            (cls.ASSISTANT, "Assistant"),
        ]


class StagingItemStatus:
    """Review workflow status for a staging item (DARE-owned, enforced)."""

    STAGED = "staged"
    APPROVED = "approved"
    REJECTED = "rejected"
    LATER = "later"

    @classmethod
    def choices(cls):
        return [
            (cls.STAGED, "Staged"),
            (cls.APPROVED, "Approved"),
            (cls.REJECTED, "Rejected"),
            (cls.LATER, "Later"),
        ]


class EvidenceLabel:
    """
    How a candidate relates to the thesis (produced by the agent; stored
    free-form). Canonical set per the §11 contract.
    """

    SUPPORTING = "supporting"
    DISPUTING = "disputing"
    PARTIAL = "partial"
    TANGENTIAL = "tangential"
    UNVERIFIABLE = "unverifiable"

    @classmethod
    def choices(cls):
        return [
            (cls.SUPPORTING, "Supporting"),
            (cls.DISPUTING, "Disputing"),
            (cls.PARTIAL, "Partial"),
            (cls.TANGENTIAL, "Tangential"),
            (cls.UNVERIFIABLE, "Unverifiable"),
        ]


class StagingSourceType:
    """Type of staged item (free-form; the contract's default is sourceCandidate)."""

    SOURCE_CANDIDATE = "sourceCandidate"
