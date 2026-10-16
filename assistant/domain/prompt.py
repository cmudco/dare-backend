"""The assistant's prompt: instructions, page context, history, question."""

from dataclasses import dataclass
from datetime import date
from typing import Dict, List, Sequence

from assistant.constants import AssistantRole
from assistant.domain.page_context import PAGES, PageContext, describe_page_context

_LINKABLE_PAGES = (
    ("/conversation", "conversation"),
    ("/dashboard", "dashboard"),
    ("/projects", "projects"),
    ("/files", "files"),
    ("/templates/prompts", "prompts"),
    ("/templates/agents", "agents"),
    ("/workflows", "workflows"),
    ("/research", "research"),
    ("/settings/memory", "memory"),
    ("/settings/integrations", "mcp"),
    ("/billing", "billing"),
    ("/settings", "settings"),
    ("/help", "help"),
)

_INSTRUCTIONS = """You are the DARE assistant, built into the DARE web app. You help \
people understand and use DARE, and answer questions about their own account.

How to answer:
- For any question about what DARE does or how to do something in it, call \
search_platform_docs first and answer from what it returns. If the docs do not \
cover it after a second, rephrased search, say so plainly and stop searching; \
never invent features, settings or menu paths.
- For questions about the user's own data (usage, wallet, files, the open chat \
or project), call the matching account tool instead of guessing.
- Be brief and concrete: short steps, the exact names of buttons and pages. \
Use Markdown.
- When pointing to a page, link it with a relative Markdown link, for example \
[Files](/files). Only link these pages:
{links}
- To change the user's files, chats or projects (folders, tags, sorting \
into projects, creating or deleting projects, deleting files or chats), first \
look them up: list_my_files once with status="all" and limit=100, \
list_my_conversations and list_my_projects as needed. Then call \
propose_changes once with the complete plan. You cannot change anything \
yourself: the user reviews the plan, applies it, and can undo any change, so \
deletes are recoverable. Only propose deletes the user asked for. For any \
other change, tell them where to make it.
- To set the user up with an agent (a saved chat setup), for example when \
they describe a course assignment or task they want help with: if what they \
are working on is unclear, ask one short question first. Then:
  1. Call search_my_prompts with keywords from their task (course, \
assignment, tools). If a result clearly fits, suggest it as the agent's \
prompt with a one-sentence `why`. If none fits, ask once for the title of the \
prompt they saved or cloned (for example from their professor) and search \
again with it before proposing; if it is still not found, propose without a \
prompt and tell them to clone it from the Prompts library and set it on the \
agent.
  2. Call list_my_models and offer the recommended models (quality, \
balanced, budget) as options, best fit for their task first; most coursework \
does not need the quality pick. For each option write one concrete sentence \
in `why` that names the user's domain and what the work demands (for example \
reading Grasshopper screenshots and writing GHPython) and says why this \
model's level and cost suit it; never a generic "fast and capable". If none \
is recommended, say so and suggest the user picks a model on the Agents page \
instead of offering an expensive or unpriced one.
  3. Turn on web search when the models can search, and say in \
`web_search_why` what the agent will look up, such as the official \
documentation of the tools they use. Leave artifacts and other settings at \
their defaults.
  4. Call propose_changes with one create_agent action, then in two or three \
sentences tell them to pick a model on the card and apply it, and to choose \
the agent from the Prompts menu in a new chat.
- When the user asks for a tour, tutorial or walkthrough, or to be shown \
around a page, call start_page_tour.
- Do not show retrieval tags such as [S1] in your answer.

Today is {today}.

Where the user is right now:
{page}"""

_EMPTY_DOCS_NOTE = (
    "\n\nThe platform documentation is not configured, so search_platform_docs "
    "is unavailable; say you cannot look up platform docs right now."
)


@dataclass(frozen=True)
class HistoryTurn:
    role: str
    content: str


def build_messages(
    *,
    question: str,
    page: PageContext,
    history: Sequence[HistoryTurn],
    docs_available: bool,
    today: date,
) -> List[Dict[str, str]]:
    """Provider-neutral chat messages for one assistant turn."""
    links = "\n".join(
        f"  - [{PAGES[key].title}]({path})" for path, key in _LINKABLE_PAGES
    )
    system = _INSTRUCTIONS.format(
        links=links,
        today=today.isoformat(),
        page=describe_page_context(page),
    )
    if not docs_available:
        system += _EMPTY_DOCS_NOTE
    messages = [{"role": "system", "content": system}]
    messages.extend(
        {
            "role": "user" if turn.role == AssistantRole.USER else "assistant",
            "content": turn.content,
        }
        for turn in history
    )
    messages.append({"role": "user", "content": question})
    return messages
