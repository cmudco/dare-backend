"""Model-facing definitions of the assistant's tools (OpenAI format).

Kept free of ORM imports so the DARE tool registry can import them.
"""

from typing import Dict

from assistant.constants import (
    GET_ACCOUNT_OVERVIEW,
    GET_CONVERSATION,
    GET_PROJECT,
    LIST_MY_FILES,
    PLAN_MAX_GROUPS,
    PROPOSE_FILE_ORGANIZATION,
    SEARCH_PLATFORM_DOCS,
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
        "for questions like 'why isn't my file ready?', and with limit=100 "
        "before proposing how to organise files.",
        {
            "status": {
                "type": "string",
                "enum": ["processing", "processed", "failed", "needs_ocr"],
                "description": "Only return files in this status.",
            },
            "name_contains": {
                "type": "string",
                "description": "Only return files whose name contains this text.",
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


def _plan_groups(name_key: str, what: str) -> Dict:
    return {
        "type": "array",
        "maxItems": PLAN_MAX_GROUPS,
        "description": what,
        "items": {
            "type": "object",
            "properties": {
                name_key: {"type": "string"},
                "file_ids": {"type": "array", "items": {"type": "integer"}},
            },
            "required": [name_key, "file_ids"],
        },
    }


def propose_file_organization_schema() -> Dict:
    return _function(
        PROPOSE_FILE_ORGANIZATION,
        "Propose putting the user's files into folders and/or tagging them. "
        "This changes nothing: the plan is shown to the user as a card with "
        "Apply and Discard. Call list_my_files (limit=100) first and use only "
        "its ids. Group files by topic using their names; reuse existing "
        "folder and tag names where they fit. Files are added to folders and "
        "tags, never removed from existing ones. Send one complete plan.",
        {
            "summary": {
                "type": "string",
                "description": "One short sentence describing the plan.",
            },
            "folders": _plan_groups("name", "Folders and the files to put in each."),
            "tags": _plan_groups("label", "Tags and the files to apply each to."),
        },
        required=("summary",),
    )
