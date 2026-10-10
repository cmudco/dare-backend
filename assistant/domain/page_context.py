"""Where the user is in the app, derived from the route they are on.

The client sends only its pathname; this module is the single owner of how
routes map to pages and which record ids a route names. Ids are hints for
the model's tool calls — every tool re-scopes them to the requesting user.
"""

import re
from dataclasses import dataclass
from typing import Optional, Tuple

from assistant.constants import TOUR_PAGES


@dataclass(frozen=True)
class Page:
    key: str
    title: str
    summary: str


PAGES = {
    page.key: page
    for page in (
        Page(
            "conversation",
            "Chat",
            "Chat with AI models; attach files, prompts, tools, web search and "
            "pick the model and retrieval (RAG) mode per message.",
        ),
        Page(
            "dashboard",
            "Dashboard",
            "Usage overview: counts of conversations, files, prompts and tokens, "
            "a per-model token breakdown, and an environmental-impact tab.",
        ),
        Page(
            "projects",
            "Projects",
            "Personal projects group conversations with shared files, folders, "
            "libraries, instructions and a default model.",
        ),
        Page(
            "files",
            "Files",
            "The user's document library: upload, tag, organise into folders, "
            "and watch ingestion (parsing and indexing) status.",
        ),
        Page(
            "prompts",
            "Templates: Prompts",
            "Saved, reusable system prompts, on the Templates page.",
        ),
        Page(
            "agents",
            "Templates: Agents",
            "Agents pair a prompt with a model, files and settings, on the "
            "Templates page.",
        ),
        Page(
            "workflows",
            "Workflows",
            "Multi-step AI pipelines that chain model steps together.",
        ),
        Page(
            "workflow_builder",
            "Workflow builder",
            "The canvas for creating or editing a workflow's steps.",
        ),
        Page(
            "research",
            "Research",
            "Research projects run agents that scout, review and synthesise sources.",
        ),
        Page(
            "settings",
            "Settings",
            "Tabs for Account (details, avatar, password), Appearance, Chat "
            "(conversation defaults, API keys), Memory, Integrations and Data "
            "(export or delete the account).",
        ),
        Page(
            "help",
            "Help",
            "The model catalogue by tier and capability, plus learning modules.",
        ),
        Page(
            "mcp",
            "Settings: Integrations",
            "Connect external MCP tool servers to chat.",
        ),
        Page(
            "memory",
            "Settings: Memory",
            "What DARE remembers about the user across chats.",
        ),
        Page(
            "billing",
            "Billing",
            "Wallet balance, credits, transaction history and cost tracking.",
        ),
        Page("group_wallet", "Group wallet", "Shared wallets a group owner manages."),
        Page("onboarding", "Onboarding", "First-run setup for a new account."),
        Page("other", "Another page", "A page without a dedicated description."),
    )
}

_ROUTES: Tuple[Tuple[re.Pattern, str], ...] = tuple(
    (re.compile(pattern), key)
    for pattern, key in (
        (r"^/conversation(?:/(?P<conversation_id>[\w-]+))?/?$", "conversation"),
        (r"^/projects(?:/(?P<project_id>\d+))?/?$", "projects"),
        (r"^/workflows/(?:create|\d+/edit)/?$", "workflow_builder"),
        (r"^/workflows/?$", "workflows"),
        (r"^/research(?:/.*)?$", "research"),
        (r"^/settings/integrations(?:/.*)?$", "mcp"),
        (r"^/settings/memory/?$", "memory"),
        (r"^/settings(?:/(?:appearance|chat|data))?/?$", "settings"),
        (r"^/templates(?:/prompts)?/?$", "prompts"),
        (r"^/templates/agents/?$", "agents"),
        (r"^/billing/?$", "billing"),
        (r"^/group-wallet/?$", "group_wallet"),
        (
            r"^/(?P<page>dashboard|files|help|onboarding)/?$",
            "",
        ),
    )
)


@dataclass(frozen=True)
class PageContext:
    path: str
    page: Page
    conversation_id: Optional[str] = None
    project_id: Optional[int] = None


def resolve_page_context(path: str) -> PageContext:
    """Map a client pathname to the page it shows and the ids it names."""
    for pattern, key in _ROUTES:
        match = pattern.match(path)
        if not match:
            continue
        groups = match.groupdict()
        project_id = groups.get("project_id")
        return PageContext(
            path=path,
            page=PAGES[key or groups["page"]],
            conversation_id=groups.get("conversation_id"),
            project_id=int(project_id) if project_id else None,
        )
    return PageContext(path=path, page=PAGES["other"])


def describe_page_context(context: PageContext) -> str:
    """The system-prompt lines telling the model where the user is."""
    lines = [
        f"The user is on the {context.page.title} page ({context.path}). "
        f"{context.page.summary}"
    ]
    if context.page.key in TOUR_PAGES:
        lines.append(f"Its tour key for start_page_tour is '{context.page.key}'.")
    else:
        lines.append("This page has no guided tour.")
    if context.conversation_id:
        lines.append(
            f"The open conversation's id is {context.conversation_id}; call "
            "get_conversation with it when the question is about this chat."
        )
    if context.project_id:
        lines.append(
            f"The open project's id is {context.project_id}; call get_project "
            "with it when the question is about this project."
        )
    return "\n".join(lines)
