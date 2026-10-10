from django.db import models
from django.utils.translation import gettext_lazy as _

APP_NAME = "assistant"

SOCKET_NAMESPACE = "/assistant"
SOCKET_EVENT = "assistant"

MESSAGE_MAX_LENGTH = 4000
PAGE_PATH_MAX_LENGTH = 512
# Prior thread messages replayed to the model each turn.
HISTORY_MESSAGES = 20
# Messages a thread returns to the client when it is opened.
THREAD_PAYLOAD_MESSAGES = 100
MAX_OUTPUT_TOKENS = 4000
DOCS_SEARCH_TOP_K = 6
# After either cap the model must answer with what it has found.
MAX_TOOL_ROUNDS = 5
MAX_TOOL_CALLS = 8
LIST_FILES_DEFAULT_LIMIT = 20
LIST_FILES_MAX_LIMIT = 100
LIST_CONVERSATIONS_DEFAULT_LIMIT = 30
LIST_CONVERSATIONS_MAX_LIMIT = 100
PLAN_MAX_ACTIONS = 25
PLAN_MAX_ITEMS = 100
PLAN_NAME_MAX_LENGTH = 255

SEARCH_PLATFORM_DOCS = "search_platform_docs"
GET_ACCOUNT_OVERVIEW = "get_account_overview"
LIST_MY_FILES = "list_my_files"
GET_CONVERSATION = "get_conversation"
GET_PROJECT = "get_project"
LIST_MY_PROJECTS = "list_my_projects"
LIST_MY_CONVERSATIONS = "list_my_conversations"
PROPOSE_CHANGES = "propose_changes"
START_PAGE_TOUR = "start_page_tour"

# Page keys (see domain/page_context.PAGES) the client has a guided tour for.
TOUR_PAGES = (
    "dashboard",
    "conversation",
    "projects",
    "files",
    "prompts",
    "workflows",
    "agents",
    "research",
    "memory",
    "mcp",
    "billing",
    "group_wallet",
    "settings",
    "help",
)

# Tools over the requesting user's own data; ToolExecutionService routes them
# to the assistant executor with the authenticated user. None of them writes:
# propose_changes only records a proposal the user applies themselves, and the
# tour tool only tells the client which tour to open.
ACCOUNT_TOOLS = frozenset(
    {
        GET_ACCOUNT_OVERVIEW,
        LIST_MY_FILES,
        LIST_MY_PROJECTS,
        LIST_MY_CONVERSATIONS,
        GET_CONVERSATION,
        GET_PROJECT,
        PROPOSE_CHANGES,
        START_PAGE_TOUR,
    }
)


class AssistantRole(models.TextChoices):
    USER = "user", _("User")
    ASSISTANT = "assistant", _("Assistant")


class AssistantMessageStatus(models.TextChoices):
    STREAMING = "streaming", _("Streaming")
    COMPLETED = "completed", _("Completed")
    STOPPED = "stopped", _("Stopped by the user")
    FAILED = "failed", _("Failed")


class ProposalStatus(models.TextChoices):
    PENDING = "pending", _("Waiting for the user")
    PARTIALLY_APPLIED = "partially_applied", _("Some changes applied")
    APPLIED = "applied", _("Applied")
    DISCARDED = "discarded", _("Discarded")


class ProposalActionStatus(models.TextChoices):
    PENDING = "pending", _("Not applied")
    APPLIED = "applied", _("Applied")


class ProposalActionType(models.TextChoices):
    """One change the assistant can propose; each can be applied and undone."""

    ADD_TO_FOLDER = "add_to_folder", _("Add files to a folder")
    REMOVE_FROM_FOLDER = "remove_from_folder", _("Remove files from a folder")
    ADD_TAG = "add_tag", _("Tag files")
    REMOVE_TAG = "remove_tag", _("Remove a tag from files")
    DELETE_FILES = "delete_files", _("Delete files")
    CREATE_PROJECT = "create_project", _("Create a project")
    ADD_TO_PROJECT = "add_to_project", _("Add files or chats to a project")
    REMOVE_FROM_PROJECT = "remove_from_project", _(
        "Remove files or chats from a project"
    )
    DELETE_PROJECT = "delete_project", _("Delete a project")
    DELETE_CONVERSATIONS = "delete_conversations", _("Delete chats")
