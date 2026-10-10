"""Read-only account tools the assistant calls on the user's behalf.

Every query is scoped to the authenticated user passed in by the tool loop;
ids in the model's arguments only select among that user's own records.
"""

from typing import Any, Dict

from django.db.models import Count

from assistant.constants import (
    GET_ACCOUNT_OVERVIEW,
    GET_CONVERSATION,
    GET_PROJECT,
    LIST_CONVERSATIONS_DEFAULT_LIMIT,
    LIST_CONVERSATIONS_MAX_LIMIT,
    LIST_FILES_DEFAULT_LIMIT,
    LIST_FILES_MAX_LIMIT,
    LIST_MY_CONVERSATIONS,
    LIST_MY_FILES,
    LIST_MY_PROJECTS,
    PROPOSE_CHANGES,
    START_PAGE_TOUR,
    TOUR_PAGES,
)
from assistant.services.proposal_service import propose_changes
from billing.constants import UserWalletPreferenceTypeChoice
from billing.models import UserWalletPreference, Wallet
from conversations.constants import ConversationSource, SenderType
from conversations.models import Conversation, Message
from files.constants import FileStatus
from files.models import File
from projects.services.project_service import owned_projects
from users.services.stats_service import get_user_stats

_FILE_STATUS_BY_NAME = {
    "processing": FileStatus.PROCESSING,
    "processed": FileStatus.PROCESSED,
    "failed": FileStatus.FAILED,
    "needs_ocr": FileStatus.NEEDS_OCR,
}


def execute_account_tool(
    tool_name: str, arguments: Dict[str, Any], user
) -> Dict[str, Any]:
    if tool_name == GET_ACCOUNT_OVERVIEW:
        return _account_overview(user)
    if tool_name == LIST_MY_FILES:
        return _list_files(user, arguments)
    if tool_name == GET_CONVERSATION:
        return _conversation(user, arguments)
    if tool_name == GET_PROJECT:
        return _project(user, arguments)
    if tool_name == LIST_MY_PROJECTS:
        return _list_projects(user)
    if tool_name == LIST_MY_CONVERSATIONS:
        return _list_conversations(user, arguments)
    if tool_name == PROPOSE_CHANGES:
        return propose_changes(user, arguments)
    if tool_name == START_PAGE_TOUR:
        return _start_page_tour(arguments)
    return {"success": False, "error": f"Unknown assistant tool: {tool_name}"}


def _start_page_tour(arguments: Dict[str, Any]) -> Dict[str, Any]:
    page = arguments.get("page")
    if page not in TOUR_PAGES:
        return {"success": False, "error": f"No tour exists for page '{page}'."}
    return {"success": True, "page": page}


def _account_overview(user) -> Dict[str, Any]:
    wallet = Wallet.objects.filter(user=user).first()
    preference = UserWalletPreference.objects.filter(user=user).first()
    active_wallet = (
        preference.get_active_wallet_type_display()
        if preference
        else UserWalletPreferenceTypeChoice.DARE.label
    )
    return {
        "success": True,
        "stats": get_user_stats(user),
        "dare_wallet_balance_usd": str(wallet.balance) if wallet else None,
        "chat_billed_to": active_wallet,
    }


def _list_files(user, arguments: Dict[str, Any]) -> Dict[str, Any]:
    files = File.active_objects.filter(user=user, is_media=False)
    status = arguments.get("status") or "all"
    if status != "all":
        if status not in _FILE_STATUS_BY_NAME:
            return {"success": False, "error": f"Unknown status '{status}'."}
        files = files.filter(status=_FILE_STATUS_BY_NAME[status])
    name_contains = (arguments.get("name_contains") or "").strip()
    if name_contains:
        files = files.filter(name__icontains=name_contains)
    total = files.count()
    try:
        limit = int(arguments.get("limit") or LIST_FILES_DEFAULT_LIMIT)
    except (TypeError, ValueError):
        limit = LIST_FILES_DEFAULT_LIMIT
    limit = max(1, min(limit, LIST_FILES_MAX_LIMIT))
    rows = files.prefetch_related("tags", "folders").order_by("-created_at")[:limit]
    return {
        "success": True,
        "total_matching": total,
        "files": [
            {
                "id": file.id,
                "name": file.name,
                "status": file.get_status_display(),
                "stage": file.get_processing_stage_display(),
                "error": file.error_message or None,
                "tags": [tag.label for tag in file.tags.all()],
                "folders": [folder.name for folder in file.folders.all()],
                "uploaded_at": file.created_at.date().isoformat(),
            }
            for file in rows
        ],
    }


