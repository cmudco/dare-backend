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
MAX_TOOL_ROUNDS = 3
MAX_TOOL_CALLS = 6
LIST_FILES_DEFAULT_LIMIT = 20
LIST_FILES_MAX_LIMIT = 100
PLAN_MAX_GROUPS = 25
PLAN_NAME_MAX_LENGTH = 255

SEARCH_PLATFORM_DOCS = "search_platform_docs"
GET_ACCOUNT_OVERVIEW = "get_account_overview"
LIST_MY_FILES = "list_my_files"
GET_CONVERSATION = "get_conversation"
GET_PROJECT = "get_project"
PROPOSE_FILE_ORGANIZATION = "propose_file_organization"

# Tools over the requesting user's own data; ToolExecutionService routes them
# to the assistant executor with the authenticated user. None of them writes:
# the organisation tool only records a proposal the user applies themselves.
ACCOUNT_TOOLS = frozenset(
    {
        GET_ACCOUNT_OVERVIEW,
        LIST_MY_FILES,
        GET_CONVERSATION,
        GET_PROJECT,
        PROPOSE_FILE_ORGANIZATION,
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
    APPLIED = "applied", _("Applied")
    DISCARDED = "discarded", _("Discarded")
