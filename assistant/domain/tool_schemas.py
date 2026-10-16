"""Model-facing definitions of the assistant's tools (OpenAI format).

Kept free of ORM imports so the DARE tool registry can import them.
"""

from typing import Dict

from assistant.constants import (
    AGENT_MAX_MODEL_OPTIONS,
    AGENT_REASON_MAX_LENGTH,
    GET_ACCOUNT_OVERVIEW,
    GET_CONVERSATION,
    GET_PROJECT,
    LIST_CONVERSATIONS_MAX_LIMIT,
    LIST_MY_CONVERSATIONS,
    LIST_MY_FILES,
    LIST_MY_MODELS,
    LIST_MY_PROJECTS,
    SEARCH_MY_PROMPTS,
    PLAN_MAX_ACTIONS,
    PROPOSE_CHANGES,
    SEARCH_PLATFORM_DOCS,
    START_PAGE_TOUR,
    TOUR_PAGES,
    ProposalActionType,
)


def _function(name: str, description: str, properties: Dict, required=()) -> Dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": list(required),
            },
        },
    }


def search_platform_docs_schema() -> Dict:
    return _function(
        SEARCH_PLATFORM_DOCS,
        "Search DARE's official user documentation for passages about a "
        "feature, page, setting or how-to. Write a focused query naming the "
        "feature or task, not the user's message verbatim. Call it again "
        "with a different query if the first results miss.",
        {
            "query": {
                "type": "string",
                "description": "Focused search query about DARE.",
            }
        },
        required=("query",),
    )


def get_account_overview_schema() -> Dict:
    return _function(
        GET_ACCOUNT_OVERVIEW,
        "The user's dashboard numbers: counts of conversations, messages, "
        "files, prompts, lifetime tokens, plus their DARE wallet balance and "
        "which wallet chat is billed to.",
        {},
    )


def list_my_files_schema() -> Dict:
    return _function(
        LIST_MY_FILES,
        "The user's most recent files: id, name, ingestion status (processing, "
        "processed, failed, needs OCR), stage, error, tags and folders. Use it "
        "for questions like 'why isn't my file ready?'. One call with "
        "status='all' and limit=100 returns every file; never call it once "
        "per status.",
        {
            "status": {
                "type": "string",
                "enum": ["all", "processing", "processed", "failed", "needs_ocr"],
                "description": "Files in this status; 'all' (default) returns every file in one call.",
            },
            "name_contains": {
                "type": "string",
                "description": "Only files whose name contains this text; empty for no filter.",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 100,
                "description": "How many files to return (default 20).",
            },
        },
    )


def get_conversation_schema() -> Dict:
    return _function(
        GET_CONVERSATION,
        "Settings and size of one of the user's conversations: title, chosen "
        "model and the model of the latest reply, "
        "retrieval (RAG) mode, attached sources, enabled tools and message "
        "count.",
        {
            "conversation_id": {
                "type": "string",
                "description": "Conversation id from the page context.",
            }
        },
        required=("conversation_id",),
    )


def get_project_schema() -> Dict:
    return _function(
        GET_PROJECT,
        "One of the user's personal projects: name, instructions, default "
        "model and counts of its files, folders, libraries and chats.",
        {
            "project_id": {
                "type": "integer",
                "description": "Project id from the page context.",
            }
        },
        required=("project_id",),
    )


def list_my_projects_schema() -> Dict:
    return _function(
        LIST_MY_PROJECTS,
        "Every one of the user's personal projects: id, name, description and "
        "counts of its chats, files and folders.",
        {},
    )


def list_my_conversations_schema() -> Dict:
    return _function(
        LIST_MY_CONVERSATIONS,
        "The user's chats, newest first: conversation id, title, the project "
        "it is in (or none) and when it was last updated.",
        {
            "project": {
                "type": "string",
                "description": (
                    "'none' for chats outside any project, a project id for "
                    "that project's chats; omit for all chats."
                ),
            },
            "title_contains": {
                "type": "string",
                "description": "Only chats whose title contains this text.",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": LIST_CONVERSATIONS_MAX_LIMIT,
                "description": "How many chats to return (default 30).",
            },
        },
    )


def list_my_models_schema() -> Dict:
    return _function(
        LIST_MY_MODELS,
        "The chat models the user can use right now, from the wallet that "
        "pays for their chats (their LiteLLM gateway models when that wallet "
        "is a gateway). Recommended models come first, each with "
        "recommended_as: quality (newest flagship GPT), balanced (newest Gemini "
        "Flash) or budget (cheapest good option), all within the price limit "
        "for agents. Per model: model_ref (pass to create_agent), name, "
        "recommended_as, output price in USD per million tokens (null when "
        "unknown), and whether it accepts images and can search the web.",
        {},
    )