def _list_projects(user) -> Dict[str, Any]:
    projects = owned_projects(user).annotate(
        file_count=Count("files", distinct=True),
        folder_count=Count("folders", distinct=True),
    )
    return {
        "success": True,
        "projects": [
            {
                "id": project.id,
                "name": project.name,
                "description": project.description,
                "chats": project.conversation_count,
                "files": project.file_count,
                "folders": project.folder_count,
            }
            for project in projects
        ],
    }


def _list_conversations(user, arguments: Dict[str, Any]) -> Dict[str, Any]:
    chats = Conversation.active_objects.filter(
        user=user, source=ConversationSource.DARE
    )
    project = str(arguments.get("project") or "").strip().lower()
    if project == "none":
        chats = chats.filter(project=None)
    elif project:
        if not project.isdigit():
            return {"success": False, "error": "project must be 'none' or an id."}
        chats = chats.filter(project_id=int(project))
    title_contains = (arguments.get("title_contains") or "").strip()
    if title_contains:
        chats = chats.filter(title__icontains=title_contains)
    total = chats.count()
    try:
        limit = int(arguments.get("limit") or LIST_CONVERSATIONS_DEFAULT_LIMIT)
    except (TypeError, ValueError):
        limit = LIST_CONVERSATIONS_DEFAULT_LIMIT
    limit = max(1, min(limit, LIST_CONVERSATIONS_MAX_LIMIT))
    rows = chats.select_related("project").order_by("-updated_at")[:limit]
    return {
        "success": True,
        "total_matching": total,
        "conversations": [
            {
                "id": chat.conversation_id,
                "title": chat.title,
                "project": chat.project.name if chat.project else None,
                "updated_at": chat.updated_at.date().isoformat(),
            }
            for chat in rows
        ],
    }


def _conversation(user, arguments: Dict[str, Any]) -> Dict[str, Any]:
    conversation = (
        Conversation.active_objects.filter(
            user=user, conversation_id=str(arguments.get("conversation_id", ""))
        )
        .select_related("selected_model", "prompt", "project")
        .first()
    )
    if conversation is None:
        return {"success": False, "error": "Conversation not found."}
    enabled_tools = [
        label
        for label, enabled in (
            ("web search", conversation.web_search_enabled),
            ("web fetch", conversation.web_fetch_enabled),
            ("image generation", conversation.image_generation_enabled),
            ("audio transcription", conversation.audio_transcription_enabled),
            ("artifacts", conversation.artifacts_enabled),
            ("memory", conversation.memory_enabled),
        )
        if enabled
    ]
    return {
        "success": True,
        "title": conversation.title,
        "model": (
            conversation.selected_model.name if conversation.selected_model else None
        ),
        "latest_reply_model": _latest_reply_model(conversation),
        "rag_mode": conversation.get_rag_mode_display(),
        "saved_prompt": conversation.prompt.title if conversation.prompt else None,
        "project": conversation.project.name if conversation.project else None,
        "searchable_files": len(conversation.selected_embedding_ids or []),
        "full_text_files": len(conversation.selected_file_ids or []),
        "shared_libraries": len(conversation.selected_library_ids or []),
        "enabled_tools": enabled_tools,
        "message_count": Message.active_objects.filter(
            conversation=conversation
        ).count(),
        "created_at": conversation.created_at.isoformat(),
    }


def _latest_reply_model(conversation):
    """The model that wrote the newest AI reply; chats need not pin a model."""
    reply = (
        Message.active_objects.filter(
            conversation=conversation, sender_type=SenderType.AI_ASSISTANT
        )
        .exclude(llm=None, litellm_model_name=None)
        .select_related("llm")
        .order_by("-created_at")
        .first()
    )
    if reply is None:
        return None
    return reply.llm.name if reply.llm else reply.litellm_model_name


def _project(user, arguments: Dict[str, Any]) -> Dict[str, Any]:
    try:
        project_id = int(arguments.get("project_id"))
    except (TypeError, ValueError):
        return {"success": False, "error": "project_id must be an integer."}
    project = owned_projects(user).filter(pk=project_id).first()
    if project is None:
        return {"success": False, "error": "Project not found."}
    return {
        "success": True,
        "name": project.name,
        "description": project.description,
        "instructions_prompt": project.prompt.title if project.prompt else None,
        "default_model": project.default_model.name if project.default_model else None,
        "files": project.files.count(),
        "folders": project.folders.count(),
        "libraries": project.libraries.count(),
        "conversations": project.conversation_count,
    }
