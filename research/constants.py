"""
Constants for the Research app.

Choice groups are plain classes exposing a ``choices()`` classmethod (see
``mcp/constants.py``). Enabled tools are not modelled here: they are free-form
slugs in ``ResearchProject.enabled_tools`` (connected MCP servers plus web).
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


class ResearchSessionMode:
    """What a session holds: a chat thread, or the project's Scout or artifact runs."""

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


# Statuses in which a run still holds the runtime (not yet terminal).
LIVE_RUN_STATUSES = frozenset(
    {
        AgentRunStatus.STARTED,
        AgentRunStatus.QUEUED,
        AgentRunStatus.RUNNING,
        AgentRunStatus.STOPPING,
        AgentRunStatus.WAITING_FOR_APPROVAL,
    }
)


TERMINAL_RUN_STATUSES = frozenset(
    {AgentRunStatus.COMPLETED, AgentRunStatus.FAILED, AgentRunStatus.CANCELLED}
)


class AgentRole:
    """Which agent a run is. Each role has its own brief in ``prompt_builder``."""

    CHAT = "main-assistant"
    SCOUT = "scout"
    CRITIC = "critic"
    PRESENTER = "presenter"


# A Scout's (searches, page reads) per depth. The brief states them and the
# gateway enforces them with a little slack.
SCOUT_LIMITS = {"quick": (2, 3), "deep": (5, 10)}


def scout_limits(depth):
    return SCOUT_LIMITS["quick"] if depth == "quick" else SCOUT_LIMITS["deep"]


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
    """Author of a chat message (DARE-owned, enforced).

    ``event`` rows are DARE-written facts pushed into a thread (a Scout started
    or finished, a review decision, a memory proposal) so both the scholar and
    the agent see what happened outside the conversation itself.
    """

    USER = "user"
    ASSISTANT = "assistant"
    EVENT = "event"

    @classmethod
    def choices(cls):
        return [
            (cls.USER, "User"),
            (cls.ASSISTANT, "Assistant"),
            (cls.EVENT, "Event"),
        ]


class ThreadEventType:
    """What a thread event row records (DARE-owned, enforced)."""

    SCOUTS_LAUNCHED = "scouts_launched"
    SCOUT_FINISHED = "scout_finished"
    REVIEW_DECISION = "review_decision"
    MEMORY_PROPOSED = "memory_proposed"
    MEMORY_DECIDED = "memory_decided"

    @classmethod
    def choices(cls):
        return [
            (cls.SCOUTS_LAUNCHED, "Scouts launched"),
            (cls.SCOUT_FINISHED, "Scout finished"),
            (cls.REVIEW_DECISION, "Review decision"),
            (cls.MEMORY_PROPOSED, "Memory proposed"),
            (cls.MEMORY_DECIDED, "Memory decided"),
        ]


class MemoryLayer:
    """Where an approved memory lives (D5).

    ``researcher`` travels with the scholar into every project (rendered to
    each profile's USER.md); ``project`` stays in one project (MEMORY.md);
    ``procedure`` is a learned method, rendered as a Hermes skill.
    """

    RESEARCHER = "researcher"
    PROJECT = "project"
    PROCEDURE = "procedure"

    @classmethod
    def choices(cls):
        return [
            (cls.RESEARCHER, "Researcher-wide"),
            (cls.PROJECT, "Project only"),
            (cls.PROCEDURE, "Procedure"),
        ]


class MemoryProposalOrigin:
    """Who raised a memory proposal."""

    FOREGROUND = "foreground"
    BACKGROUND_REVIEW = "background_review"
    LEGACY = "legacy"

    @classmethod
    def choices(cls):
        return [
            (cls.FOREGROUND, "During the conversation"),
            (cls.BACKGROUND_REVIEW, "Agent's own reflection"),
            (cls.LEGACY, "Earlier memory file"),
        ]


class ReviewReasonTag:
    """Quick-pick reasons a scholar can attach to a review decision."""

    PREPRINT = "preprint"
    OFF_SCOPE = "off_scope"
    CONFLICT_OF_INTEREST = "conflict_of_interest"
    WEAK_METHOD = "weak_method"
    DUPLICATE = "duplicate"
    OUTDATED = "outdated"
    STRONG_EVIDENCE = "strong_evidence"

    @classmethod
    def choices(cls):
        return [
            (cls.PREPRINT, "Preprint"),
            (cls.OFF_SCOPE, "Off scope"),
            (cls.CONFLICT_OF_INTEREST, "Conflict of interest"),
            (cls.WEAK_METHOD, "Weak method"),
            (cls.DUPLICATE, "Duplicate"),
            (cls.OUTDATED, "Outdated"),
            (cls.STRONG_EVIDENCE, "Strong evidence"),
        ]

    @classmethod
    def values(cls):
        return {value for value, _ in cls.choices()}


# Most Scouts one chat turn may launch at once; each is a parallel Hermes run.
MAX_SCOUTS_PER_LAUNCH = 4


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


# How a staged source bears on the question, as the Scout labels it.
EVIDENCE_LABELS = frozenset(
    {"supporting", "disputing", "partial", "tangential", "unverifiable"}
)
# What sort of source a staged item is.
SOURCE_KINDS = frozenset(
    {
        "peer-reviewed",
        "preprint",
        "report",
        "article",
        "documentation",
        "official-data",
        "news",
        "community",
        "video",
    }
)


class StagingSourceType:
    """Type of staged item (free-form; the contract's default is sourceCandidate)."""

    SOURCE_CANDIDATE = "sourceCandidate"


class ProjectKind:
    """What sort of question a project asks; sets which sources count and how fresh.

    ``fresh_months`` flags a staged source older than that (0 = age doesn't matter).
    ``guidance`` is handed to Scouts verbatim.
    """

    GENERAL = "general"
    ACADEMIC = "academic"
    WORK = "work"
    MONEY = "money"
    PERSONAL = "personal"
    NEWS = "news"

    CONFIG = {
        GENERAL: {
            "label": "General",
            "fresh_months": 0,
            "guidance": "Use whatever sources answer the question best, and label what each one is.",
        },
        ACADEMIC: {
            "label": "Academic",
            "fresh_months": 0,
            "guidance": "Prefer peer-reviewed papers and preprints; use search_papers first.",
        },
        WORK: {
            "label": "Work & tech",
            "fresh_months": 24,
            "guidance": (
                "Prefer official docs, specs, changelogs, engineering write-ups from "
                "teams that built it, and benchmarks with methods. Papers when they exist."
            ),
        },
        MONEY: {
            "label": "Money & markets",
            "fresh_months": 12,
            "guidance": (
                "Prefer official data: regulators, exchanges, central banks, fund "
                "factsheets and offering documents. Numbers must be dated. No tips or "
                "promotional content; this is research, not advice."
            ),
        },
        PERSONAL: {
            "label": "Personal & habits",
            "fresh_months": 0,
            "guidance": (
                "Prefer controlled studies and careful practitioner write-ups. Community "
                "threads are fine for lived experience if labelled community."
            ),
        },
        NEWS: {
            "label": "News & politics",
            "fresh_months": 12,
            "guidance": (
                "Prefer primary records (court orders, official statements) and "
                "established outlets; show more than one side; date every claim."
            ),
        },
    }

    @classmethod
    def choices(cls):
        return [(key, value["label"]) for key, value in cls.CONFIG.items()]

    @classmethod
    def config(cls, kind):
        return cls.CONFIG.get(kind) or cls.CONFIG[cls.GENERAL]
