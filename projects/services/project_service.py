"""Use cases for personal projects: listing, new-chat defaults, deletion, memory scope."""

import math
from dataclasses import dataclass
from typing import Iterable, Optional, Tuple

from django.db import transaction
from django.db.models import Count, F, IntegerField, Max, Q, QuerySet, Sum
from django.db.models.functions import Coalesce, Greatest, Length
from django.utils import timezone

from conversations.models import Conversation
from core.services.model_ref import model_ref
from files.models import File
from libraries.models import UserLibraryAccess
from projects.constants import (
    FULL_FILES_TOKEN_LIMIT,
    ProjectMemoryScope,
    ProjectSourceMode,
)
from projects.models import PersonalProject
from prompts.services.prompt_service import PromptService, PromptServiceError

_LIVE_CHATS = Q(conversations__is_active=True, conversations__is_deleted=False)


@dataclass(frozen=True)
class ProjectChatSources:
    file_ids: Tuple[int, ...]
    library_ids: Tuple[int, ...]


@dataclass(frozen=True)
class SourceSize:
    """Rough prompt cost of reading a set of files in full."""

    file_count: int
    estimated_tokens: int

    @property
    def fits_full_files(self) -> bool:
        return self.estimated_tokens <= FULL_FILES_TOKEN_LIMIT


def project_model_ref(project: PersonalProject) -> Optional[str]:
    """Picker id of the project's default model, in the same form chats use."""
    return model_ref(
        project.default_model_id,
        project.default_litellm_key_id,
        project.default_litellm_model_name,
    )


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


def _document_files(user) -> QuerySet:
    return File.active_objects.filter(user=user, is_media=False)


def estimate_source_size(files: QuerySet) -> SourceSize:
    """Estimate tokens at four characters each from the cached extraction.

    A file not parsed yet counts its byte size, which overstates the text, so
    an unparsed file can push a set into search but never sneak it past.
    """
    totals = files.aggregate(
        file_count=Count("id", distinct=True),
        chars=Sum(
            Coalesce(Length("extracted_text"), F("size"), output_field=IntegerField())
        ),
    )
    return SourceSize(
        file_count=totals["file_count"],
        estimated_tokens=math.ceil((totals["chars"] or 0) / 4),
    )


def selected_source_files(
    user, file_ids: Iterable[int], folder_ids: Iterable[int]
) -> QuerySet:
    """The user's documents picked directly or through the given folders."""
    ids = _document_files(user).filter(
        Q(id__in=list(file_ids))
        | Q(folders__id__in=list(folder_ids), folders__user=user)
    )
    return _document_files(user).filter(id__in=ids.values("id"))


def resolve_chat_sources(project: PersonalProject) -> ProjectChatSources:
    """Expand the project's files, folders and libraries into chat selections.

    Folders are expanded at chat creation, so files added to a folder later
    reach new chats but never rewrite an existing chat's selection.
    """
    files = _document_files(project.user).filter(
        Q(personal_projects=project) | Q(folders__personal_projects=project)
    )
    file_ids = tuple(files.order_by("id").values_list("id", flat=True).distinct())
    library_ids = tuple(
        UserLibraryAccess.active_objects.filter(
            user=project.user, library__personal_projects=project
        )
        .order_by("library_id")
        .values_list("library_id", flat=True)
        .distinct()
    )
    return ProjectChatSources(file_ids=file_ids, library_ids=library_ids)


def chat_reads_full_files(project: PersonalProject, file_ids: Tuple[int, ...]) -> bool:
    """Full files only while they fit; folders that grew past the limit search."""
    if project.source_mode != ProjectSourceMode.FULL or not file_ids:
        return False
    files = _document_files(project.user).filter(id__in=file_ids)
    return estimate_source_size(files).fits_full_files


def start_chat_in_project(conversation: Conversation) -> None:
    """Copy the project's defaults onto a freshly created chat.

    Runs after the user's default prompt is applied, so a project prompt wins.
    """
    project = conversation.project
    sources = resolve_chat_sources(project)
    if chat_reads_full_files(project, sources.file_ids):
        conversation.selected_file_ids = list(sources.file_ids)
        conversation.selected_embedding_ids = []
    else:
        conversation.selected_file_ids = []
        conversation.selected_embedding_ids = list(sources.file_ids)
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
            "selected_file_ids",
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