def search_my_prompts_schema() -> Dict:
    return _function(
        SEARCH_MY_PROMPTS,
        "Search the user's saved prompts (including ones cloned from the "
        "Prompts library) by keywords in their title or text, best match "
        "first: prompt_id, title and the start of the text.",
        {
            "query": {
                "type": "string",
                "description": (
                    "Keywords from the user's task, such as the course, "
                    "assignment name or tools, or the prompt title they gave."
                ),
            }
        },
        required=("query",),
    )


_AGENT_SCHEMA = {
    "type": "object",
    "description": "create_agent only: the models to offer and whether to search the web.",
    "properties": {
        "description": {
            "type": "string",
            "description": "What the agent helps with, one or two sentences.",
        },
        "options": {
            "type": "array",
            "minItems": 1,
            "maxItems": AGENT_MAX_MODEL_OPTIONS,
            "description": "Models the user chooses between, best fit first.",
            "items": {
                "type": "object",
                "properties": {
                    "model_ref": {
                        "type": "string",
                        "description": "A model_ref from list_my_models.",
                    },
                    "why": {
                        "type": "string",
                        "maxLength": AGENT_REASON_MAX_LENGTH,
                        "description": (
                            "One sentence tying this model's strength and cost "
                            "to the user's task."
                        ),
                    },
                },
                "required": ["model_ref", "why"],
            },
        },
        "prompt": {
            "type": "object",
            "description": (
                "One of the user's prompts from search_my_prompts that fits "
                "this task; omit when none clearly fits."
            ),
            "properties": {
                "prompt_id": {"type": "integer"},
                "why": {
                    "type": "string",
                    "maxLength": AGENT_REASON_MAX_LENGTH,
                    "description": "One sentence on why this prompt fits the task.",
                },
            },
            "required": ["prompt_id", "why"],
        },
        "enable_web_search": {"type": "boolean"},
        "web_search_why": {
            "type": "string",
            "maxLength": AGENT_REASON_MAX_LENGTH,
            "description": (
                "When web search is on: one sentence on what the agent will look "
                "up for this task, such as the official documentation."
            ),
        },
    },
    "required": ["options", "enable_web_search", "web_search_why"],
}


def propose_changes_schema() -> Dict:
    return _function(
        PROPOSE_CHANGES,
        "Propose changes to the user's files, chats, projects and agents. This "
        "changes nothing: the plan is shown to the user as a card where they "
        "apply, undo and re-apply each change. Use only ids from "
        "list_my_files and list_my_conversations, and project, folder and tag "
        "names that exist (add_* and create_project may use new names). "
        "Deletes are recoverable with Undo. Send one complete plan.\n"
        "Action types:\n"
        "- add_to_folder / remove_from_folder: name=folder, file_ids.\n"
        "- add_tag / remove_tag: name=tag label, file_ids.\n"
        "- delete_files: file_ids.\n"
        "- create_project: name, optional description.\n"
        "- add_to_project / remove_from_project: name=project, file_ids "
        "(project sources) and/or conversation_ids (chats). add_to_project "
        "creates the project if it does not exist.\n"
        "- delete_project: name=project; its chats return to the chat list.\n"
        "- delete_conversations: conversation_ids.\n"
        "- create_agent: name=agent name, agent={options (1-3 models, each "
        "with why), enable_web_search, web_search_why, optional prompt "
        "{prompt_id, why}, description}. The user picks the model on the card.",
        {
            "summary": {
                "type": "string",
                "description": "One short sentence describing the plan.",
            },
            "actions": {
                "type": "array",
                "minItems": 1,
                "maxItems": PLAN_MAX_ACTIONS,
                "items": {
                    "type": "object",
                    "properties": {
                        "type": {
                            "type": "string",
                            "enum": list(ProposalActionType.values),
                        },
                        "name": {
                            "type": "string",
                            "description": "Folder, tag, project or agent name.",
                        },
                        "description": {
                            "type": "string",
                            "description": "New project description (create_project).",
                        },
                        "file_ids": {"type": "array", "items": {"type": "integer"}},
                        "conversation_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "agent": _AGENT_SCHEMA,
                    },
                    "required": ["type"],
                },
            },
        },
        required=("summary", "actions"),
    )


def start_page_tour_schema() -> Dict:
    return _function(
        START_PAGE_TOUR,
        "Start the guided on-screen tour of a DARE page for the user. Pass "
        "the current page's tour key from the page context unless they ask "
        "about another page, which the app then opens first. The tour starts "
        "when your answer finishes, so reply with one short sentence and do "
        "not describe the tour's steps.",
        {
            "page": {
                "type": "string",
                "enum": list(TOUR_PAGES),
                "description": "Tour key of the page to tour.",
            }
        },
        required=("page",),
    )
