"""Use cases for personal projects: listing, new-chat defaults, deletion, memory scope."""

from dataclasses import dataclass
from typing import Optional, Tuple

from django.db import transaction
from django.db.models import Count, Max, Q, QuerySet
from django.db.models.functions import Coalesce, Greatest
from django.utils import timezone

from conversations.models import Conversation
from files.models import File
from libraries.models import UserLibraryAccess
from projects.constants import ProjectMemoryScope
from projects.models import PersonalProject
from prompts.services.prompt_service import PromptService, PromptServiceError

_LIVE_CHATS = Q(conversations__is_active=True, conversations__is_deleted=False)


@dataclass(frozen=True)
class ProjectChatSources:
    embedding_ids: Tuple[int, ...]
    library_ids: Tuple[int, ...]


def owned_projects(user) -> QuerySet:
    """The user's projects with chat counts, most recently active first."""
    return (
        PersonalProject.active_objects.filter(user=user)
        .annotate(
            conversation_count=Count("conversations", filter=_LIVE_CHATS),
            last_activity_at=Greatest(
                "updated_at",
                Coalesce(
                    Max("conversations__created_at", filter=_LIVE_CHATS),
                    "updated_at",
                ),
            ),
        )
        .select_related("prompt")
        .prefetch_related("files", "folders", "libraries", "workflows")
        .order_by("-last_activity_at", "-id")
    )


def resolve_chat_sources(project: PersonalProject) -> ProjectChatSources:
    """Expand the project's files, folders and libraries into chat selections.

    Folders are expanded at chat creation, so files added to a folder later
    reach new chats but never rewrite an existing chat's selection.
    """
    files = File.active_objects.filter(user=project.user, is_media=False).filter(
        Q(personal_projects=project) | Q(folders__personal_projects=project)
    )
    embedding_ids = tuple(files.order_by("id").values_list("id", flat=True).distinct())
    library_ids = tuple(
        UserLibraryAccess.active_objects.filter(
            user=project.user, library__personal_projects=project
        )
        .order_by("library_id")
        .values_list("library_id", flat=True)
        .distinct()
    )
    return ProjectChatSources(embedding_ids=embedding_ids, library_ids=library_ids)


def start_chat_in_project(conversation: Conversation) -> None:
    """Copy the project's defaults onto a freshly created chat.

    Runs after the user's default prompt is applied, so a project prompt wins.
    """
    project = conversation.project
    sources = resolve_chat_sources(project)
    conversation.selected_embedding_ids = list(sources.embedding_ids)
    conversation.selected_library_ids = list(sources.library_ids)
    if project.prompt_id:
        conversation.prompt_id = project.prompt_id
    if project.default_model_id:
        conversation.selected_model_id = project.default_model_id
    conversation.web_search_enabled = project.web_search_enabled
    conversation.artifacts_enabled = project.artifacts_enabled
    conversation.memory_enabled = project.memory_enabled
    conversation.save(
        update_fields=[
            "selected_embedding_ids",
            "selected_library_ids",
            "prompt",
            "selected_model",
            "web_search_enabled",
            "artifacts_enabled",
            "memory_enabled",
            "updated_at",
        ]
    )
    touch_project(project)


def set_project_instructions(project: PersonalProject, instructions: str) -> None:
    """Store instructions as the project's prompt, reusing the Prompts library.

    Editing a linked prompt saves its next version, the same as editing it on
    the Prompts page; an older version the user has since moved past gets a
    fresh prompt instead, because only the latest version can be versioned.
    """
    text = instructions.strip()
    current = project.prompt
    if current is not None and current.content.strip() == text:
        return
    if not text:
        project.prompt = None
    elif current is None:
        project.prompt = PromptService.create_prompt(
            {"title": f"{project.name} instructions", "content": text},
            project.user,
            is_default=False,
        )
    else:
        try:
            project.prompt = PromptService.create_next_version(
                current,
                {"content": text},
                project.user,
                # Versioning the user's default prompt must keep it the default.
                is_default=project.user.default_prompt_id == current.pk,
            )
        except PromptServiceError:
            project.prompt = PromptService.create_prompt(
                {"title": current.title, "content": text},
                project.user,
                is_default=False,
            )
    project.save(update_fields=["prompt", "updated_at"])


def touch_project(project: PersonalProject) -> None:
    PersonalProject.objects.filter(pk=project.pk).update(updated_at=timezone.now())


def delete_project(project: PersonalProject, *, delete_conversations: bool) -> int:
    """Delete a project; its chats are deleted too or return to the main list.

    Returns how many chats were deleted or released.
    """
    with transaction.atomic():
        chats = Conversation.active_objects.filter(project=project)
        affected = chats.count()
        if delete_conversations:
            for conversation in chats:
                conversation.delete()
        project.delete()
    return affected


def memory_scope_project_id(project_id: Optional[int]) -> Optional[int]:
    """The project whose chats bound memory recall, or None for all memories."""
    if project_id is None:
        return None
    scoped = PersonalProject.active_objects.filter(
        pk=project_id, memory_scope=ProjectMemoryScope.PROJECT
    ).exists()
    return project_id if scoped else None
